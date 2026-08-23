if(CONFIG_ZMK_BEHAVIOR_STATUS_REPORT)
  zephyr_library_named(scylla_behaviors)
  zephyr_library_sources(src/behavior_status_report.c)
endif()
