// Local diagnostic only: public OpenVR camera frames stay in volatile memory.
// No camera driver, registry, image file, or skeletal trace is written. Explicit
// keep-awake modes temporarily change two public power settings and restore them.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "vendor/openvr_capi.h"
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

constexpr auto OK = EVRTrackedCameraError_VRTrackedCameraError_None;
// The public runtime supplies intrinsics for the matching undistorted image;
// never apply undistorted intrinsics directly to a distorted raw frame.
constexpr auto FRAME = EVRTrackedCameraFrameType_VRTrackedCameraFrameType_Undistorted;
static bool power_restore_failed = false;

static unsigned hold_duration(const char *text)
{
  unsigned seconds = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9') return 0;
    seconds = seconds * 10 + unsigned(*text - '0');
    if (seconds > 180) return 0;
  }
  return seconds;
}

static bool valid_buffer(uint32_t width, uint32_t height, uint32_t bytes)
{
  return width > 0 && height > 0 && width <= 8192 && height <= 8192 &&
         bytes == uint64_t(width) * height * 4 && bytes <= 64 * 1024 * 1024;
}

struct CameraClient {
  HMODULE dll = nullptr;
  using Init = intptr_t (*)(EVRInitError *, EVRApplicationType);
  using GetInterface = intptr_t (*)(const char *, EVRInitError *);
  using Shutdown = void (*)();
  Shutdown shutdown = nullptr;
  VR_IVRTrackedCamera_FnTable *camera = nullptr;
  VR_IVRSystem_FnTable *system = nullptr;
  VR_IVRSettings_FnTable *settings = nullptr;
  bool restore_power = false, previous_pause = true;
  float previous_screens_timeout = 5.0f;
  TrackedCameraHandle_t handle = 0;
  std::vector<unsigned char> pixels;

  ~CameraClient()
  {
    if (!pixels.empty()) SecureZeroMemory(pixels.data(), pixels.size());
    if (camera && handle) {
      const auto result = camera->ReleaseVideoStreamingService(handle);
      std::printf("XR_CAMERA_STREAM_RELEASE result=%d\n", int(result));
    }
    if (restore_power) {
      EVRSettingsError pause_error{}, screen_error{};
      char section[]="power", pause[]="pauseCompositorOnStandby", screen[]="turnOffScreensTimeout";
      settings->SetBool(section, pause, previous_pause, &pause_error);
      settings->SetFloat(section, screen, previous_screens_timeout, &screen_error);
      power_restore_failed = pause_error || screen_error;
      std::printf("XR_POWER_RESTORE pause_result=%d screen_result=%d previous_pause=%d previous_screens_timeout=%.3f\n",
                  int(pause_error), int(screen_error), int(previous_pause), previous_screens_timeout);
    }
    if (shutdown) shutdown();
    if (dll) FreeLibrary(dll);
  }

  void open()
  {
    // Standalone process: never call VR_Init inside an existing OpenXR Blender.
    dll = LoadLibraryExW(
        L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\openvr_api.dll",
        nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!dll) throw std::runtime_error("SteamVR public API DLL unavailable");
    auto init = reinterpret_cast<Init>(GetProcAddress(dll, "VR_InitInternal"));
    auto get = reinterpret_cast<GetInterface>(GetProcAddress(dll, "VR_GetGenericInterface"));
    auto stop = reinterpret_cast<Shutdown>(GetProcAddress(dll, "VR_ShutdownInternal"));
    if (!init || !get || !stop) throw std::runtime_error("Missing OpenVR entry point");
    EVRInitError error{};
    init(&error, EVRApplicationType_VRApplication_Background);
    if (error) {
      std::printf("XR_CAMERA_INIT_ERROR code=%d\n", int(error));
      throw std::runtime_error("OpenVR background client initialization failed");
    }
    shutdown = stop;
    camera = reinterpret_cast<VR_IVRTrackedCamera_FnTable *>(
        get("FnTable:IVRTrackedCamera_006", &error));
    if (error || !camera) throw std::runtime_error("Public tracked camera interface unavailable");
    system = reinterpret_cast<VR_IVRSystem_FnTable *>(get("FnTable:IVRSystem_026", &error));
    if (error || !system) throw std::runtime_error("Public system interface unavailable");
    settings = reinterpret_cast<VR_IVRSettings_FnTable *>(get("FnTable:IVRSettings_003", &error));
    if (error || !settings) throw std::runtime_error("Public settings interface unavailable");
  }

