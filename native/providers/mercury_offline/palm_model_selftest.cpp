// Synthetic-only load/inference check for the local MediaPipe-style ONNX pair.
// No camera, image, crop, landmark, or model output is serialized.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <onnxruntime_cxx_api.h>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include "hand_landmark_probe.hpp"
#include "palm_detector.hpp"

namespace {
void check(bool ok, const char *message)
{
	if (!ok) { throw std::runtime_error(message); }
}

void check_finite(const Ort::Value &value, size_t expected)
{
	const auto shape = value.GetTensorTypeAndShapeInfo();
	check(shape.GetElementType() == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT &&
	      shape.GetElementCount() == expected, "unexpected ONNX output shape/type");
	const float *data = value.GetTensorData<float>();
	for (size_t index = 0; index < expected; ++index) {
		check(std::isfinite(data[index]), "nonfinite ONNX output");
	}
}

void run_model(Ort::Env &env, const char *file, const char *input_name,
               const char *const *output_names, const size_t *output_sizes,
               size_t output_count, int side)
{
	const auto path = std::filesystem::path(MERCURY_MODEL_DIR) / file;
	check(std::filesystem::is_regular_file(path), "local model missing");
	Ort::SessionOptions options;
	options.SetIntraOpNumThreads(1);
	Ort::Session session(env, path.c_str(), options);
	const std::vector<int64_t> dimensions{1, 3, side, side};
	std::vector<float> synthetic(static_cast<size_t>(3 * side * side), 0.5f);
	const auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
	auto input = Ort::Value::CreateTensor<float>(memory, synthetic.data(), synthetic.size(),
	                                          dimensions.data(), dimensions.size());
	const char *input_names[] = {input_name};
	auto outputs = session.Run(Ort::RunOptions{nullptr}, input_names, &input, 1,
	                           output_names, output_count);
	check(outputs.size() == output_count, "unexpected ONNX output count");
	for (size_t index = 0; index < output_count; ++index) {
		check_finite(outputs[index], output_sizes[index]);
	}
}

void check_decoder()
{
	std::vector<float> boxes(896 * 18, 0.0f), logits(896, -100.0f);
	for (int anchor : {0, 1, 512}) { boxes[anchor * 18 + 2] = 4; boxes[anchor * 18 + 3] = 4; }
	logits[0] = 4.0f;
	logits[1] = 3.0f; // Same 16x16 cell: NMS must suppress it.
	logits[512] = 2.0f; // Below the 0.90 sigmoid gate.
	auto result = xrwb::mercury_offline::detail::decode_palm_outputs(
	    boxes.data(), logits.data(), 960, 960, 0, 0, 128.0f / 960.0f);
	check(result.size() == 1, "palm threshold/NMS failed");
	check(std::abs(result[0].box.x - 15.0f) < 0.01f, "left eye-local decode failed");
	check(std::abs(result[0].keypoints[0].x - 30.0f) < 0.01f,
	      "left eye-local wrist decode failed");
	logits[512] = 3.0f;
	result = xrwb::mercury_offline::detail::decode_palm_outputs(
	    boxes.data(), logits.data(), 800, 800, 0, 0, 128.0f / 800.0f);
	check(result.size() == 2, "second anchor group was not decoded");
	check(std::abs(result[0].box.x - 12.5f) < 0.01f, "right eye-local decode failed");
	check(std::abs(result[1].box.x - 37.5f) < 0.01f, "second anchor mapping failed");
}

void screenshot_smoke(const char *path)
{
	const cv::Mat screenshot = cv::imread(path, cv::IMREAD_COLOR);
	check(screenshot.cols == 1449 && screenshot.rows == 620, "unexpected screenshot dimensions");
	// Known viewport bounds in the user-supplied app screenshot: x=195..1394,
	// y=27..619. This deliberately excludes top chrome and left/right UI.
	const cv::Rect left(195, 27, 600, 593), right(795, 27, 600, 593);
	const cv::Rect hand_left(0, 250, 370, 260);
	const cv::Rect hand_right(200, 225, 380, 260);
	xrwb::mercury_offline::PalmDetector detector(
	    std::filesystem::path(MERCURY_MODEL_DIR) / "palm_detection_MEDIAPIPE.onnx");
	xrwb::mercury_offline::HandLandmarkProbe landmarks(
	    std::filesystem::path(MERCURY_MODEL_DIR) / "hand_landmark_MEDIAPIPE.onnx");
	for (const auto &item : {std::pair{"left", left}, std::pair{"right", right}}) {
		for (float gate : {0.0f, 0.5f, 0.7f, 0.9f}) {
			const auto detect_start = std::chrono::steady_clock::now();
			const auto detections = detector.detect(screenshot(item.second), gate);
			const auto detect_end = std::chrono::steady_clock::now();
			const auto hand_region = item.first[0] == 'l' ? hand_left : hand_right;
			int overlap_count = 0;
			int center_count = 0;
			int presence_count = 0;
			int presence_center_count = 0;
			int present_landmarks_in_region = 0;
			float best = 0;
			float best_presence = 0;
			const auto landmark_start = std::chrono::steady_clock::now();
			for (const auto &detection : detections) {
				overlap_count += (detection.box & cv::Rect2f(hand_region)).area() > 0;
				const cv::Point2f center(detection.box.x + detection.box.width * 0.5f,
				                         detection.box.y + detection.box.height * 0.5f);
				center_count += cv::Rect2f(hand_region).contains(center);
				best = std::max(best, detection.score);
				const auto hand = landmarks.infer(screenshot(item.second), detection);
				presence_count += hand.presence >= 0.5f;
				if (hand.presence >= 0.5f) {
					presence_center_count += cv::Rect2f(hand_region).contains(center);
					for (const auto &point : hand.eye_landmarks) {
						present_landmarks_in_region += cv::Rect2f(hand_region).contains(point);
					}
				}
				best_presence = std::max(best_presence, hand.presence);
			}
			const auto landmark_end = std::chrono::steady_clock::now();
			std::printf("OFFLINE_SCREENSHOT_%s gate=%.1f candidates=%zu hand_region_overlap=%d hand_region_center=%d landmark_presence_pass=%d presence_center_in_region=%d present_landmarks_in_region=%d best_palm_score=%.3f best_presence=%.3f\n",
			            item.first, gate, detections.size(), overlap_count, center_count,
			            presence_count, presence_center_count, present_landmarks_in_region, best, best_presence);
			if (gate == 0.5f) {
				const auto ms = [](auto start, auto end) {
					return std::chrono::duration<double, std::milli>(end - start).count();
				};
				std::printf("OFFLINE_TIMING_%s palm_ms=%.3f landmark_total_ms=%.3f candidates=%zu\n",
				            item.first, ms(detect_start, detect_end), ms(landmark_start, landmark_end), detections.size());
			}
		}
	}
	for (const auto &item : {std::pair{"left", left}, std::pair{"right", right}}) {
		// Ceiling/light-only strip above the visible fingertips.
		const cv::Rect upper(item.second.x, item.second.y, item.second.width, 130);
		const auto detections = detector.detect(screenshot(upper), 0.5f);
		int presence_count = 0;
		for (const auto &detection : detections) {
			presence_count += landmarks.infer(screenshot(upper), detection).presence >= 0.5f;
		}
		std::printf("OFFLINE_NEGATIVE_CEILING_%s palm_candidates=%zu landmark_presence_pass=%d\n",
		            item.first, detections.size(), presence_count);
	}
}
} // namespace

