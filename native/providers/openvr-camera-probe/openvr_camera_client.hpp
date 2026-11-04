// Public OpenVR camera-client RAII helper for bounded local diagnostics.
// It never registers a driver or writes camera pixels. Camera use is opt-in at
// the calling executable; this header performs no API calls during loading.
#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "vendor/openvr_capi.h"

#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <vector>

namespace xrwb::openvr_camera {

class CameraClient {
public:
	HMODULE dll = nullptr;
	using Init = intptr_t (*)(EVRInitError *, EVRApplicationType);
	using GetInterface = intptr_t (*)(const char *, EVRInitError *);
	using Shutdown = void (*)();
	Shutdown shutdown = nullptr;
	VR_IVRTrackedCamera_FnTable *camera = nullptr;
	VR_IVRSystem_FnTable *system = nullptr;
	VR_IVRSettings_FnTable *settings = nullptr;
	TrackedCameraHandle_t handle = 0;
	std::vector<unsigned char> pixels;

	~CameraClient()
	{
		close();
	}

	// Explicitly releases the stream, clears pixels, restores temporary power
	// values, then unloads OpenVR. Safe to call more than once.
	bool close()
	{
		if (closed_) {
			return power_restore_failed_;
		}
		closed_ = true;
		if (!pixels.empty()) {
			SecureZeroMemory(pixels.data(), pixels.size());
			pixels.clear();
		}
		if (camera && handle) {
			const auto result = camera->ReleaseVideoStreamingService(handle);
			std::printf("XR_CAMERA_STREAM_RELEASE result=%d\n", int(result));
			handle = 0;
		}
		if (restore_power_) {
			EVRSettingsError pause_error{}, screen_error{};
			char section[] = "power", pause[] = "pauseCompositorOnStandby", screen[] = "turnOffScreensTimeout";
			settings->SetBool(section, pause, previous_pause_, &pause_error);
			settings->SetFloat(section, screen, previous_screens_timeout_, &screen_error);
			power_restore_failed_ = pause_error || screen_error;
			std::printf("XR_POWER_RESTORE pause_result=%d screen_result=%d previous_pause=%d previous_screens_timeout=%.3f\n",
			            int(pause_error), int(screen_error), int(previous_pause_), previous_screens_timeout_);
		}
		if (shutdown) {
			shutdown();
		}
		if (dll) {
			FreeLibrary(dll);
			dll = nullptr;
		}
		return power_restore_failed_;
	}

	void open()
	{
		// Standalone process only: never call this from an OpenXR Blender process.
		dll = LoadLibraryExW(
		    L"C:\\Program Files (x86)\\Steam\\steamapps\\common\\SteamVR\\bin\\win64\\openvr_api.dll",
		    nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
		if (!dll) {
			throw std::runtime_error("SteamVR public API DLL unavailable");
		}
		auto init = reinterpret_cast<Init>(GetProcAddress(dll, "VR_InitInternal"));
		auto get = reinterpret_cast<GetInterface>(GetProcAddress(dll, "VR_GetGenericInterface"));
		auto stop = reinterpret_cast<Shutdown>(GetProcAddress(dll, "VR_ShutdownInternal"));
		if (!init || !get || !stop) {
			throw std::runtime_error("Missing OpenVR entry point");
		}
		EVRInitError error{};
		init(&error, EVRApplicationType_VRApplication_Background);
		if (error) {
			std::printf("XR_CAMERA_INIT_ERROR code=%d\n", int(error));
			throw std::runtime_error("OpenVR background client initialization failed");
		}
		shutdown = stop;
		camera = reinterpret_cast<VR_IVRTrackedCamera_FnTable *>(get("FnTable:IVRTrackedCamera_006", &error));
		if (error || !camera) {
			throw std::runtime_error("Public tracked camera interface unavailable");
		}
		system = reinterpret_cast<VR_IVRSystem_FnTable *>(get("FnTable:IVRSystem_026", &error));
		if (error || !system) {
			throw std::runtime_error("Public system interface unavailable");
		}
		settings = reinterpret_cast<VR_IVRSettings_FnTable *>(get("FnTable:IVRSettings_003", &error));
		if (error || !settings) {
			throw std::runtime_error("Public settings interface unavailable");
		}
	}

	void temporarily_keep_awake()
	{
		EVRSettingsError error{};
		char section[] = "power", pause[] = "pauseCompositorOnStandby", screen[] = "turnOffScreensTimeout";
		previous_pause_ = settings->GetBool(section, pause, &error);
		if (error) {
			throw std::runtime_error("Cannot read compositor pause setting");
		}
		previous_screens_timeout_ = settings->GetFloat(section, screen, &error);
		if (error || !std::isfinite(previous_screens_timeout_)) {
			throw std::runtime_error("Cannot read display timeout setting");
		}
		std::printf("XR_POWER_TEMPORARY previous_pause=%d previous_screens_timeout=%.3f restore_on_exit=1 tracking_safeguards_unchanged=1\n",
		            int(previous_pause_), previous_screens_timeout_);
		std::fflush(stdout);
		restore_power_ = true;
		settings->SetBool(section, pause, false, &error);
		if (error) {
			throw std::runtime_error("Cannot set temporary compositor pause setting");
		}
		settings->SetFloat(section, screen, 600.0f, &error);
		if (error) {
			throw std::runtime_error("Cannot set temporary display timeout");
		}
		Sleep(500);
	}

	void check(EVRTrackedCameraError error, const char *operation) const
	{
		if (error == EVRTrackedCameraError_VRTrackedCameraError_None) {
			return;
		}
		std::printf("XR_CAMERA_ERROR operation=%s code=%d name=%s\n", operation, int(error),
		            camera->GetCameraErrorNameFromEnum(error));
		throw std::runtime_error(operation);
	}

	bool power_restore_failed() const { return power_restore_failed_; }

private:
	bool restore_power_ = false;
	bool previous_pause_ = true;
	float previous_screens_timeout_ = 5.0f;
	bool power_restore_failed_ = false;
	bool closed_ = false;
};

} // namespace xrwb::openvr_camera
