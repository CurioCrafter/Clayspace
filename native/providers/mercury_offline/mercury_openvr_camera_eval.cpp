// Explicit-consent, private local evaluator. No default path opens a camera.
// It holds raw pixels only in RAM, clears them on destruction, and logs counts
// rather than image data or joint poses. World output is intentionally absent.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <type_traits>

#include <opencv2/core.hpp>

#include "../openvr-camera-probe/openvr_camera_client.hpp"
#include "hg_interface.h"
#include "hg_sync.hpp"
#include "openvr_mercury_bridge.hpp"
#include "tracking/t_frame_cv_mat_wrapper.hpp"

namespace {

namespace xat = xrt::auxiliary::tracking;
using xrwb::mercury_offline::BridgeStatus;
using xrwb::mercury_offline::OpenVrUndistortedStereoMetadata;
using xrwb::mercury_offline::StereoFramePlan;

constexpr auto kFrameType = EVRTrackedCameraFrameType_VRTrackedCameraFrameType_Undistorted;
constexpr uint32_t kShortMaxFreshFrames = 5;
constexpr uint32_t kDiagnosticMaxFreshFrames = 30;
constexpr auto kDeadline = std::chrono::seconds(15);

struct EvaluationMode {
	const char *name;
	uint32_t max_fresh_frames;
};

constexpr EvaluationMode kShortEvaluation{"short", kShortMaxFreshFrames};
constexpr EvaluationMode kDiagnosticEvaluation{"diagnostic", kDiagnosticMaxFreshFrames};

// These are deliberately aggregate-only: they establish that changing camera
// buffers reached the tracker without serializing any camera or hand content.
struct EvaluationSummary {
	uint32_t fresh_frames = 0;
	uint32_t model_input_pairs = 0;
	uint32_t sync_process_calls = 0;
	uint32_t output_timestamp_matches = 0;
	uint32_t active_hand_observations = 0;
	uint32_t camera_header_pose_valid_frames = 0;
	uint32_t current_hmd_connected_samples = 0;
	uint32_t current_hmd_pose_valid_samples = 0;
	uint32_t current_hmd_tracking_uninitialized = 0;
	uint32_t current_hmd_tracking_calibrating = 0;
	uint32_t current_hmd_tracking_calibrating_out_of_range = 0;
	uint32_t current_hmd_tracking_running_ok = 0;
	uint32_t current_hmd_tracking_running_out_of_range = 0;
	uint32_t current_hmd_tracking_rotation_only = 0;
	uint32_t current_hmd_tracking_other = 0;
	uint32_t current_hmd_activity_idle = 0;
	uint32_t current_hmd_activity_interaction = 0;
	uint32_t current_hmd_activity_interaction_timeout = 0;
	uint32_t current_hmd_activity_standby = 0;
	uint32_t current_hmd_activity_idle_timeout = 0;
	uint32_t current_hmd_activity_unknown = 0;
	uint32_t exposure_changed_frames = 0;
	uint32_t input_nonflat_pairs = 0;
	uint32_t distinct_stereo_pairs = 0;
	uint32_t interval_samples = 0;
	double processing_ms_total = 0.0;
	double interval_ms_total = 0.0;
	double interval_ms_min = std::numeric_limits<double>::infinity();
	double interval_ms_max = 0.0;
};

void
record_current_hmd_state(xrwb::openvr_camera::CameraClient &client, EvaluationSummary *summary)
{
	TrackedDevicePose_t current_hmd_pose{};
	client.system->GetDeviceToAbsoluteTrackingPose(
	    ETrackingUniverseOrigin_TrackingUniverseStanding, 0.0F, &current_hmd_pose, 1);
	summary->current_hmd_connected_samples += static_cast<uint32_t>(current_hmd_pose.bDeviceIsConnected);
	summary->current_hmd_pose_valid_samples += static_cast<uint32_t>(current_hmd_pose.bPoseIsValid);
	switch (current_hmd_pose.eTrackingResult) {
	case ETrackingResult_TrackingResult_Uninitialized: ++summary->current_hmd_tracking_uninitialized; break;
	case ETrackingResult_TrackingResult_Calibrating_InProgress: ++summary->current_hmd_tracking_calibrating; break;
	case ETrackingResult_TrackingResult_Calibrating_OutOfRange:
		++summary->current_hmd_tracking_calibrating_out_of_range;
		break;
	case ETrackingResult_TrackingResult_Running_OK: ++summary->current_hmd_tracking_running_ok; break;
	case ETrackingResult_TrackingResult_Running_OutOfRange:
		++summary->current_hmd_tracking_running_out_of_range;
		break;
	case ETrackingResult_TrackingResult_Fallback_RotationOnly:
		++summary->current_hmd_tracking_rotation_only;
		break;
	default: ++summary->current_hmd_tracking_other; break;
	}
	switch (client.system->GetTrackedDeviceActivityLevel(k_unTrackedDeviceIndex_Hmd)) {
	case EDeviceActivityLevel_k_EDeviceActivityLevel_Idle: ++summary->current_hmd_activity_idle; break;
	case EDeviceActivityLevel_k_EDeviceActivityLevel_UserInteraction:
		++summary->current_hmd_activity_interaction;
		break;
	case EDeviceActivityLevel_k_EDeviceActivityLevel_UserInteraction_Timeout:
		++summary->current_hmd_activity_interaction_timeout;
		break;
	case EDeviceActivityLevel_k_EDeviceActivityLevel_Standby: ++summary->current_hmd_activity_standby; break;
	case EDeviceActivityLevel_k_EDeviceActivityLevel_Idle_Timeout:
		++summary->current_hmd_activity_idle_timeout;
		break;
	default: ++summary->current_hmd_activity_unknown; break;
	}
}

bool
valid_buffer(uint32_t width, uint32_t height, uint32_t bytes)
{
	return width > 0 && height > 0 && width <= 8192 && height <= 8192 &&
	       bytes == static_cast<uint64_t>(width) * height * xrwb::mercury_offline::kRgbaBytesPerPixel &&
	       bytes <= 64 * 1024 * 1024;
}

struct TrackerScope {
	t_hand_tracking_sync *tracker = nullptr;
	t_stereo_camera_calibration *calibration = nullptr;
	TrackerScope() = default;
	TrackerScope(const TrackerScope &) = delete;
	TrackerScope &operator=(const TrackerScope &) = delete;
	TrackerScope(TrackerScope &&other) noexcept : tracker(other.tracker), calibration(other.calibration)
	{
		other.tracker = nullptr;
		other.calibration = nullptr;
	}
	TrackerScope &operator=(TrackerScope &&other) noexcept
	{
		if (this != &other) {
			t_ht_sync_destroy(&tracker);
			t_stereo_camera_calibration_reference(&calibration, nullptr);
			tracker = other.tracker;
			calibration = other.calibration;
			other.tracker = nullptr;
			other.calibration = nullptr;
		}
		return *this;
	}

