// Private, offline evaluation only. No camera or OpenVR runtime calls live here.
#include "openvr_mercury_bridge.hpp"

#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <new>

#include <opencv2/imgproc.hpp>

namespace xrwb::mercury_offline {
namespace {

constexpr double kEpsilon = 1e-6;
constexpr double kMinBaselineMeters = 0.01;
constexpr double kMaxBaselineMeters = 0.20;

bool
is_finite(double value)
{
	return std::isfinite(value);
}

bool
has_horizontal_stereo_layout(EVRTrackedCameraFrameLayout layout)
{
	return (static_cast<int>(layout) & EVRTrackedCameraFrameLayout_Stereo) != 0 &&
	       (static_cast<int>(layout) & EVRTrackedCameraFrameLayout_HorizontalLayout) != 0;
}

bool
is_proper_rotation(const double matrix[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			if (!is_finite(matrix[row][column])) {
				return false;
			}
		}
	}

	for (int first = 0; first < 3; ++first) {
		for (int second = 0; second < 3; ++second) {
			double dot = 0.0;
			for (int index = 0; index < 3; ++index) {
				dot += matrix[index][first] * matrix[index][second];
			}
			if (std::abs(dot - (first == second ? 1.0 : 0.0)) > 1e-4) {
				return false;
			}
		}
	}

	const double determinant =
	    matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1]) -
	    matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0]) +
	    matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0]);
	return std::abs(determinant - 1.0) <= 1e-4;
}

bool
is_valid_camera_matrix(const HmdMatrix34_t &matrix)
{
	double rotation[3][3]{};
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			rotation[row][column] = matrix.m[row][column];
		}
		if (!is_finite(matrix.m[row][3])) {
			return false;
		}
	}
	return is_proper_rotation(rotation);
}

bool
is_valid_intrinsics(const CameraIntrinsicsPixels &intrinsics, uint32_t width, uint32_t height)
{
	return is_finite(intrinsics.focal_x) && is_finite(intrinsics.focal_y) &&
	       is_finite(intrinsics.center_x) && is_finite(intrinsics.center_y) && intrinsics.focal_x > 0.0 &&
	       intrinsics.focal_y > 0.0 && intrinsics.center_x >= 0.0 &&
	       intrinsics.center_y >= 0.0 && intrinsics.center_x < width && intrinsics.center_y < height;
}

void
transpose(const double input[3][3], double output[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			output[row][column] = input[column][row];
		}
	}
}

void
multiply(const double left[3][3], const double right[3][3], double output[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			double value = 0.0;
			for (int index = 0; index < 3; ++index) {
				value += left[row][index] * right[index][column];
			}
			output[row][column] = value;
		}
	}
}

void
multiply_vector(const double matrix[3][3], const double input[3], double output[3])
{
	for (int row = 0; row < 3; ++row) {
		output[row] = 0.0;
		for (int column = 0; column < 3; ++column) {
			output[row] += matrix[row][column] * input[column];
		}
	}
}

void
read_rotation(const HmdMatrix34_t &matrix, double output[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			output[row][column] = matrix.m[row][column];
		}
	}
}

void
read_translation(const HmdMatrix34_t &matrix, double output[3])
{
	for (int index = 0; index < 3; ++index) {
		output[index] = matrix.m[index][3];
	}
}

BridgeStatus
validate_metadata(const OpenVrUndistortedStereoMetadata &metadata, uint32_t *out_eye_width)
{
	if (metadata.frame_type != EVRTrackedCameraFrameType_VRTrackedCameraFrameType_Undistorted) {
		return BridgeStatus::UnsupportedFrameType;
	}
	if (!has_horizontal_stereo_layout(metadata.layout)) {
		return BridgeStatus::UnsupportedStereoLayout;
	}
	if (metadata.bytes_per_pixel != kRgbaBytesPerPixel || metadata.combined_width == 0 ||
	    metadata.combined_height == 0 || (metadata.combined_width % 2) != 0) {
		return BridgeStatus::InvalidFrameDimensions;
	}
	const uint32_t eye_width = metadata.combined_width / 2;
	if (eye_width < 32 || metadata.combined_height < 32 || eye_width > 8192 || metadata.combined_height > 8192) {
		return BridgeStatus::InvalidFrameDimensions;
	}
	for (int eye = 0; eye < 2; ++eye) {
		if (!is_valid_intrinsics(metadata.intrinsics[eye], eye_width, metadata.combined_height)) {
			return BridgeStatus::InvalidIntrinsics;
		}
		if (!is_valid_camera_matrix(metadata.camera_to_head[eye])) {
			return BridgeStatus::InvalidTransform;
		}
	}
	*out_eye_width = eye_width;
	return BridgeStatus::Ok;
}