int main(int argc, char **argv)
{
	try {
		if (argc == 3 && std::string(argv[1]) == "--known-user-screenshot") {
			screenshot_smoke(argv[2]);
			return 0;
		}
		check(argc == 1, "usage: mercury_palm_model_selftest [--known-user-screenshot path]");
		check_decoder();
		Ort::Env env(ORT_LOGGING_LEVEL_ERROR, "offline_palm_test");
		const char *palm_outputs[] = {"regressors", "classificators"};
		const size_t palm_sizes[] = {896 * 18, 896};
		run_model(env, "palm_detection_MEDIAPIPE.onnx", "input", palm_outputs,
		          palm_sizes, 2, 128);
		const char *landmark_outputs[] = {"Identity", "Identity_1", "Identity_2"};
		const size_t landmark_sizes[] = {63, 1, 1};
		run_model(env, "hand_landmark_MEDIAPIPE.onnx", "input_1", landmark_outputs,
		          landmark_sizes, 3, 224);
		xrwb::mercury_offline::PalmDetector detector(
		    std::filesystem::path(MERCURY_MODEL_DIR) / "palm_detection_MEDIAPIPE.onnx");
		cv::Mat blank(960, 960, CV_8UC3, cv::Scalar(0, 0, 0));
		const auto detections = detector.detect(blank);
		check(detections.empty(), "blank frame yielded a palm candidate");
		xrwb::mercury_offline::HandLandmarkProbe landmark_probe(
		    std::filesystem::path(MERCURY_MODEL_DIR) / "hand_landmark_MEDIAPIPE.onnx");
		xrwb::mercury_offline::PalmDetection synthetic_palm{};
		synthetic_palm.raw_box = cv::Rect2f(440, 440, 80, 80);
		synthetic_palm.keypoints[0] = {480, 520};
		synthetic_palm.keypoints[2] = {480, 440};
		const auto synthetic_hand = landmark_probe.infer(blank, synthetic_palm);
		check(std::isfinite(synthetic_hand.presence), "nonfinite synthetic hand presence");
		check(synthetic_hand.presence < 0.5f, "blank synthetic crop passed hand-presence gate");
		std::puts("MERCURY_PALM_MODEL_SELFTEST_PASS synthetic_only=1 palm_anchors=896 landmarks=21 camera_accessed=0");
		return 0;
	} catch (const std::exception &error) {
		std::fprintf(stderr, "MERCURY_PALM_MODEL_SELFTEST_FAIL %s\n", error.what());
		return 1;
	}
}