	~TrackerScope()
	{
		t_ht_sync_destroy(&tracker);
		t_stereo_camera_calibration_reference(&calibration, nullptr);
	}
};

OpenVrUndistortedStereoMetadata
read_metadata(xrwb::openvr_camera::CameraClient &client, uint32_t *out_bytes)
{
	bool camera_present = false;
	client.check(client.camera->HasCamera(0, &camera_present), "HasCamera");
	if (!camera_present) {
		throw std::runtime_error("Headset has no public camera");
	}

	uint32_t width = 0;
	uint32_t height = 0;
	uint32_t bytes = 0;
	client.check(client.camera->GetCameraFrameSize(0, kFrameType, &width, &height, &bytes), "GetCameraFrameSize");
	if (!valid_buffer(width, height, bytes)) {
		throw std::runtime_error("Unexpected undistorted RGBA buffer format");
	}

	ETrackedPropertyError property_error{};
	const int layout = client.system->GetInt32TrackedDeviceProperty(
	    0, ETrackedDeviceProperty_Prop_CameraFrameLayout_Int32, &property_error);
	if (property_error) {
		throw std::runtime_error("Camera layout unavailable");
	}

	OpenVrUndistortedStereoMetadata metadata{};
	metadata.combined_width = width;
	metadata.combined_height = height;
	metadata.bytes_per_pixel = xrwb::mercury_offline::kRgbaBytesPerPixel;
	metadata.layout = static_cast<EVRTrackedCameraFrameLayout>(layout);
	metadata.frame_type = kFrameType;
	const uint32_t transform_bytes = client.system->GetArrayTrackedDeviceProperty(
	    0, ETrackedDeviceProperty_Prop_CameraToHeadTransforms_Matrix34_Array,
	    k_unHmdMatrix34PropertyTag, metadata.camera_to_head, sizeof(metadata.camera_to_head), &property_error);
	if (property_error || transform_bytes != sizeof(metadata.camera_to_head)) {
		throw std::runtime_error("Stereo CameraToHead metadata unavailable");
	}
	for (uint32_t eye = 0; eye < 2; ++eye) {
		HmdVector2_t focal{};
		HmdVector2_t center{};
		client.check(client.camera->GetCameraIntrinsics(0, eye, kFrameType, &focal, &center), "GetCameraIntrinsics");
		metadata.intrinsics[eye] = {focal.v[0], focal.v[1], center.v[0], center.v[1]};
	}
	*out_bytes = bytes;
	return metadata;
}

TrackerScope
create_tracker(const OpenVrUndistortedStereoMetadata &metadata, const StereoFramePlan &plan)
{
	TrackerScope scope{};
	const BridgeStatus calibration_status = xrwb::mercury_offline::make_mercury_calibration(
	    metadata, plan, xrwb::mercury_offline::openvr_from_opencv_optical_basis(), &scope.calibration);
	if (calibration_status != BridgeStatus::Ok) {
		throw std::runtime_error(xrwb::mercury_offline::bridge_status_string(calibration_status));
	}
	t_camera_extra_info extra{};
	for (auto &view : extra.views) {
		view.boundary_type = HT_IMAGE_BOUNDARY_NONE;
		view.camera_orientation = CAMERA_ORIENTATION_0;
	}
	scope.tracker = t_hand_tracking_sync_mercury_create(
	    scope.calibration, extra, MERCURY_MODEL_DIR);
	if (!scope.tracker) {
		throw std::runtime_error("Mercury tracker creation failed");
	}
	return scope;
}

int
run_consented_camera_evaluation(const EvaluationMode &mode)
{
	xrwb::openvr_camera::CameraClient client;
	client.open();
	// This evaluator never changes SteamVR power settings. A separately-owned,
	// bounded keep-awake helper may be run before it by the hardware tester; it
	// outlives an evaluator abort and can therefore restore settings itself.

	uint32_t bytes = 0;
	const OpenVrUndistortedStereoMetadata metadata = read_metadata(client, &bytes);
	StereoFramePlan plan{};
	const BridgeStatus plan_status = xrwb::mercury_offline::make_square_plan(
	    metadata, xrwb::mercury_offline::kMercurySquarePixels, &plan);
	if (plan_status != BridgeStatus::Ok) {
		throw std::runtime_error(xrwb::mercury_offline::bridge_status_string(plan_status));
	}
	TrackerScope tracker = create_tracker(metadata, plan);

	client.check(client.camera->AcquireVideoStreamingService(0, &client.handle), "AcquireVideoStreamingService");
	client.pixels.resize(bytes);

	// A successful construction has initialized two detector and four keypoint
	// ONNX sessions (hg_sync.cpp:init_hand_detection/init_keypoint_estimation).
	// The public sync API does not expose per-model counters, so this evaluator
	// records only calls that completed and their privacy-safe input aggregates.
	const uint32_t model_sessions_initialized = 1;
	EvaluationSummary summary{};
	uint32_t previous_sequence = 0;
	uint64_t previous_exposure = 0;
	bool have_sequence = false;
	bool have_exposure = false;
	std::chrono::steady_clock::time_point previous_fresh_time{};
	bool have_fresh_time = false;
	const auto deadline = std::chrono::steady_clock::now() + kDeadline;
	while (std::chrono::steady_clock::now() < deadline && summary.fresh_frames < mode.max_fresh_frames) {
		CameraVideoStreamFrameHeader_t header{};
		const auto frame_result = client.camera->GetVideoStreamFrameBuffer(
		    client.handle, kFrameType, client.pixels.data(), bytes, &header, sizeof(header));
		if (frame_result == EVRTrackedCameraError_VRTrackedCameraError_NoFrameAvailable) {
			Sleep(5);
			continue;
		}
		client.check(frame_result, "GetVideoStreamFrameBuffer");
		if (header.nWidth != metadata.combined_width || header.nHeight != metadata.combined_height ||
		    header.nBytesPerPixel != xrwb::mercury_offline::kRgbaBytesPerPixel) {
			throw std::runtime_error("Camera format changed during evaluation");
		}
		// Establish a baseline first: a first returned buffer can be an old frame.
		// Only a changed sequence is eligible for Mercury processing.
		if (!have_sequence) {
			have_sequence = true;
			previous_sequence = header.nFrameSequence;
			Sleep(5);
			continue;
		}
		if (header.nFrameSequence == previous_sequence) {
			Sleep(5);
			continue;
		}
		previous_sequence = header.nFrameSequence;
		const auto accepted_at = std::chrono::steady_clock::now();
		if (have_fresh_time) {
			const double interval_ms =
			    std::chrono::duration<double, std::milli>(accepted_at - previous_fresh_time).count();
			summary.interval_ms_total += interval_ms;
			summary.interval_ms_min = std::min(summary.interval_ms_min, interval_ms);
			summary.interval_ms_max = std::max(summary.interval_ms_max, interval_ms);
			++summary.interval_samples;
		}
		previous_fresh_time = accepted_at;
		have_fresh_time = true;
		if (have_exposure && header.ulFrameExposureTime != previous_exposure) {
			++summary.exposure_changed_frames;
		}
		previous_exposure = header.ulFrameExposureTime;
		have_exposure = true;

		void *left_gray = nullptr;
		void *right_gray = nullptr;
		const BridgeStatus frame_status = xrwb::mercury_offline::prepare_latest_rgba_frame(
		    metadata, plan, client.pixels.data(), client.pixels.size(), &left_gray, &right_gray);
		if (frame_status != BridgeStatus::Ok) {
			throw std::runtime_error(xrwb::mercury_offline::bridge_status_string(frame_status));
		}
		xrt_frame *frames[2] = {nullptr, nullptr};
		const uint64_t timestamp_ns = static_cast<uint64_t>(
		    std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());
		xat::FrameMat::Params params{};
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = timestamp_ns;
		xat::FrameMat::wrapL8(*static_cast<cv::Mat *>(left_gray), &frames[0], params);
		xat::FrameMat::wrapL8(*static_cast<cv::Mat *>(right_gray), &frames[1], params);
		const cv::Mat &left = *static_cast<cv::Mat *>(left_gray);
		const cv::Mat &right = *static_cast<cv::Mat *>(right_gray);
		cv::Scalar left_mean{};
		cv::Scalar left_stddev{};
		cv::Scalar right_mean{};
		cv::Scalar right_stddev{};
		cv::meanStdDev(left, left_mean, left_stddev);
		cv::meanStdDev(right, right_mean, right_stddev);
		if (left_stddev[0] > 0.5 && right_stddev[0] > 0.5) {
			++summary.input_nonflat_pairs;
		}
		if (cv::norm(left, right, cv::NORM_L1) > 0.0) {
			++summary.distinct_stereo_pairs;
		}
		xrwb::mercury_offline::release_gray_mat(&left_gray);
		xrwb::mercury_offline::release_gray_mat(&right_gray);

		xrt_hand_joint_set hands[2] = {};
		uint64_t output_timestamp_ns = 0;
		const auto process_begin = std::chrono::steady_clock::now();
		t_ht_sync_process(tracker.tracker, frames[0], frames[1], &hands[0], &hands[1], &output_timestamp_ns);
		summary.processing_ms_total +=
		    std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - process_begin).count();
		++summary.sync_process_calls;
		summary.model_input_pairs++;
		if (output_timestamp_ns == timestamp_ns) {
			++summary.output_timestamp_matches;
		}
		for (xrt_frame *&frame : frames) {
			xrt_frame_reference(&frame, nullptr);
		}
		summary.active_hand_observations +=
		    static_cast<uint32_t>(hands[0].is_active) + static_cast<uint32_t>(hands[1].is_active);
		summary.camera_header_pose_valid_frames += static_cast<uint32_t>(header.trackedDevicePose.bPoseIsValid);
		record_current_hmd_state(client, &summary);
		++summary.fresh_frames;
	}