bool
is_valid_plan(const OpenVrUndistortedStereoMetadata &metadata, uint32_t eye_width, const StereoFramePlan &plan)
{
	if (plan.source_eye_width != eye_width || plan.source_eye_height != metadata.combined_height ||
	    plan.target_width == 0 || plan.target_height == 0 || plan.target_width > 4096 || plan.target_height > 4096) {
		return false;
	}
	for (uint32_t eye = 0; eye < 2; ++eye) {
		const EyeCrop &crop = plan.eye_crop[eye];
		const uint32_t eye_origin_x = eye * eye_width;
		if (crop.width == 0 || crop.height == 0 || crop.x < eye_origin_x || crop.y > metadata.combined_height) {
			return false;
		}
		const uint32_t local_x = crop.x - eye_origin_x;
		if (local_x > eye_width || crop.width > eye_width - local_x || crop.height > metadata.combined_height - crop.y) {
			return false;
		}
	}
	return true;
}

void
copy_intrinsics(const CameraIntrinsicsPixels &source, const EyeCrop &crop, uint32_t target, t_camera_calibration *out)
{
	const double scale_x = static_cast<double>(target) / crop.width;
	const double scale_y = static_cast<double>(target) / crop.height;
	out->image_size_pixels.w = target;
	out->image_size_pixels.h = target;
	out->intrinsics[0][0] = source.focal_x * scale_x;
	out->intrinsics[1][1] = source.focal_y * scale_y;
	out->intrinsics[0][2] = (source.center_x - crop.x) * scale_x;
	out->intrinsics[1][2] = (source.center_y - crop.y) * scale_y;
	out->intrinsics[2][2] = 1.0;
	std::memset(out->distortion_parameters_as_array, 0, sizeof(out->distortion_parameters_as_array));
}

} // namespace

OpticalAxisBasis
synthetic_identity_basis_for_math_test()
{
	OpticalAxisBasis basis{};
	for (int index = 0; index < 3; ++index) {
		basis.openvr_from_optical[index][index] = 1.0;
	}
	basis.independently_validated = true;
	return basis;
}

OpticalAxisBasis
openvr_from_opencv_optical_basis()
{
	// Primary source contract: OpenVR's public_vrtypes.h documents a
	// right-handed +X-right/+Y-up/-Z-forward basis. Monado's
	// t_camera_models_unproject_and_flip documents the inverse OpenCV-to-XRT
	// conversion (+Z forward/-Y up to -Z forward/+Y up). Rectus v0.4.2's
	// ChangeBasisToFromOpenCV independently applies this same 180-degree X
	// rotation around its OpenVR/OpenXR camera-transform path.
	OpticalAxisBasis basis{};
	basis.openvr_from_optical[0][0] = 1.0;
	basis.openvr_from_optical[1][1] = -1.0;
	basis.openvr_from_optical[2][2] = -1.0;
	basis.independently_validated = true;
	return basis;
}

BridgeStatus
make_square_plan(const OpenVrUndistortedStereoMetadata &metadata, uint32_t target_pixels, StereoFramePlan *out_plan)
{
	if (out_plan == nullptr || target_pixels == 0 || target_pixels > 4096) {
		return BridgeStatus::InvalidFrameDimensions;
	}
	uint32_t eye_width = 0;
	const BridgeStatus metadata_status = validate_metadata(metadata, &eye_width);
	if (metadata_status != BridgeStatus::Ok) {
		return metadata_status;
	}

	const uint32_t square = std::min(eye_width, metadata.combined_height);
	const uint32_t crop_x = (eye_width - square) / 2;
	const uint32_t crop_y = (metadata.combined_height - square) / 2;
	StereoFramePlan plan{};
	plan.source_eye_width = eye_width;
	plan.source_eye_height = metadata.combined_height;
	plan.target_width = target_pixels;
	plan.target_height = target_pixels;
	for (uint32_t eye = 0; eye < 2; ++eye) {
		plan.eye_crop[eye] = {crop_x, crop_y, square, square};
		plan.eye_crop[eye].x += eye * eye_width;
	}
	*out_plan = plan;
	return BridgeStatus::Ok;
}