  void temporarily_keep_awake()
  {
    EVRSettingsError error{};
    char section[]="power", pause[]="pauseCompositorOnStandby", screen[]="turnOffScreensTimeout";
    previous_pause = settings->GetBool(section, pause, &error);
    if (error) throw std::runtime_error("Cannot read compositor pause setting");
    previous_screens_timeout = settings->GetFloat(section, screen, &error);
    if (error || !std::isfinite(previous_screens_timeout))
      throw std::runtime_error("Cannot read display timeout setting");
    std::printf("XR_POWER_TEMPORARY previous_pause=%d previous_screens_timeout=%.3f restore_on_exit=1 tracking_safeguards_unchanged=1\n",
                int(previous_pause), previous_screens_timeout);
    std::fflush(stdout);
    restore_power = true;
    settings->SetBool(section, pause, false, &error);
    if (error) throw std::runtime_error("Cannot set temporary compositor pause setting");
    settings->SetFloat(section, screen, 600.0f, &error);
    if (error) throw std::runtime_error("Cannot set temporary display timeout");
    Sleep(500);
  }

  void check(EVRTrackedCameraError error, const char *operation)
  {
    if (error == OK) return;
    std::printf("XR_CAMERA_ERROR operation=%s code=%d name=%s\n", operation, int(error),
                camera->GetCameraErrorNameFromEnum(error));
    throw std::runtime_error(operation);
  }

  void run(bool metadata_only = false)
  {
    TrackedDevicePose_t device_poses[k_unMaxTrackedDeviceCount]{};
    system->GetDeviceToAbsoluteTrackingPose(
        ETrackingUniverseOrigin_TrackingUniverseStanding, 0.0f, device_poses, k_unMaxTrackedDeviceCount);
    const auto &headset = device_poses[0];
    std::printf("XR_HEADSET_METADATA connected=%d pose_valid=%d tracking_result=%d activity=%d\n",
                int(headset.bDeviceIsConnected), int(headset.bPoseIsValid),
                int(headset.eTrackingResult), int(system->GetTrackedDeviceActivityLevel(0)));
    unsigned reference_count = 0, valid_reference_count = 0;
    for (uint32_t device = 0; device < k_unMaxTrackedDeviceCount; ++device) {
      if (system->GetTrackedDeviceClass(device) == ETrackedDeviceClass_TrackedDeviceClass_TrackingReference &&
          device_poses[device].bDeviceIsConnected) {
        ++reference_count;
        valid_reference_count += device_poses[device].bPoseIsValid;
      }
    }
    std::printf("XR_TRACKING_METADATA connected_references=%u valid_references=%u device_ids_saved=0\n",
                reference_count, valid_reference_count);
    bool present = false;
    check(camera->HasCamera(0, &present), "HasCamera");
    if (!present) throw std::runtime_error("Headset has no public camera");
    uint32_t width=0, height=0, bytes=0;
    check(camera->GetCameraFrameSize(0, FRAME, &width, &height, &bytes), "GetCameraFrameSize");
    if (!valid_buffer(width, height, bytes)) throw std::runtime_error("Unexpected camera buffer format");
    ETrackedPropertyError error{};
    const int layout = system->GetInt32TrackedDeviceProperty(
        0, ETrackedDeviceProperty_Prop_CameraFrameLayout_Int32, &error);
    if (error) throw std::runtime_error("Camera layout unavailable");
    HmdMatrix34_t camera_to_head[2]{};
    const uint32_t transform_bytes = system->GetArrayTrackedDeviceProperty(
        0, ETrackedDeviceProperty_Prop_CameraToHeadTransforms_Matrix34_Array,
        k_unHmdMatrix34PropertyTag, camera_to_head, sizeof(camera_to_head), &error);
    const bool stereo_transforms = !error && transform_bytes == sizeof(camera_to_head);
    int intrinsics_valid = 0;
    for (uint32_t eye=0; eye<2; ++eye) {
      HmdVector2_t focal{}, center{};
      const auto result = camera->GetCameraIntrinsics(0, eye, FRAME, &focal, &center);
      std::printf("XR_CAMERA_INTRINSICS camera=%u frame_type=%d result=%d focal_x=%.9f focal_y=%.9f center_x=%.9f center_y=%.9f\n",
                  eye, int(FRAME), int(result), focal.v[0], focal.v[1], center.v[0], center.v[1]);
      if (result == OK && std::isfinite(focal.v[0]) && std::isfinite(focal.v[1]) &&
          std::isfinite(center.v[0]) && std::isfinite(center.v[1]) &&
          focal.v[0]>0 && focal.v[1]>0) ++intrinsics_valid;
    }
    std::printf("XR_CAMERA_METADATA width=%u height=%u bytes=%u layout=%d intrinsics=%d stereo_transforms=%d\n",
                width, height, bytes, layout, intrinsics_valid, int(stereo_transforms));
    if (metadata_only) {
      if (!stereo_transforms || intrinsics_valid != 2) throw std::runtime_error("Incomplete static camera calibration");
      for (unsigned eye = 0; eye < 2; ++eye) {
        for (unsigned row = 0; row < 3; ++row) {
          std::printf("XR_CAMERA_TO_HEAD camera=%u row=%u values=%.9f,%.9f,%.9f,%.9f\n",
                      eye, row, camera_to_head[eye].m[row][0], camera_to_head[eye].m[row][1],
                      camera_to_head[eye].m[row][2], camera_to_head[eye].m[row][3]);
        }
      }
      std::puts("XR_METADATA_PASS camera_frames_accessed=0 power_changed=0");
      return;
    }
    check(camera->AcquireVideoStreamingService(0, &handle), "AcquireVideoStreamingService");
    pixels.resize(bytes);
    using Clock = std::chrono::steady_clock;
    const auto begin = Clock::now();
    const auto deadline = begin + std::chrono::seconds(8);
    uint32_t sequence=0, unique=0, pose_valid=0, exposure_changes=0;
    uint64_t exposure=0;
    while (Clock::now() < deadline && unique < 60) {
      CameraVideoStreamFrameHeader_t header{};
      const auto result = camera->GetVideoStreamFrameBuffer(
          handle, FRAME, pixels.data(), bytes, &header, sizeof(header));
      if (result == EVRTrackedCameraError_VRTrackedCameraError_NoFrameAvailable) {
        Sleep(5);
        continue;
      }
      check(result, "GetVideoStreamFrameBuffer");
      if (header.nWidth != width || header.nHeight != height || header.nBytesPerPixel != 4)
        throw std::runtime_error("Camera format changed; refusing stale buffer mapping");
      if (unique == 0 || header.nFrameSequence != sequence) {
        ++unique;
        if (header.trackedDevicePose.bPoseIsValid) ++pose_valid;
        if (header.ulFrameExposureTime && header.ulFrameExposureTime != exposure) ++exposure_changes;
        sequence=header.nFrameSequence;
        exposure=header.ulFrameExposureTime;
      }
      Sleep(5);
    }
    const double seconds=std::chrono::duration<double>(Clock::now()-begin).count();
    std::printf("XR_CAMERA_CAPTURE unique_frames=%u pose_valid_frames=%u exposure_changes=%u elapsed_seconds=%.3f images_saved=0 joints_inferred=0 exposure_clock_qualified=0\n",
                unique, pose_valid, exposure_changes, seconds);
    if (unique < 2) throw std::runtime_error("No advancing public camera stream");
    if (!stereo_transforms || intrinsics_valid != 2)
      throw std::runtime_error("Frames arrived but stereo calibration metadata is incomplete");
    std::puts("XR_CAMERA_PROBE_PASS optical_hands_qualified=0");
  }
};