	const double average_ms =
	    summary.fresh_frames == 0 ? 0.0 : summary.processing_ms_total / summary.fresh_frames;
	const double average_interval_ms =
	    summary.interval_samples == 0 ? 0.0 : summary.interval_ms_total / summary.interval_samples;
	const double min_interval_ms = summary.interval_samples == 0 ? 0.0 : summary.interval_ms_min;
	const auto &diagnostics = xrt::tracking::hand::mercury::HandTracking::fromC(tracker.tracker).diagnostics;
	std::printf(
	    "XR_MERCURY_CAMERA_EVAL source=optical_index_camera mode=%s fresh_frames=%u "
	    "model_sessions_initialized=%u model_input_pairs=%u sync_process_calls=%u output_timestamp_matches=%u "
	    "active_hand_observations=%u camera_header_pose_valid_frames=%u "
	    "current_hmd_connected_samples=%u current_hmd_pose_valid_samples=%u "
	    "hmd_tracking_uninitialized=%u hmd_tracking_calibrating=%u hmd_tracking_calibrating_out_of_range=%u "
	    "hmd_tracking_running_ok=%u hmd_tracking_running_out_of_range=%u hmd_tracking_rotation_only=%u "
	    "hmd_tracking_other=%u hmd_activity_idle=%u hmd_activity_interaction=%u "
	    "hmd_activity_interaction_timeout=%u hmd_activity_standby=%u hmd_activity_idle_timeout=%u "
	    "hmd_activity_unknown=%u "
	    "exposure_changed_frames=%u input_nonflat_pairs=%u distinct_stereo_pairs=%u "
	    "mean_process_ms=%.3f fresh_interval_samples=%u mean_fresh_interval_ms=%.3f "
	    "min_fresh_interval_ms=%.3f max_fresh_interval_ms=%.3f power_override=0 "
	    "detector_view_runs=%u detector_scores_nonfinite=%u detector_scores_over_03=%u "
	    "detector_scores_over_092=%u detector_combined_gate_pass=%u detector_rois_accepted=%u "
	    "keypoint_view_runs=%u keypoint_input_valid=%u keypoint_score_pass=%u keypoint_view_active=%u "
	    "optimizer_reprojection_rejections=%u "
	    "optimizer_distance_rejections=%u world_output=0 images_saved=0 joint_traces_saved=0\n",
	    mode.name, summary.fresh_frames, model_sessions_initialized, summary.model_input_pairs,
	    summary.sync_process_calls, summary.output_timestamp_matches, summary.active_hand_observations,
	    summary.camera_header_pose_valid_frames, summary.current_hmd_connected_samples,
	    summary.current_hmd_pose_valid_samples, summary.current_hmd_tracking_uninitialized,
	    summary.current_hmd_tracking_calibrating, summary.current_hmd_tracking_calibrating_out_of_range,
	    summary.current_hmd_tracking_running_ok, summary.current_hmd_tracking_running_out_of_range,
	    summary.current_hmd_tracking_rotation_only, summary.current_hmd_tracking_other,
	    summary.current_hmd_activity_idle, summary.current_hmd_activity_interaction,
	    summary.current_hmd_activity_interaction_timeout, summary.current_hmd_activity_standby,
	    summary.current_hmd_activity_idle_timeout, summary.current_hmd_activity_unknown,
	    summary.exposure_changed_frames, summary.input_nonflat_pairs, summary.distinct_stereo_pairs, average_ms,
	    summary.interval_samples, average_interval_ms, min_interval_ms, summary.interval_ms_max,
	    diagnostics.detector_view_runs, diagnostics.detector_scores_nonfinite,
	    diagnostics.detector_scores_over_03, diagnostics.detector_scores_over_092,
	    diagnostics.detector_combined_gate_pass, diagnostics.detector_rois_accepted,
	    diagnostics.keypoint_view_runs, diagnostics.keypoint_input_valid,
	    diagnostics.keypoint_score_pass, diagnostics.keypoint_view_active,
	    diagnostics.optimizer_reprojection_rejections, diagnostics.optimizer_distance_rejections);
	if (summary.fresh_frames != mode.max_fresh_frames) {
		throw std::runtime_error("Timed out waiting for the requested number of fresh camera frames");
	}
	return client.close() ? 1 : 0;
}

