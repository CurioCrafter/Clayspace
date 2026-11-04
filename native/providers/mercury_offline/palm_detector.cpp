#include "palm_detector.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

#include <opencv2/imgproc.hpp>

namespace xrwb::mercury_offline {
namespace {
constexpr int kInput = 128;
constexpr int kAnchors = 896;

float overlap(const cv::Rect2f &a, const cv::Rect2f &b)
{
	const float intersection = (a & b).area();
	return intersection / (a.area() + b.area() - intersection);
}

std::array<cv::Point2f, kAnchors> make_anchors()
{
	std::array<cv::Point2f, kAnchors> anchors{};
	int index = 0;
	// The 128x128 / 896-anchor MediaPipe palm variant groups equal-stride
	// layers: 2 anchors per 16x16 cell, then 6 per 8x8 cell.
	for (int grid : {16, 8}) {
		const int repeats = grid == 16 ? 2 : 6;
		for (int y = 0; y < grid; ++y) {
			for (int x = 0; x < grid; ++x) {
				for (int repeat = 0; repeat < repeats; ++repeat) {
					anchors[index++] = {(x + 0.5f) / grid, (y + 0.5f) / grid};
				}
			}
		}
	}
	if (index != kAnchors) { throw std::logic_error("palm anchor count"); }
	return anchors;
}
} // namespace

PalmDetector::PalmDetector(const std::filesystem::path &model_path)
{
	if (!std::filesystem::is_regular_file(model_path)) { throw std::runtime_error("palm model missing"); }
	Ort::SessionOptions options;
	options.SetIntraOpNumThreads(1);
	session_ = Ort::Session(env_, model_path.c_str(), options);
}

std::vector<PalmDetection> detail::decode_palm_outputs(const float *boxes, const float *scores,
                                                        int eye_width, int eye_height,
                                                        int pad_x, int pad_y, float scale,
                                                        float min_score)
{
	if (!boxes || !scores || eye_width <= 0 || eye_height <= 0 || !std::isfinite(scale) || scale <= 0 ||
	    !std::isfinite(min_score) || min_score < 0 || min_score > 1) {
		throw std::invalid_argument("invalid palm decode arguments");
	}
	static const auto anchors = make_anchors();
	std::vector<PalmDetection> candidates;
	for (int i = 0; i < kAnchors; ++i) {
		const float raw = scores[i];
		if (!std::isfinite(raw)) { continue; }
		const float score = 1.0f / (1.0f + std::exp(-std::clamp(raw, -100.0f, 100.0f)));
		if (score < min_score) { continue; }
		const float *box = boxes + i * 18;
		if (!std::isfinite(box[0]) || !std::isfinite(box[1]) ||
		    !std::isfinite(box[2]) || !std::isfinite(box[3]) || box[2] <= 0 || box[3] <= 0) { continue; }
		const float center_x = ((anchors[i].x + box[0] / kInput) * kInput - pad_x) / scale;
		const float center_y = ((anchors[i].y + box[1] / kInput) * kInput - pad_y) / scale;
		const float box_width = box[2] / scale;
		const float box_height = box[3] / scale;
		const cv::Rect2f decoded(center_x - box_width / 2, center_y - box_height / 2,
		                         box_width, box_height);
		const cv::Rect2f clipped = decoded & cv::Rect2f(0, 0, float(eye_width), float(eye_height));
		if (clipped.area() > 0) {
			std::array<cv::Point2f, 7> keypoints;
			bool valid = true;
			for (int kp = 0; kp < 7; ++kp) {
				const float x = ((anchors[i].x + box[4 + kp * 2] / kInput) * kInput - pad_x) / scale;
				const float y = ((anchors[i].y + box[5 + kp * 2] / kInput) * kInput - pad_y) / scale;
				valid &= std::isfinite(x) && std::isfinite(y);
				keypoints[kp] = {x, y};
			}
			if (valid) { candidates.push_back({score, clipped, decoded, keypoints}); }
		}
	}
	std::sort(candidates.begin(), candidates.end(), [](const auto &a, const auto &b) { return a.score > b.score; });
	std::vector<PalmDetection> accepted;
	for (const auto &candidate : candidates) {
		bool duplicate = false;
		for (const auto &other : accepted) { duplicate |= overlap(candidate.box, other.box) > 0.3f; }
		if (!duplicate) { accepted.push_back(candidate); }
		if (accepted.size() == 2) { break; }
	}
	return accepted;
}

std::vector<PalmDetection> PalmDetector::detect(const cv::Mat &eye_bgr, float min_score)
{
	if (eye_bgr.empty() || eye_bgr.type() != CV_8UC3) { throw std::invalid_argument("expected BGR8 eye image"); }
	const float scale = std::min(float(kInput) / eye_bgr.cols, float(kInput) / eye_bgr.rows);
	const int width = std::max(1, int(std::round(eye_bgr.cols * scale)));
	const int height = std::max(1, int(std::round(eye_bgr.rows * scale)));
	const int pad_x = (kInput - width) / 2;
	const int pad_y = (kInput - height) / 2;
	cv::Mat resized, rgb;
	cv::resize(eye_bgr, resized, cv::Size(width, height), 0, 0, cv::INTER_LINEAR);
	cv::cvtColor(resized, rgb, cv::COLOR_BGR2RGB);
	std::vector<float> pixels(3 * kInput * kInput, 0.0f);
	for (int y = 0; y < height; ++y) {
		for (int x = 0; x < width; ++x) {
			const cv::Vec3b color = rgb.at<cv::Vec3b>(y, x);
			for (int channel = 0; channel < 3; ++channel) {
				pixels[channel * kInput * kInput + (y + pad_y) * kInput + x + pad_x] = color[channel] / 255.0f;
			}
		}
	}
	const std::array<int64_t, 4> shape{1, 3, kInput, kInput};
	const auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
	auto input = Ort::Value::CreateTensor<float>(memory, pixels.data(), pixels.size(), shape.data(), shape.size());
	const char *inputs[] = {"input"};
	const char *outputs[] = {"regressors", "classificators"};
	auto values = session_.Run(Ort::RunOptions{nullptr}, inputs, &input, 1, outputs, 2);
	if (values[0].GetTensorTypeAndShapeInfo().GetElementCount() != kAnchors * 18 ||
	    values[1].GetTensorTypeAndShapeInfo().GetElementCount() != kAnchors) {
		throw std::runtime_error("unexpected palm model output");
	}
	return detail::decode_palm_outputs(values[0].GetTensorData<float>(), values[1].GetTensorData<float>(),
	                                   eye_bgr.cols, eye_bgr.rows, pad_x, pad_y, scale, min_score);
}
} // namespace xrwb::mercury_offline
