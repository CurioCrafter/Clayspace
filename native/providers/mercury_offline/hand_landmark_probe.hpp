#pragma once

#include <array>
#include <filesystem>

#include "palm_detector.hpp"

namespace xrwb::mercury_offline {

struct HandLandmarkResult {
	float presence = 0;
	std::array<cv::Point2f, 21> eye_landmarks{};
};

// Private offline probe only. A high presence score is not a validated hand outline.
class HandLandmarkProbe {
public:
	explicit HandLandmarkProbe(const std::filesystem::path &model_path);
	HandLandmarkResult infer(const cv::Mat &eye_bgr, const PalmDetection &palm);

private:
	Ort::Env env_{ORT_LOGGING_LEVEL_ERROR, "offline_hand_landmark"};
	Ort::Session session_{nullptr};
};

} // namespace xrwb::mercury_offline
