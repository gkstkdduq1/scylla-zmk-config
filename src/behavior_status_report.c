/*
 * Types the keyboard's connection status as text.
 *
 * SPDX-License-Identifier: MIT
 *
 * ZMK's Studio RPC has only three subsystems - core, behaviors, keymap - and no
 * notification carrying endpoint or profile state. So a host application cannot
 * ask the keyboard which output it is using or which BLE profile is live, which
 * is why nothing on the desktop can show it.
 *
 * This behavior sidesteps that by answering over the channel the keyboard
 * always has: the keystrokes it is already sending. Press the key and it types
 * something like
 *
 *     out=ble prof=2 conn=1 batt=96/100
 *
 * Fields:
 *   out    endpoint actually in use, which is not always the preferred one.
 *          A trailing "?pref" appears when the preferred endpoint differs -
 *          e.g. "out=ble?usb" means USB is preferred but not connected.
 *   prof   active BLE profile index.
 *   conn   1 if that profile has a live connection, 0 if it is only bonded.
 *   batt   this half, then the peripheral. "-" when a level is not known yet.
 *
 * Only lowercase letters, digits and unshifted punctuation are emitted, so the
 * output survives any keyboard layout that keeps ASCII in the usual places.
 * A Hangul/Kana IME will still transliterate the letters - switch to English
 * before pressing, or let the companion app read it (it decodes raw scancodes,
 * so the IME state does not matter there).
 */

#define DT_DRV_COMPAT zmk_behavior_status_report

#include <stdio.h>

#include <zephyr/device.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <drivers/behavior.h>

#include <zmk/behavior.h>
#include <zmk/endpoints.h>
#include <zmk/endpoints_types.h>
#include <zmk/event_manager.h>
#include <zmk/events/keycode_state_changed.h>
#include <zmk/hid.h>
#include <zmk/keys.h>

#include <dt-bindings/zmk/hid_usage.h>
#include <dt-bindings/zmk/hid_usage_pages.h>
#include <dt-bindings/zmk/modifiers.h>

#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <zmk/ble.h>
#endif

#if IS_ENABLED(CONFIG_ZMK_BATTERY_REPORTING)
#include <zmk/battery.h>
#include <zmk/events/battery_state_changed.h>
#endif

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#define MSG_MAX 96

struct behavior_status_report_config {
    uint16_t tap_ms;
    uint16_t wait_ms;
};

/* -- peripheral battery cache -------------------------------------------- */

/*
 * The central has no getter for a peripheral's charge; it only learns about it
 * through events as they arrive, so keep the last value we saw.
 */
#if IS_ENABLED(CONFIG_ZMK_BATTERY_REPORTING) && IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
#define TRACK_PERIPHERAL_BATTERY 1
static uint8_t peripheral_soc[4];
static bool peripheral_soc_valid[4];

