cmake_minimum_required(VERSION 3.22)
project(monado_win32_hid_contract C)
if(NOT WIN32)
    message(FATAL_ERROR "This probe tests the Windows HID adapter only")
endif()
set(MONADO_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../providers/mercury-research/monado")
add_executable(test_win32_hid_contract
    test_win32_hid_contract.c
    "${MONADO_SOURCE}/src/xrt/auxiliary/os/os_hid_win32.c"
)
target_include_directories(test_win32_hid_contract PRIVATE
    "${MONADO_SOURCE}/src/xrt/auxiliary"
    "${MONADO_SOURCE}/src/xrt/state_trackers/prober"
    "${MONADO_SOURCE}/src/xrt/include"
)
target_link_libraries(test_win32_hid_contract PRIVATE hid setupapi)
add_executable(test_win32_hid_timeout test_win32_hid_timeout.c)
target_include_directories(test_win32_hid_timeout PRIVATE
    "${MONADO_SOURCE}/src/xrt/auxiliary"
    "${MONADO_SOURCE}/src/xrt/include"
)
target_link_libraries(test_win32_hid_timeout PRIVATE hid setupapi)
add_executable(index_watchman_caps_probe
    index_watchman_caps_probe.c
    "${MONADO_SOURCE}/src/xrt/auxiliary/os/os_hid_win32.c"
)
target_include_directories(index_watchman_caps_probe PRIVATE
    "${MONADO_SOURCE}/src/xrt/auxiliary"
    "${MONADO_SOURCE}/src/xrt/include"
)
target_link_libraries(index_watchman_caps_probe PRIVATE hid setupapi)
add_executable(index_config_start_probe
    index_config_start_probe.c
    "${MONADO_SOURCE}/src/xrt/auxiliary/os/os_hid_win32.c"
)
target_include_directories(index_config_start_probe PRIVATE
    "${MONADO_SOURCE}/src/xrt/auxiliary"
    "${MONADO_SOURCE}/src/xrt/include"
)
target_link_libraries(index_config_start_probe PRIVATE hid setupapi)
add_executable(test_index_extended_target test_index_extended_target.c)
target_include_directories(test_index_extended_target PRIVATE
    "${MONADO_SOURCE}/src/xrt/compositor"
)
enable_testing()
add_test(NAME win32_hid_contract COMMAND test_win32_hid_contract)
add_test(NAME win32_hid_timeout COMMAND test_win32_hid_timeout)
add_test(NAME index_extended_target COMMAND test_index_extended_target)
add_test(NAME index_config_start_guard COMMAND index_config_start_probe)
set_tests_properties(index_config_start_guard PROPERTIES WILL_FAIL TRUE)
