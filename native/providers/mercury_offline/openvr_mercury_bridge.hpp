// Private, offline evaluation only. This header contains pure calibration and
// frame-normalization math. It never opens an OpenVR interface or a camera.
#pragma once

#include <cstddef>
#include <cstdint>

#include "openvr_capi.h"

extern "C" {
#include "tracking/t_tracking.h"
}

namespace xrwb::mercury_offline {

constexpr uint32_t kRgbaBytesPerPixel = 4;
constexpr uint32_t kMercurySquarePixels = 960;

enum class BridgeStatus {
	Ok,
	UnverifiedOpticalBasis,
	UnsupportedFrameType,
	UnsupportedStereoLayout,
	InvalidFrameDimensions,
	InvalidIntrinsics,
	InvalidTransform,
	InvalidBaseline,
	InvalidCrop,
};

struct CameraIntrinsicsPixels {
	double focal_x = 0.0;
	double focal_y = 0.0;
	double center_x = 0.0;
	double center_y = 0.0;
};

// `camera_to_head` is the public OpenVR CameraToHead property. It must be
// paired with intrinsics reported for the same *undistorted* frame type.
struct OpenVrUndistortedStereoMetadata {
	uint32_t combined_width = 0;
	uint32_t combined_height = 0;
	uint32_t bytes_per_pixel = 0;
	EVRTrackedCameraFrameLayout layout = EVRTrackedCameraFrameLayout_Mono;
	EVRTrackedCameraFrameType frame_type = EVRTrackedCameraFrameType_VRTrackedCameraFrameType_Distorted;
	HmdMatrix34_t camera_to_head[2]{};
	CameraIntrinsicsPixels intrinsics[2]{};
};

struct EyeCrop {
	uint32_t x = 0;
	uint32_t y = 0;
	uint32_t width = 0;
	uint32_t height = 0;
};

struct StereoFramePlan {
	uint32_t source_eye_width = 0;
	uint32_t source_eye_height = 0;
	uint32_t target_width = 0;
	uint32_t target_height = 0;
	EyeCrop eye_crop[2]{};
};

// An optical-axis basis maps OpenCV-style image rays (+X right, +Y down, +Z
// forward) into the coordinate system used by OpenVR camera transforms.
struct OpticalAxisBasis {
	double openvr_from_optical[3][3]{};
	bool independently_validated = false;
};

OpticalAxisBasis synthetic_identity_basis_for_math_test();

// Evidence-backed basis for this bridge: Valve's public OpenVR SDK defines
// +X right, +Y up, -Z forward, while Monado's camera contract is OpenCV's +X
// right, +Y down, +Z forward. The proper 180-degree X rotation is therefore
// diag(1, -1, -1). It supports local camera-relative inference only; it does
// not establish an HMD/world pose.
OpticalAxisBasis openvr_from_opencv_optical_basis();

BridgeStatus make_square_plan(const OpenVrUndistortedStereoMetadata &metadata,
                              uint32_t target_pixels,
                              StereoFramePlan *out_plan);

// Allocates a zero-distortion OpenCV-radtan8 calibration. The zero coefficients
// represent the pinhole model for OpenVR's already-undistorted image. It fails
// closed unless the caller provides either the evidence-backed OpenVR/OpenCV
// basis above or another separately validated optical-axis basis.
BridgeStatus make_mercury_calibration(const OpenVrUndistortedStereoMetadata &metadata,
                                      const StereoFramePlan &plan,
                                      const OpticalAxisBasis &basis,
                                      t_stereo_camera_calibration **out_calibration);

// Converts exactly one latest packed RGBA side-by-side frame in RAM to two
// square grayscale Mats. It has no camera acquisition, file, or joint output.
BridgeStatus prepare_latest_rgba_frame(const OpenVrUndistortedStereoMetadata &metadata,
                                       const StereoFramePlan &plan,
                                       const uint8_t *rgba,
                                       size_t rgba_bytes,
                                       void **out_left_gray_mat,
                                       void **out_right_gray_mat);

// Opaque cv::Mat outputs from prepare_latest_rgba_frame are released here so
// clients using this small boundary do not need an OpenCV header in their API.
void release_gray_mat(void **mat);

const char *bridge_status_string(BridgeStatus status);

} // namespace xrwb::mercury_offline