int main(int argc, char **argv)
{
  if (argc == 2 && !std::strcmp(argv[1], "--self-test")) {
    assert(hold_duration("30") == 30 && hold_duration("120") == 120);
    assert(hold_duration("180") == 180 && !hold_duration("181"));
    assert(!hold_duration("-1") && !hold_duration("2x") && !hold_duration(""));
    assert(valid_buffer(1920,960,1920*960*4));
    assert(!valid_buffer(0,960,0));
    assert(!valid_buffer(1920,960,1920*960*3));
    assert(!valid_buffer(0xffffffffu,0xffffffffu,4));
    std::puts("XR_CAMERA_BOUNDS_PASS camera_accessed=0");
    return 0;
  }
  const bool keep_awake = argc == 2 && !std::strcmp(argv[1], "--consented-keep-awake-camera-test");
  const bool hold_only = (argc == 2 || argc == 3) && !std::strcmp(argv[1], "--consented-keep-awake-only");
  const unsigned hold_seconds = hold_only && argc == 3 ? hold_duration(argv[2]) : 30;
  const bool metadata_only = argc == 2 && !std::strcmp(argv[1], "--metadata-only");
  if ((argc != 2 && !(argc == 3 && hold_only)) || !hold_seconds ||
      (std::strcmp(argv[1], "--consented-camera-test") && !keep_awake && !hold_only && !metadata_only)) {
    std::fputs("Explicit local camera-test consent required. No images are saved.\n", stderr);
    return 2;
  }
  try {
    CameraClient client;
    client.open();
    if (keep_awake || hold_only) client.temporarily_keep_awake();
    if (hold_only) {
      std::printf("XR_POWER_HOLD seconds=%u camera_accessed=0 tracking_safeguards_unchanged=1\n", hold_seconds);
      std::fflush(stdout);
      Sleep(hold_seconds * 1000);
    }
    else {
      client.run(metadata_only);
    }
  } catch (const std::exception &error) {
    std::fprintf(stderr, "XR_CAMERA_PROBE_FAIL %s\n", error.what());
    return 1;
  }
  return power_restore_failed ? 1 : 0;
}
