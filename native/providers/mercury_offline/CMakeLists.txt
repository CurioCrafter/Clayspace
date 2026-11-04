cmake_minimum_required(VERSION 3.21)
cmake_policy(SET CMP0077 NEW)

project(mercury_offline_selftest LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(MERCURY_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/../mercury-research"
    CACHE PATH "Checked-out Mercury source tree")
set(MERCURY_MODEL_DIR "${MERCURY_SOURCE_DIR}/hand-tracking-models"
    CACHE PATH "Private Monado Mercury model checkout")
set(ONNXRuntime_ROOT_DIR
    "${CMAKE_CURRENT_LIST_DIR}/third_party/onnxruntime-win-x64-1.13.1"
    CACHE PATH "Workspace-local ONNX Runtime development archive")
set(OpenCV_DIR
    "${CMAKE_CURRENT_LIST_DIR}/third_party/opencv/build/x64/vc16/lib"
    CACHE PATH "Workspace-local OpenCV development archive")
set(EIGEN3_INCLUDE_DIR
    "${CMAKE_CURRENT_LIST_DIR}/third_party/eigen-3.4.0"
    CACHE PATH "Workspace-local Eigen headers" FORCE)

if(NOT EXISTS "${MERCURY_SOURCE_DIR}/monado/CMakeLists.txt")
  message(FATAL_ERROR "MERCURY_SOURCE_DIR must contain the initialized Monado submodule")
endif()
if(NOT EXISTS "${MERCURY_MODEL_DIR}/grayscale_detection_160x160.onnx")
  message(FATAL_ERROR "MERCURY_MODEL_DIR is missing the private evaluation models")
endif()
set(OPENVR_CAPI_VENDOR_DIR "${CMAKE_CURRENT_LIST_DIR}/../openvr-camera-probe/vendor")
if(NOT EXISTS "${OPENVR_CAPI_VENDOR_DIR}/openvr_capi.h")
  message(FATAL_ERROR "The pinned public OpenVR C API vendor header is required for calibration-only types")
endif()

# Configure only Monado utility/tracker libraries. The full OpenXR runtime,
# every hardware driver, and SteamVR integration remain disabled.
list(APPEND CMAKE_MODULE_PATH "${MERCURY_SOURCE_DIR}/monado/cmake")
list(APPEND CMAKE_MODULE_PATH "${MERCURY_SOURCE_DIR}/monado/cmake/sanitizers")

set(MONADO_SRC_XRT "${MERCURY_SOURCE_DIR}/monado/src/xrt")
set(XRT_FEATURE_OPENXR OFF)
set(XRT_FEATURE_IPC OFF)
set(XRT_FEATURE_SERVICE OFF)
set(XRT_FEATURE_SERVICE_SYSTEMD OFF)
set(XRT_FEATURE_STEAMVR_PLUGIN OFF)
set(XRT_FEATURE_TRACING OFF)
set(XRT_FEATURE_SLAM OFF)
set(XRT_FEATURE_COMPOSITOR_MAIN OFF)
set(XRT_FEATURE_COMPOSITOR_NULL OFF)
set(XRT_MODULE_IPC OFF)
set(XRT_MODULE_COMPOSITOR OFF)
set(XRT_MODULE_MONADO_CLI OFF)
set(XRT_MODULE_MONADO_GUI OFF)
set(XRT_MODULE_AUX_VIVE OFF)
set(XRT_MODULE_MERCURY_HANDTRACKING ON)
set(XRT_BUILD_SAMPLES OFF)
set(BUILD_TESTING OFF)
set(BUILD_DOC OFF)

foreach(driver
    ANDROID ARDUINO DAYDREAM DEPTHAI EUROC HANDTRACKING TWRAP HDK HYDRA
    ILLIXR NS OHMD OPENGLOVES PSMV PSVR QWERTY REALSENSE REMOTE RIFT_S
    SURVIVE ULV2 VF VIVE WMR SIMULATED)
  set("XRT_BUILD_DRIVER_${driver}" OFF)
endforeach()

# Monado's older CMake code uses option() in a nested project scope. Persist
# the intended values in the cache as well, otherwise its default hardware and
# compositor targets can be silently re-enabled during configuration.
foreach(option
    XRT_FEATURE_OPENXR XRT_FEATURE_IPC XRT_FEATURE_SERVICE
    XRT_FEATURE_SERVICE_SYSTEMD XRT_FEATURE_STEAMVR_PLUGIN XRT_FEATURE_TRACING
    XRT_FEATURE_SLAM XRT_FEATURE_COMPOSITOR_MAIN XRT_FEATURE_COMPOSITOR_NULL
    XRT_FEATURE_CLIENT_DEBUG_GUI XRT_FEATURE_RENDERDOC
    XRT_FEATURE_OPENXR_LAYER_CUBE XRT_FEATURE_OPENXR_LAYER_CYLINDER
    XRT_FEATURE_OPENXR_LAYER_DEPTH XRT_FEATURE_OPENXR_LAYER_EQUIRECT1
    XRT_FEATURE_OPENXR_LAYER_EQUIRECT2 XRT_MODULE_IPC
    XRT_MODULE_COMPOSITOR XRT_MODULE_MONADO_CLI XRT_MODULE_MONADO_GUI
    XRT_MODULE_AUX_VIVE XRT_BUILD_SAMPLES BUILD_TESTING BUILD_DOC)
  set("${option}" OFF CACHE BOOL "Offline Mercury evaluation" FORCE)
endforeach()
foreach(driver
    ANDROID ARDUINO DAYDREAM DEPTHAI EUROC HANDTRACKING TWRAP HDK HYDRA
    ILLIXR NS OHMD OPENGLOVES PSMV PSVR QWERTY REALSENSE REMOTE RIFT_S
    SURVIVE ULV2 VF VIVE WMR SIMULATED)
  set("XRT_BUILD_DRIVER_${driver}" OFF CACHE BOOL "Offline Mercury evaluation" FORCE)
endforeach()
set(XRT_MODULE_MERCURY_HANDTRACKING ON CACHE BOOL "Offline Mercury evaluation" FORCE)

find_package(OpenCV REQUIRED CONFIG COMPONENTS core imgproc imgcodecs highgui)
add_subdirectory("${MERCURY_SOURCE_DIR}/monado" "${CMAKE_BINARY_DIR}/monado")

# Monado's aux_tracking library is intentionally broad: when OpenCV is found it
# also adds PS Move / PSVR camera-tracker implementation files. Mercury does not
# reference those trackers, and compiling them would make this camera-free target
# needlessly platform-sensitive. Keep the shared calibration/frame utilities, but
# remove the unused hardware tracker sources from this isolated evaluation target.
get_property(AUX_TRACKING_SOURCES TARGET aux_tracking PROPERTY SOURCES)
list(REMOVE_ITEM AUX_TRACKING_SOURCES
  t_tracker_psmv_fusion.hpp
  t_tracker_psmv.cpp
  t_tracker_psvr.cpp)
set_property(TARGET aux_tracking PROPERTY SOURCES "${AUX_TRACKING_SOURCES}")

if(MSVC)
  # Eigen-heavy calibration code exceeds the default COFF section limit in a
  # Debug build. This is a compiler-format setting, not a source change.
  target_compile_options(aux_tracking PRIVATE /bigobj)
endif()

add_executable(mercury_offline_selftest
  mercury_offline_selftest.cpp
  openvr_mercury_bridge.cpp)
add_executable(mercury_openvr_camera_eval
  mercury_openvr_camera_eval.cpp
  openvr_mercury_bridge.cpp)
add_executable(mercury_direct_camera_eval
  mercury_direct_camera_eval.cpp
  palm_detector.cpp
  hand_landmark_probe.cpp)
enable_testing()
add_test(NAME mercury_stereo_packet_serialization
  COMMAND mercury_direct_camera_eval --packet-self-test)
add_test(NAME mercury_latest_frame_reader
  COMMAND mercury_direct_camera_eval --latest-reader-self-test)
add_test(NAME mercury_legacy_freshness_boundary
  COMMAND mercury_direct_camera_eval --legacy-freshness-self-test)
add_test(NAME mercury_parallel_eye_equivalence
  COMMAND mercury_direct_camera_eval --parallel-eyes-self-test)
if(WIN32)
  find_program(MERCURY_POWERSHELL_EXECUTABLE powershell REQUIRED)
  add_test(NAME mercury_legacy_rgba_protocol
    COMMAND "${MERCURY_POWERSHELL_EXECUTABLE}" -NoProfile -ExecutionPolicy Bypass
      -File "${CMAKE_CURRENT_LIST_DIR}/test_legacy_rgba_stream.ps1"
      -Exe "$<TARGET_FILE:mercury_direct_camera_eval>")
endif()
add_executable(mercury_palm_model_selftest palm_model_selftest.cpp palm_detector.cpp hand_landmark_probe.cpp)
find_package(ONNXRuntime REQUIRED)
target_link_libraries(mercury_direct_camera_eval PRIVATE ONNXRuntime::ONNXRuntime ${OpenCV_LIBS})
target_compile_definitions(mercury_palm_model_selftest PRIVATE MERCURY_MODEL_DIR="${MERCURY_MODEL_DIR}")
target_link_libraries(mercury_palm_model_selftest PRIVATE ONNXRuntime::ONNXRuntime ${OpenCV_LIBS})

foreach(offline_target mercury_offline_selftest mercury_openvr_camera_eval mercury_direct_camera_eval)
  target_compile_definitions(${offline_target}
    PRIVATE MERCURY_MODEL_DIR="${MERCURY_MODEL_DIR}")
  target_include_directories(${offline_target} PRIVATE
    "${MERCURY_SOURCE_DIR}/monado/src/xrt/include"
    "${MERCURY_SOURCE_DIR}/monado/src/xrt/auxiliary"
    "${MERCURY_SOURCE_DIR}/monado/src/xrt/tracking/hand/mercury"
    "${OPENVR_CAPI_VENDOR_DIR}"
    "${EIGEN3_INCLUDE_DIR}"
    "${ONNXRuntime_ROOT_DIR}/include")
  target_link_libraries(${offline_target} PRIVATE
    t_ht_mercury
    aux_tracking
    aux_util)
endforeach()

if(WIN32)
  set(MERCURY_OPENCV_BIN_DIR
      "${CMAKE_CURRENT_LIST_DIR}/third_party/opencv/build/x64/vc16/bin")
  foreach(offline_target mercury_offline_selftest mercury_openvr_camera_eval mercury_direct_camera_eval)
    add_custom_command(TARGET ${offline_target} POST_BUILD
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${ONNXRuntime_ROOT_DIR}/lib/onnxruntime.dll"
        "$<TARGET_FILE_DIR:${offline_target}>/onnxruntime.dll"
      # The pinned OpenCV Windows archive uses this documented world-DLL name.
      # Copy it beside the private evaluator so validation does not alter PATH.
      COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${MERCURY_OPENCV_BIN_DIR}/opencv_world4100$<$<CONFIG:Debug>:d>.dll"
        "$<TARGET_FILE_DIR:${offline_target}>/opencv_world4100$<$<CONFIG:Debug>:d>.dll")
  endforeach()
  add_custom_command(TARGET mercury_palm_model_selftest POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${ONNXRuntime_ROOT_DIR}/lib/onnxruntime.dll"
      "$<TARGET_FILE_DIR:mercury_palm_model_selftest>/onnxruntime.dll"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${MERCURY_OPENCV_BIN_DIR}/opencv_world4100$<$<CONFIG:Debug>:d>.dll"
      "$<TARGET_FILE_DIR:mercury_palm_model_selftest>/opencv_world4100$<$<CONFIG:Debug>:d>.dll")
endif()