int
run_self_test()
{
	static_assert(sizeof(TrackedDevicePose_t) == 80, "OpenVR Win64 pose layout changed");
	static_assert(offsetof(TrackedDevicePose_t, eTrackingResult) == 72, "OpenVR pose result offset changed");
	static_assert(offsetof(TrackedDevicePose_t, bPoseIsValid) == 76, "OpenVR pose validity offset changed");
	static_assert(offsetof(TrackedDevicePose_t, bDeviceIsConnected) == 77, "OpenVR pose connection offset changed");
	static_assert(sizeof(CameraVideoStreamFrameHeader_t) == 112, "OpenVR camera header layout changed");
	static_assert(offsetof(CameraVideoStreamFrameHeader_t, trackedDevicePose) == 20,
	              "OpenVR camera pose offset changed");
	static_assert(offsetof(CameraVideoStreamFrameHeader_t, ulFrameExposureTime) == 104,
	              "OpenVR camera exposure offset changed");
	using ExpectedPoseFunction = void(OPENVR_FNTABLE_CALLTYPE *)(
	    ETrackingUniverseOrigin, float, TrackedDevicePose_t *, uint32_t);
	static_assert(std::is_same<decltype(VR_IVRSystem_FnTable::GetDeviceToAbsoluteTrackingPose),
	                           ExpectedPoseFunction>::value,
	              "IVRSystem_026 pose function signature changed");
	const auto basis = xrwb::mercury_offline::openvr_from_opencv_optical_basis();
	if (!basis.independently_validated || basis.openvr_from_optical[0][0] != 1.0 ||
	    basis.openvr_from_optical[1][1] != -1.0 || basis.openvr_from_optical[2][2] != -1.0) {
		std::fputs("MERCURY_OPENVR_EVAL_SELFTEST_FAIL basis\n", stderr);
		return 1;
	}
	std::puts("MERCURY_OPENVR_EVAL_SELFTEST_PASS camera_accessed=0 world_output=0 openvr_capi_win64_abi=1 ivrsystem=026");
	return 0;
}

} // namespace

int
main(int argc, char **argv)
{
	if (argc == 2 && !std::strcmp(argv[1], "--self-test")) {
		return run_self_test();
	}
	if (argc != 2) {
		std::fputs("Explicit local camera-test consent required. No images or joint traces are saved.\n", stderr);
		return 2;
	}
	try {
		if (!std::strcmp(argv[1], "--consented-camera-test")) {
			return run_consented_camera_evaluation(kShortEvaluation);
		}
		if (!std::strcmp(argv[1], "--consented-camera-diagnostic")) {
			return run_consented_camera_evaluation(kDiagnosticEvaluation);
		}
		std::fputs("Explicit local camera-test consent required. No images or joint traces are saved.\n", stderr);
		return 2;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "XR_MERCURY_CAMERA_EVAL_FAIL %s world_output=0\n", error.what());
		return 1;
	}
}
