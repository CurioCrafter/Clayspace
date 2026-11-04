// Private, offline evaluation only. This executable never initializes OpenVR,
// enumerates cameras, loads USB drivers, or registers a SteamVR driver.

#include <cstdio>
#include <cmath>
#include <utility>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/core/utils/logger.hpp>

extern "C" {
#include "tracking/t_hand_tracking.h"
#include "tracking/t_tracking.h"
}

#include "hg_interface.h"
#include "hg_sync.hpp"
#include "openvr_mercury_bridge.hpp"
#include "tracking/t_frame_cv_mat_wrapper.hpp"

namespace xat = xrt::auxiliary::tracking;

namespace {

void
set_identity(double matrix[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int column = 0; column < 3; ++column) {
			matrix[row][column] = row == column ? 1.0 : 0.0;
		}
	}
}

t_stereo_camera_calibration *
make_synthetic_calibration()
{
	t_stereo_camera_calibration *calibration = nullptr;
	t_stereo_camera_calibration_alloc(&calibration, T_DISTORTION_FISHEYE_KB4);

	for (int view = 0; view < 2; ++view) {
		auto &camera = calibration->view[view];
		camera.image_size_pixels.w = 960;
		camera.image_size_pixels.h = 960;
		camera.intrinsics[0][0] = 480.0;
		camera.intrinsics[1][1] = 480.0;
		camera.intrinsics[0][2] = 480.0;
		camera.intrinsics[1][2] = 480.0;
		camera.intrinsics[2][2] = 1.0;
		camera.kb4 = {};
	}

	set_identity(calibration->camera_rotation);
	calibration->camera_translation[0] = -0.060;
	return calibration;
}

} // namespace

bool
check(bool condition, const char *message)
{
	if (condition) {
		return true;
	}
	std::fprintf(stderr, "MERCURY_OFFLINE_SELFTEST_FAIL %s\n", message);
	return false;
}

HmdMatrix34_t
camera_to_head(double translate_x)
{
	HmdMatrix34_t matrix{};
	for (int index = 0; index < 3; ++index) {
		matrix.m[index][index] = 1.0f;
	}
	matrix.m[0][3] = static_cast<float>(translate_x);
	return matrix;
}

HmdMatrix34_t
camera_to_head_yaw_90(double translate_x)
{
	HmdMatrix34_t matrix{};
	// Right-handed +90 degrees around the OpenVR +Y axis.
	matrix.m[0][2] = 1.0f;
	matrix.m[1][1] = 1.0f;
	matrix.m[2][0] = -1.0f;
	matrix.m[0][3] = static_cast<float>(translate_x);
	return matrix;
}

bool
project_left_space_point(const t_stereo_camera_calibration &calibration,
                         int view,
                         const double point_left[3],
                         double *out_x,
                         double *out_y,
                         double *out_depth)
{
	if (view < 0 || view > 1 || out_x == nullptr || out_y == nullptr || out_depth == nullptr) {
		return false;
	}
	double point[3] = {point_left[0], point_left[1], point_left[2]};
	if (view == 1) {
		for (int row = 0; row < 3; ++row) {
			point[row] = calibration.camera_translation[row];
			for (int column = 0; column < 3; ++column) {
				point[row] += calibration.camera_rotation[row][column] * point_left[column];
			}
		}
	}
	if (!std::isfinite(point[2]) || point[2] <= 1.0e-9) {
		return false;
	}
	const auto &intrinsics = calibration.view[view].intrinsics;
	*out_x = intrinsics[0][0] * point[0] / point[2] + intrinsics[0][2];
	*out_y = intrinsics[1][1] * point[1] / point[2] + intrinsics[1][2];
	*out_depth = point[2];
	return std::isfinite(*out_x) && std::isfinite(*out_y);
}