BridgeStatus
make_mercury_calibration(const OpenVrUndistortedStereoMetadata &metadata,
                         const StereoFramePlan &plan,
                         const OpticalAxisBasis &basis,
                         t_stereo_camera_calibration **out_calibration)
{
	if (out_calibration == nullptr) {
		return BridgeStatus::InvalidFrameDimensions;
	}
	*out_calibration = nullptr;
	uint32_t eye_width = 0;
	const BridgeStatus metadata_status = validate_metadata(metadata, &eye_width);
	if (metadata_status != BridgeStatus::Ok) {
		return metadata_status;
	}
	if (!basis.independently_validated) {
		return BridgeStatus::UnverifiedOpticalBasis;
	}
	if (!is_proper_rotation(basis.openvr_from_optical)) {
		return BridgeStatus::InvalidTransform;
	}
	if (!is_valid_plan(metadata, eye_width, plan) || plan.target_width != plan.target_height) {
		return BridgeStatus::InvalidCrop;
	}
	for (int eye = 0; eye < 2; ++eye) {
		const EyeCrop &crop = plan.eye_crop[eye];
		const uint32_t local_x = crop.x - static_cast<uint32_t>(eye) * eye_width;
		if (
		    metadata.intrinsics[eye].center_x < local_x ||
		    metadata.intrinsics[eye].center_x >= local_x + crop.width ||
		    metadata.intrinsics[eye].center_y < crop.y ||
		    metadata.intrinsics[eye].center_y >= crop.y + crop.height) {
			return BridgeStatus::InvalidCrop;
		}
	}

	// The property name is CameraToHead: p_head = H_from_camera * p_camera.
	// Therefore p_right = inverse(H_from_right) * H_from_left * p_left.
	double head_from_left[3][3]{};
	double head_from_right[3][3]{};
	double head_from_right_inverse[3][3]{};
	double right_from_left_openvr[3][3]{};
	read_rotation(metadata.camera_to_head[0], head_from_left);
	read_rotation(metadata.camera_to_head[1], head_from_right);
	transpose(head_from_right, head_from_right_inverse);
	multiply(head_from_right_inverse, head_from_left, right_from_left_openvr);

	double head_left_translation[3]{};
	double head_right_translation[3]{};
	double translation_delta[3]{};
	double right_from_left_translation_openvr[3]{};
	read_translation(metadata.camera_to_head[0], head_left_translation);
	read_translation(metadata.camera_to_head[1], head_right_translation);
	for (int index = 0; index < 3; ++index) {
		translation_delta[index] = head_left_translation[index] - head_right_translation[index];
	}
	multiply_vector(head_from_right_inverse, translation_delta, right_from_left_translation_openvr);

	// Transform from optical image coordinates into the supplied OpenVR camera
	// axes, then bring the relative pose back into optical coordinates.
	double optical_from_openvr[3][3]{};
	double temporary_rotation[3][3]{};
	double right_from_left_optical[3][3]{};
	double right_from_left_translation_optical[3]{};
	transpose(basis.openvr_from_optical, optical_from_openvr);
	multiply(optical_from_openvr, right_from_left_openvr, temporary_rotation);
	multiply(temporary_rotation, basis.openvr_from_optical, right_from_left_optical);
	multiply_vector(optical_from_openvr, right_from_left_translation_openvr, right_from_left_translation_optical);

	const double baseline = std::sqrt(right_from_left_translation_optical[0] * right_from_left_translation_optical[0] +
	                                  right_from_left_translation_optical[1] * right_from_left_translation_optical[1] +
	                                  right_from_left_translation_optical[2] * right_from_left_translation_optical[2]);
	if (!is_finite(baseline) || baseline < kMinBaselineMeters || baseline > kMaxBaselineMeters ||
	    right_from_left_translation_optical[0] >= -kEpsilon || !is_proper_rotation(right_from_left_optical)) {
		return BridgeStatus::InvalidBaseline;
	}

	t_stereo_camera_calibration *calibration = nullptr;
	t_stereo_camera_calibration_alloc(&calibration, T_DISTORTION_OPENCV_RADTAN_8);
	if (calibration == nullptr) {
		return BridgeStatus::InvalidTransform;
	}
	for (int eye = 0; eye < 2; ++eye) {
		EyeCrop local_crop = plan.eye_crop[eye];
		local_crop.x -= static_cast<uint32_t>(eye) * eye_width;
		copy_intrinsics(metadata.intrinsics[eye], local_crop, plan.target_width, &calibration->view[eye]);
	}
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			calibration->camera_rotation[row][column] = right_from_left_optical[row][column];
		}
		calibration->camera_translation[row] = right_from_left_translation_optical[row];
	}
	*out_calibration = calibration;
	return BridgeStatus::Ok;
}

