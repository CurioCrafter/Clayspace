#include "hand_landmark_probe.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <vector>

#include <opencv2/imgproc.hpp>

namespace xrwb::mercury_offline {
namespace {
constexpr int kSide = 224;
constexpr float kPi = 3.14159265358979323846f;
}

HandLandmarkProbe::HandLandmarkProbe(const std::filesystem::path &model_path)
{
	if (!std::filesystem::is_regular_file(model_path)) { throw std::runtime_error("hand landmark model missing"); }
	Ort::SessionOptions options;
	options.SetIntraOpNumThreads(1);
	session_ = Ort::Session(env_, model_path.c_str(), options);
}

HandLandmarkResult HandLandmarkProbe::infer(const cv::Mat &eye_bgr, const PalmDetection &palm)
{
	if (eye_bgr.empty() || eye_bgr.type() != CV_8UC3 || palm.raw_box.width <= 0 ||
	    palm.raw_box.height <= 0) { throw std::invalid_argument("invalid palm landmark input"); }
	const cv::Point2f wrist = palm.keypoints[0];
	const cv::Point2f middle = palm.keypoints[2];
	if (!std::isfinite(wrist.x) || !std::isfinite(wrist.y) ||
	    !std::isfinite(middle.x) || !std::isfinite(middle.y)) {
		throw std::invalid_argument("nonfinite palm orientation keypoints");
	}
	const float rotation = kPi * 0.5f - std::atan2(-(middle.y - wrist.y), middle.x - wrist.x);
	if (!std::isfinite(rotation)) { throw std::invalid_argument("invalid palm orientation"); }
	const float c = std::cos(rotation), s = std::sin(rotation);
	const float palm_side = std::max(palm.raw_box.width, palm.raw_box.height);
	if (!std::isfinite(palm_side) || palm_side > 4.0f * std::max(eye_bgr.cols, eye_bgr.rows)) {
		throw std::invalid_argument("invalid palm ROI scale");
	}
	const cv::Point2f palm_center(palm.raw_box.x + palm.raw_box.width * 0.5f,
	                              palm.raw_box.y + palm.raw_box.height * 0.5f);
	// Matches MediaPipe's rect transform: shift by -0.5 * original height in
	// rotated local Y, then square_long and scale both axes by 2.6.
	const cv::Point2f center = palm_center + cv::Point2f(0.5f * palm.raw_box.height * s,
	                                                  -0.5f * palm.raw_box.height * c);
	const float half = 1.3f * palm_side;
	const auto rotated = [&](float x, float y) {
		return center + cv::Point2f(c * x - s * y, s * x + c * y);
	};
	const cv::Point2f source[3] = {rotated(-half, -half), rotated(half, -half), rotated(-half, half)};
	const cv::Point2f target[3] = {{0, 0}, {kSide - 1.0f, 0}, {0, kSide - 1.0f}};
	const cv::Mat affine = cv::getAffineTransform(source, target);
	cv::Mat crop, rgb;
	cv::warpAffine(eye_bgr, crop, affine, cv::Size(kSide, kSide), cv::INTER_LINEAR,
	               cv::BORDER_CONSTANT, cv::Scalar(0, 0, 0));
	cv::cvtColor(crop, rgb, cv::COLOR_BGR2RGB);
	std::vector<float> pixels(3 * kSide * kSide);
	for (int y = 0; y < kSide; ++y) {
		for (int x = 0; x < kSide; ++x) {
			const cv::Vec3b color = rgb.at<cv::Vec3b>(y, x);
			for (int channel = 0; channel < 3; ++channel) {
				pixels[channel * kSide * kSide + y * kSide + x] = color[channel] / 255.0f;
			}
		}
	}
	const std::array<int64_t, 4> shape{1, 3, kSide, kSide};
	const auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
	auto input = Ort::Value::CreateTensor<float>(memory, pixels.data(), pixels.size(), shape.data(), shape.size());
	const char *inputs[] = {"input_1"};
	const char *outputs[] = {"Identity", "Identity_1"};
	auto values = session_.Run(Ort::RunOptions{nullptr}, inputs, &input, 1, outputs, 2);
	if (values[0].GetTensorTypeAndShapeInfo().GetElementCount() != 63 ||
	    values[1].GetTensorTypeAndShapeInfo().GetElementCount() != 1) {
		throw std::runtime_error("unexpected landmark model output");
	}
	HandLandmarkResult result;
	result.presence = values[1].GetTensorData<float>()[0];
	if (!std::isfinite(result.presence)) { throw std::runtime_error("nonfinite hand presence"); }
	const float *coordinates = values[0].GetTensorData<float>();
	cv::Mat inverse;
	cv::invertAffineTransform(affine, inverse);
	for (int i = 0; i < 21; ++i) {
		const float x = coordinates[i * 3], y = coordinates[i * 3 + 1];
		if (!std::isfinite(x) || !std::isfinite(y)) { throw std::runtime_error("nonfinite hand landmark"); }
		result.eye_landmarks[i] = {
		    float(inverse.at<double>(0, 0) * x + inverse.at<double>(0, 1) * y + inverse.at<double>(0, 2)),
		    float(inverse.at<double>(1, 0) * x + inverse.at<double>(1, 1) * y + inverse.at<double>(1, 2))};
	}
	return result;
}

} // namespace xrwb::mercury_offline