xrwb::mercury_offline::OpenVrUndistortedStereoMetadata
valid_openvr_metadata()
{
	using namespace xrwb::mercury_offline;
	OpenVrUndistortedStereoMetadata metadata{};
	metadata.combined_width = 1920;
	metadata.combined_height = 960;
	metadata.bytes_per_pixel = kRgbaBytesPerPixel;
	metadata.layout = static_cast<EVRTrackedCameraFrameLayout>(
	    EVRTrackedCameraFrameLayout_Stereo | EVRTrackedCameraFrameLayout_HorizontalLayout);
	metadata.frame_type = EVRTrackedCameraFrameType_VRTrackedCameraFrameType_Undistorted;
	metadata.camera_to_head[0] = camera_to_head(0.0);
	metadata.camera_to_head[1] = camera_to_head(0.064);
	for (auto &intrinsics : metadata.intrinsics) {
		intrinsics.focal_x = 640.0;
		intrinsics.focal_y = 640.0;
		intrinsics.center_x = 480.0;
		intrinsics.center_y = 480.0;
	}
	return metadata;
}

bool
run_openvr_bridge_math_selftest()
{
	using namespace xrwb::mercury_offline;
	OpenVrUndistortedStereoMetadata metadata = valid_openvr_metadata();
	StereoFramePlan plan{};
	if (!check(make_square_plan(metadata, kMercurySquarePixels, &plan) == BridgeStatus::Ok,
	           "openvr_plan")) {
		return false;
	}
	if (!check(plan.eye_crop[0].x == 0 && plan.eye_crop[1].x == 960 && plan.eye_crop[0].width == 960,
	           "openvr_horizontal_crop")) {
		return false;
	}

	t_stereo_camera_calibration *calibration = nullptr;
	OpticalAxisBasis unverified{};
	if (!check(make_mercury_calibration(metadata, plan, unverified, &calibration) ==
	               BridgeStatus::UnverifiedOpticalBasis &&
	               calibration == nullptr,
	           "openvr_basis_fail_closed")) {
		return false;
	}

	const OpticalAxisBasis synthetic_basis = synthetic_identity_basis_for_math_test();
	const OpticalAxisBasis openvr_basis = openvr_from_opencv_optical_basis();
	if (!check(synthetic_basis.independently_validated && synthetic_basis.openvr_from_optical[0][0] == 1.0 &&
	               synthetic_basis.openvr_from_optical[1][1] == 1.0 && synthetic_basis.openvr_from_optical[2][2] == 1.0 &&
	               openvr_basis.independently_validated && openvr_basis.openvr_from_optical[0][0] == 1.0 &&
	               openvr_basis.openvr_from_optical[1][1] == -1.0 && openvr_basis.openvr_from_optical[2][2] == -1.0,
	           "openvr_opencv_basis")) {
		return false;
	}
	if (!check(make_mercury_calibration(metadata, plan, openvr_basis, &calibration) == BridgeStatus::Ok,
	           "openvr_known_baseline")) {
		return false;
	}
	const bool known_baseline = calibration->view[0].distortion_model == T_DISTORTION_OPENCV_RADTAN_8 &&
	                            std::abs(calibration->camera_translation[0] + 0.064) < 1e-6 &&
	                            std::abs(calibration->camera_translation[1]) < 1e-6 &&
	                            std::abs(calibration->camera_translation[2]) < 1e-6 &&
	                            std::abs(calibration->camera_rotation[0][0] - 1.0) < 1e-6 &&
	                            std::abs(calibration->view[0].intrinsics[0][0] - 640.0) < 1e-6 &&
	                            std::abs(calibration->view[0].intrinsics[0][2] - 480.0) < 1e-6;
	if (!check(known_baseline, "openvr_known_baseline_values")) {
		t_stereo_camera_calibration_reference(&calibration, nullptr);
		return false;
	}

	// OpenCV's stereo convention is P_right = R * P_left + T. A real camera
	// calibration must satisfy this same forward-projection/positive-depth
	// check before its values are trusted, rather than being repaired by a
	// sign flip or an eye swap. For this known 64 mm baseline, a point straight
	// ahead of the left camera appears to the left in the right-eye image.
	const double point_left[3] = {0.0, 0.0, 1.0};
	double left_x = 0.0, left_y = 0.0, left_depth = 0.0;
	double right_x = 0.0, right_y = 0.0, right_depth = 0.0;
	const bool positive_depth_projection =
	    project_left_space_point(*calibration, 0, point_left, &left_x, &left_y, &left_depth) &&
	    project_left_space_point(*calibration, 1, point_left, &right_x, &right_y, &right_depth) &&
	    std::abs(left_x - 480.0) < 1e-6 && std::abs(left_y - 480.0) < 1e-6 &&
	    std::abs(right_x - 439.04) < 1e-4 && std::abs(right_y - 480.0) < 1e-6 &&
	    left_depth > 0.0 && right_depth > 0.0 && right_x < left_x;
	t_stereo_camera_calibration_reference(&calibration, nullptr);
	if (!check(positive_depth_projection, "openvr_stereo_forward_projection")) {
		return false;
	}

	metadata = valid_openvr_metadata();
	metadata.camera_to_head[0] = camera_to_head_yaw_90(0.0);
	if (!check(make_mercury_calibration(metadata, plan, openvr_basis, &calibration) == BridgeStatus::Ok,
	           "openvr_basis_rotation")) {
		return false;
	}
	const bool basis_rotation = std::abs(calibration->camera_rotation[0][2] + 1.0) < 1e-6 &&
	                            std::abs(calibration->camera_rotation[1][1] - 1.0) < 1e-6 &&
	                            std::abs(calibration->camera_rotation[2][0] - 1.0) < 1e-6;
	t_stereo_camera_calibration_reference(&calibration, nullptr);
	if (!check(basis_rotation, "openvr_basis_rotation_values")) {
		return false;
	}

	metadata = valid_openvr_metadata();
	StereoFramePlan seam_crossing_plan = plan;
	seam_crossing_plan.eye_crop[0].x = 1;
	if (!check(make_mercury_calibration(metadata, seam_crossing_plan, openvr_basis, &calibration) ==
	               BridgeStatus::InvalidCrop,
	           "openvr_crop_crosses_stereo_seam")) {
		return false;
	}

	std::swap(metadata.camera_to_head[0], metadata.camera_to_head[1]);
	if (!check(make_mercury_calibration(metadata, plan, openvr_basis, &calibration) ==
	               BridgeStatus::InvalidBaseline,
	           "openvr_swapped_eyes")) {
		return false;
	}
	std::swap(metadata.camera_to_head[0], metadata.camera_to_head[1]);
	metadata.camera_to_head[1] = metadata.camera_to_head[0];
	if (!check(make_mercury_calibration(metadata, plan, openvr_basis, &calibration) ==
	               BridgeStatus::InvalidBaseline,
	           "openvr_zero_baseline")) {
		return false;
	}

	metadata = valid_openvr_metadata();
	std::vector<uint8_t> rgba(static_cast<size_t>(metadata.combined_width) * metadata.combined_height * kRgbaBytesPerPixel,
	                          0);
	rgba[0] = 255; // Red left pixel: expected luma near 76.
	const size_t right_start = static_cast<size_t>(960) * kRgbaBytesPerPixel;
	rgba[right_start + 1] = 255; // Green right pixel: expected luma near 150.
	void *left_gray = nullptr;
	void *right_gray = nullptr;
	if (!check(prepare_latest_rgba_frame(metadata, plan, rgba.data(), rgba.size(), &left_gray, &right_gray) ==
	               BridgeStatus::Ok,
	           "openvr_rgba_prepare")) {
		return false;
	}
	auto *left = static_cast<cv::Mat *>(left_gray);
	auto *right = static_cast<cv::Mat *>(right_gray);
	const bool frame_values = left->rows == 960 && left->cols == 960 && right->rows == 960 && right->cols == 960 &&
	                          std::abs(int(left->at<uint8_t>(0, 0)) - 76) <= 1 &&
	                          std::abs(int(right->at<uint8_t>(0, 0)) - 150) <= 1;
	release_gray_mat(&left_gray);
	release_gray_mat(&right_gray);
	if (!check(frame_values, "openvr_rgba_values")) {
		return false;
	}

	std::puts("MERCURY_OPENVR_CALIBRATION_MATH_PASS camera_accessed=0 joints_emitted=0 positive_depth=1");
	return true;
}