static int status_report_battery_listener(const zmk_event_t *eh) {
    const struct zmk_peripheral_battery_state_changed *ev =
        as_zmk_peripheral_battery_state_changed(eh);
    if (ev && ev->source < ARRAY_SIZE(peripheral_soc)) {
        peripheral_soc[ev->source] = ev->state_of_charge;
        peripheral_soc_valid[ev->source] = true;
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(status_report_battery, status_report_battery_listener);
ZMK_SUBSCRIPTION(status_report_battery, zmk_peripheral_battery_state_changed);
#endif

/* -- ascii -> keycode ------------------------------------------------------ */

/*
 * Returns the encoded keycode for a character, or 0 if we cannot type it.
 * Encoding matches ZMK's own: (implicit mods << 24) | (usage page << 16) | id.
 */
static uint32_t encode_char(char c) {
    uint16_t id = 0;
    if (c >= 'a' && c <= 'z') {
        id = HID_USAGE_KEY_KEYBOARD_A + (c - 'a');
    } else if (c >= '1' && c <= '9') {
        id = HID_USAGE_KEY_KEYBOARD_1_AND_EXCLAMATION + (c - '1');
    } else if (c == '0') {
        id = HID_USAGE_KEY_KEYBOARD_0_AND_RIGHT_PARENTHESIS;
    } else if (c == ' ') {
        id = HID_USAGE_KEY_KEYBOARD_SPACEBAR;
    } else if (c == '=') {
        id = HID_USAGE_KEY_KEYBOARD_EQUAL_AND_PLUS;
    } else if (c == '-') {
        id = HID_USAGE_KEY_KEYBOARD_MINUS_AND_UNDERSCORE;
    } else if (c == '/') {
        id = HID_USAGE_KEY_KEYBOARD_SLASH_AND_QUESTION_MARK;
    } else if (c == '.') {
        id = HID_USAGE_KEY_KEYBOARD_PERIOD_AND_GREATER_THAN;
    } else if (c == ',') {
        id = HID_USAGE_KEY_KEYBOARD_COMMA_AND_LESS_THAN;
    } else if (c == '?') {
        /* Only shifted character used; '/' with left shift. */
        return ((uint32_t)MOD_LSFT << 24) | (HID_USAGE_KEY << 16) |
               HID_USAGE_KEY_KEYBOARD_SLASH_AND_QUESTION_MARK;
    } else {
        return 0;
    }
    return ((uint32_t)HID_USAGE_KEY << 16) | id;
}

/* -- typing thread --------------------------------------------------------- */

static K_SEM_DEFINE(type_sem, 0, 1);
static atomic_t typing_busy = ATOMIC_INIT(0);
static char pending_msg[MSG_MAX];
static uint16_t pending_tap_ms = 6;
static uint16_t pending_wait_ms = 6;

static void type_message(const char *msg, uint16_t tap_ms, uint16_t wait_ms) {
    for (const char *p = msg; *p; p++) {
        uint32_t code = encode_char(*p);
        if (code == 0) {
            continue;
        }
        raise_zmk_keycode_state_changed_from_encoded(code, true, k_uptime_get());
        k_sleep(K_MSEC(tap_ms));
        raise_zmk_keycode_state_changed_from_encoded(code, false, k_uptime_get());
        k_sleep(K_MSEC(wait_ms));
    }
}

static void status_report_thread(void *a, void *b, void *c) {
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);
    for (;;) {
        k_sem_take(&type_sem, K_FOREVER);
        /* Let the key that triggered us finish releasing first. */
        k_sleep(K_MSEC(40));
        type_message(pending_msg, pending_tap_ms, pending_wait_ms);
        atomic_clear(&typing_busy);
    }
}

K_THREAD_DEFINE(status_report_tid, CONFIG_ZMK_BEHAVIOR_STATUS_REPORT_STACK_SIZE,
                status_report_thread, NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO, 0, 0);

/* -- building the message -------------------------------------------------- */

static const char *transport_name(enum zmk_transport t) {
    switch (t) {
    case ZMK_TRANSPORT_USB:
        return "usb";
    case ZMK_TRANSPORT_BLE:
        return "ble";
    default:
        return "none";
    }
}

static void build_message(char *out, size_t len) {
    struct zmk_endpoint_instance selected = zmk_endpoint_get_selected();
    struct zmk_endpoint_instance preferred = zmk_endpoint_get_preferred();

    int n = snprintf(out, len, "out=%s", transport_name(selected.transport));

    if (preferred.transport != selected.transport && n > 0 && n < (int)len) {
        n += snprintf(out + n, len - n, "?%s", transport_name(preferred.transport));
    }

#if IS_ENABLED(CONFIG_ZMK_BLE)
    if (n > 0 && n < (int)len) {
        int profile = zmk_ble_active_profile_index();
        n += snprintf(out + n, len - n, " prof=%d conn=%d", profile,
                      zmk_ble_active_profile_is_connected() ? 1 : 0);
    }
#endif

#if IS_ENABLED(CONFIG_ZMK_BATTERY_REPORTING)
    if (n > 0 && n < (int)len) {
        n += snprintf(out + n, len - n, " batt=%d", zmk_battery_state_of_charge());
    }
#ifdef TRACK_PERIPHERAL_BATTERY
    for (int i = 0; i < (int)ARRAY_SIZE(peripheral_soc); i++) {
        if (n <= 0 || n >= (int)len) {
            break;
        }
        if (peripheral_soc_valid[i]) {
            n += snprintf(out + n, len - n, "/%d", peripheral_soc[i]);
        }
    }
#endif
#endif
}

/* -- behavior -------------------------------------------------------------- */

static int on_keymap_binding_pressed(struct zmk_behavior_binding *binding,
                                     struct zmk_behavior_binding_event event) {
    const struct device *dev = zmk_behavior_get_binding(binding->behavior_dev);
    const struct behavior_status_report_config *cfg = dev->config;

    /* Still typing the previous report - ignore rather than interleave. */
    if (!atomic_cas(&typing_busy, 0, 1)) {
        return ZMK_BEHAVIOR_OPAQUE;
    }

    build_message(pending_msg, sizeof(pending_msg));
    pending_tap_ms = cfg->tap_ms;
    pending_wait_ms = cfg->wait_ms;
    LOG_DBG("status report: %s", pending_msg);
    k_sem_give(&type_sem);

    return ZMK_BEHAVIOR_OPAQUE;
}

static int on_keymap_binding_released(struct zmk_behavior_binding *binding,
                                      struct zmk_behavior_binding_event event) {
    ARG_UNUSED(binding);
    ARG_UNUSED(event);
    return ZMK_BEHAVIOR_OPAQUE;
}

static const struct behavior_driver_api behavior_status_report_driver_api = {
    .binding_pressed = on_keymap_binding_pressed,
    .binding_released = on_keymap_binding_released,
};

#define SR_INST(n)                                                                                 \
    static const struct behavior_status_report_config behavior_status_report_config_##n = {        \
        .tap_ms = DT_INST_PROP(n, tap_ms),                                                         \
        .wait_ms = DT_INST_PROP(n, wait_ms),                                                       \
    };                                                                                             \
    BEHAVIOR_DT_INST_DEFINE(n, NULL, NULL, NULL, &behavior_status_report_config_##n, POST_KERNEL,  \
                            CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,                                   \
                            &behavior_status_report_driver_api);

DT_INST_FOREACH_STATUS_OKAY(SR_INST)