BridgeStatus
prepare_latest_rgba_frame(const OpenVrUndistortedStereoMetadata &metadata,
                          const StereoFramePlan &plan,
                          const uint8_t *rgba,
                          size_t rgba_bytes,
                          void **out_left_gray_mat,
                          void **out_right_gray_mat)
{
	if (out_left_gray_mat == nullptr || out_right_gray_mat == nullptr || rgba == nullptr) {
		return BridgeStatus::InvalidFrameDimensions;
	}
	*out_left_gray_mat = nullptr;
	*out_right_gray_mat = nullptr;
	uint32_t eye_width = 0;
	const BridgeStatus metadata_status = validate_metadata(metadata, &eye_width);
	if (metadata_status != BridgeStatus::Ok) {
		return metadata_status;
	}
	if (!is_valid_plan(metadata, eye_width, plan)) {
		return BridgeStatus::InvalidCrop;
	}
	const size_t expected_bytes = static_cast<size_t>(metadata.combined_width) * metadata.combined_height * kRgbaBytesPerPixel;
	if (rgba_bytes != expected_bytes) {
		return BridgeStatus::InvalidFrameDimensions;
	}

	try {
		cv::Mat combined(static_cast<int>(metadata.combined_height), static_cast<int>(metadata.combined_width), CV_8UC4,
		                 const_cast<uint8_t *>(rgba));
		auto left_gray = std::make_unique<cv::Mat>();
		auto right_gray = std::make_unique<cv::Mat>();
		cv::Mat *gray[2] = {left_gray.get(), right_gray.get()};
		for (int eye = 0; eye < 2; ++eye) {
			const EyeCrop &crop = plan.eye_crop[eye];
			cv::Mat rgba_crop = combined(cv::Rect(static_cast<int>(crop.x), static_cast<int>(crop.y),
			                                         static_cast<int>(crop.width), static_cast<int>(crop.height)));
			cv::Mat grayscale;
			cv::cvtColor(rgba_crop, grayscale, cv::COLOR_RGBA2GRAY);
			cv::resize(grayscale, *gray[eye], cv::Size(static_cast<int>(plan.target_width),
			                                            static_cast<int>(plan.target_height)),
			           0.0, 0.0, cv::INTER_LINEAR);
		}
		*out_left_gray_mat = left_gray.release();
		*out_right_gray_mat = right_gray.release();
		return BridgeStatus::Ok;
	} catch (const cv::Exception &) {
		return BridgeStatus::InvalidFrameDimensions;
	} catch (const std::bad_alloc &) {
		return BridgeStatus::InvalidFrameDimensions;
	}
}

void
release_gray_mat(void **mat)
{
	if (mat == nullptr || *mat == nullptr) {
		return;
	}
	delete static_cast<cv::Mat *>(*mat);
	*mat = nullptr;
}

const char *
bridge_status_string(BridgeStatus status)
{
	switch (status) {
	case BridgeStatus::Ok: return "ok";
	case BridgeStatus::UnverifiedOpticalBasis: return "unverified_optical_basis";
	case BridgeStatus::UnsupportedFrameType: return "unsupported_frame_type";
	case BridgeStatus::UnsupportedStereoLayout: return "unsupported_stereo_layout";
	case BridgeStatus::InvalidFrameDimensions: return "invalid_frame_dimensions";
	case BridgeStatus::InvalidIntrinsics: return "invalid_intrinsics";
	case BridgeStatus::InvalidTransform: return "invalid_transform";
	case BridgeStatus::InvalidBaseline: return "invalid_baseline";
	case BridgeStatus::InvalidCrop: return "invalid_crop";
	}
	return "unknown";
}

} // namespace xrwb::mercury_offline
