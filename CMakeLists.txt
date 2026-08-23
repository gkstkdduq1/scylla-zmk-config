# Compile into the `app` target rather than a separate library: that is how ZMK
# behavior modules get ZMK's own include paths (drivers/behavior.h and friends
# live in zmk/app/include, which is not exposed to standalone module libraries).
#
# Central only. The peripheral half never runs the keymap, and the endpoint and
# BLE profile APIs this behavior reads do not exist in a peripheral build.
if((NOT CONFIG_ZMK_SPLIT) OR CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
  target_sources_ifdef(CONFIG_ZMK_BEHAVIOR_STATUS_REPORT app PRIVATE src/behavior_status_report.c)
endif()
