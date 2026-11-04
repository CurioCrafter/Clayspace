#pragma once

#include <array>
#include <filesystem>
#include <vector>

#include <opencv2/core.hpp>
#include <onnxruntime_cxx_api.h>

namespace xrwb::mercury_offline {

struct PalmDetection {
	float score;
	cv::Rect2f box; // Eye-local pixels; palm candidate, not a full-hand outline.
	cv::Rect2f raw_box; // Before image clipping; required for the oriented hand ROI.
	std::array<cv::Point2f, 7> keypoints; // Eye-local palm keypoints.
};

namespace detail {
// Pure model-output decode, exposed for synthetic geometry/gating regression.
std::vector<PalmDetection> decode_palm_outputs(const float *boxes, const float *logits,
                                               int eye_width, int eye_height,
                                               int pad_x, int pad_y, float scale,
                                               float min_score = 0.90f);
}

class PalmDetector {
public:
	explicit PalmDetector(const std::filesystem::path &model_path);
	std::vector<PalmDetection> detect(const cv::Mat &eye_bgr, float min_score = 0.90f);

private:
	Ort::Env env_{ORT_LOGGING_LEVEL_ERROR, "offline_palm"};
	Ort::Session session_{nullptr};
};

} // namespace xrwb::mercury_offline