int
main()
{
	// The self-test intentionally runs without optional OpenCV parallel plugins;
	// keep that expected optional-plugin probing out of its machine-readable log.
	cv::utils::logging::setLogLevel(cv::utils::logging::LOG_LEVEL_ERROR);
	if (!run_openvr_bridge_math_selftest()) {
		return 10;
	}
	t_stereo_camera_calibration *calibration = make_synthetic_calibration();
	t_camera_extra_info camera_info = {};
	for (int view = 0; view < 2; ++view) {
		camera_info.views[view].camera_orientation = CAMERA_ORIENTATION_0;
		camera_info.views[view].boundary_type = HT_IMAGE_BOUNDARY_CIRCLE;
		camera_info.views[view].boundary.circle.normalized_center = {0.5f, 0.5f};
		camera_info.views[view].boundary.circle.normalized_radius = 0.55f;
	}

	t_hand_tracking_sync *tracker =
	    t_hand_tracking_sync_mercury_create(calibration, camera_info, MERCURY_MODEL_DIR);
	if (tracker == nullptr) {
		std::fputs("MERCURY_OFFLINE_SELFTEST_FAIL tracker_creation\n", stderr);
		t_stereo_camera_calibration_reference(&calibration, nullptr);
		return 1;
	}

	cv::Mat blank(960, 960, CV_8UC1, cv::Scalar(0));
	xrt_frame *frames[2] = {nullptr, nullptr};
	for (int view = 0; view < 2; ++view) {
		xat::FrameMat::Params params;
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = 1'000'000;
		xat::FrameMat::wrapL8(blank, &frames[view], params);
	}

	xrt_hand_joint_set hands[2] = {};
	uint64_t output_timestamp_ns = 0;
	t_ht_sync_process(tracker, frames[0], frames[1], &hands[0], &hands[1], &output_timestamp_ns);
	auto &implementation = xrt::tracking::hand::mercury::HandTracking::fromC(tracker);
	const auto &diagnostics = implementation.diagnostics;
	const bool blank_detector_path = diagnostics.detector_view_runs > 0 &&
	                                 diagnostics.detector_scores_nonfinite == 0 &&
	                                 diagnostics.detector_scores_over_03 == 0 &&
	                                 diagnostics.detector_combined_gate_pass == 0 &&
	                                 diagnostics.keypoint_view_runs == 0;
	if (!check(blank_detector_path, "blank_detector_diagnostics")) {
		for (xrt_frame *&frame : frames) { xrt_frame_reference(&frame, nullptr); }
		t_ht_sync_destroy(&tracker);
		t_stereo_camera_calibration_reference(&calibration, nullptr);
		return 5;
	}

	// Regression for a real-hands-only path: with the default privacy-safe
	// debug sinks inactive, the keypoint projector must receive nullptr rather
	// than a pointer to its empty debug Mat. This reaches the model's crop and
	// stereographic projection code without a camera or any saved image.
	implementation.debug_scribble = false;
	auto &view = implementation.views[0];
	view.run_model_on_this = blank;
	view.debug_out_to_this = cv::Mat{};
	auto &roi = view.regions_of_interest_this_frame[0];
	roi.provenance = xrt::tracking::hand::mercury::ROIProvenance::HAND_DETECTION;
	roi.center_px = {480.0F, 480.0F};
	roi.size_px = 160.0F;
	roi.found = true;
	roi.hand_detection_confidence = true;
	xrt::tracking::hand::mercury::keypoint_estimation_run_info run_info{};
	run_info.view = &view;
	run_info.hand_idx = false;
	xrt::tracking::hand::mercury::run_keypoint_estimation(&run_info);
	if (!view.debug_out_to_this.empty() || view.keypoint_input_valid[0]) {
		std::fputs("MERCURY_OFFLINE_SELFTEST_FAIL blank_keypoint_diagnostics\n", stderr);
		for (xrt_frame *&frame : frames) {
			xrt_frame_reference(&frame, nullptr);
		}
		t_ht_sync_destroy(&tracker);
		t_stereo_camera_calibration_reference(&calibration, nullptr);
		return 4;
	}

	for (xrt_frame *&frame : frames) {
		xrt_frame_reference(&frame, nullptr);
	}
	t_ht_sync_destroy(&tracker);
	t_stereo_camera_calibration_reference(&calibration, nullptr);

	if (hands[0].is_active || hands[1].is_active) {
		std::fputs("MERCURY_OFFLINE_SELFTEST_FAIL blank_frame_tracked\n", stderr);
		return 2;
	}
	if (output_timestamp_ns == 0) {
		std::fputs("MERCURY_OFFLINE_SELFTEST_FAIL no_timestamp\n", stderr);
		return 3;
	}

	std::puts("MERCURY_OFFLINE_SELFTEST_PASS blank_frames=2 hands=0 keypoint_no_debug_guard=1 detector_diagnostics=1");
	return 0;
}
