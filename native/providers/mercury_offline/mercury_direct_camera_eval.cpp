// Private, bounded DirectShow-to-Mercury diagnostic. No OpenVR calls, frames,
// regions, or joints are written to disk or printed.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <TlHelp32.h>

#include <chrono>
#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <exception>
#include <filesystem>
#include <fcntl.h>
#include <future>
#include <io.h>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/highgui.hpp>
#include <opencv2/highgui/highgui_c.h>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include "hand_landmark_probe.hpp"
#include "hg_interface.h"
#include "hg_sync.hpp"
#include "palm_detector.hpp"
#include "tracking/t_frame_cv_mat_wrapper.hpp"

namespace xrt::tracking::hand::mercury {
void predict_new_regions_of_interest(HandTracking *hgt);
}

namespace {
namespace xat = xrt::auxiliary::tracking;
namespace mercury = xrt::tracking::hand::mercury;
namespace offline = xrwb::mercury_offline;
constexpr int kWidth = 1920;
constexpr int kHeight = 960;
constexpr int kEyeWidth = kWidth / 2;
constexpr int kMaxFrames = 30;
constexpr auto kDeadline = std::chrono::seconds(15);
constexpr auto kStereoPacketDeadline = std::chrono::minutes(5);
const cv::Scalar kOutlineBlue(210, 105, 25);
constexpr uint64_t kLegacyMaxSourceAgeNs = 500'000'000ULL;

bool legacy_source_frame_fresh(uint64_t capture_ns, uint64_t now_ns)
{
	return now_ns >= capture_ns && now_ns - capture_ns <= kLegacyMaxSourceAgeNs;
}
static_assert(std::chrono::steady_clock::is_steady, "Aggregate timing requires a monotonic clock");

int preview_remaining_seconds(std::chrono::steady_clock::time_point now,
                              std::chrono::steady_clock::time_point deadline)
{
	const auto remaining_ms = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count();
	return static_cast<int>(std::clamp<int64_t>((remaining_ms + 999) / 1000, 0, kDeadline.count()));
}

void draw_preview_countdown(cv::Mat &stereo, int seconds)
{
	if (seconds < 1 || seconds > 5 || stereo.type() != CV_8UC3 ||
	    stereo.cols != kWidth || stereo.rows != kHeight) { return; }
	const std::string label = "Preview ends in " + std::to_string(seconds) + "s";
	constexpr double font_scale = 1.3;
	constexpr int thickness = 3;
	int baseline = 0;
	const cv::Size text = cv::getTextSize(label, cv::FONT_HERSHEY_SIMPLEX, font_scale, thickness, &baseline);
	for (int eye = 0; eye < 2; ++eye) {
		const int center_x = eye * kEyeWidth + kEyeWidth / 2;
		const cv::Rect backdrop(center_x - text.width / 2 - 28, 78,
		                        text.width + 56, text.height + baseline + 42);
		cv::rectangle(stereo, backdrop, cv::Scalar(12, 12, 12), cv::FILLED);
		cv::putText(stereo, label, cv::Point(center_x - text.width / 2, 78 + text.height + 20),
		            cv::FONT_HERSHEY_SIMPLEX, font_scale, cv::Scalar(220, 220, 220), thickness, cv::LINE_AA);
	}
}

cv::Rect centered_preview_rect(cv::Size source, cv::Size target)
{
	if (source.width <= 0 || source.height <= 0 || target.width <= 0 || target.height <= 0) {
		throw std::runtime_error("Invalid headset preview dimensions");
	}
	const double scale = std::min(double(target.width) / source.width, double(target.height) / source.height);
	const int width = std::min(target.width, cvRound(source.width * scale));
	const int height = std::min(target.height, cvRound(source.height * scale));
	return cv::Rect((target.width - width) / 2, (target.height - height) / 2, width, height);
}

void split_index_frame(cv::Mat &both, cv::Mat &left, cv::Mat &right)
{
	// Match Mercury's active Index path: grayscale, left/right crop, orientation 0.
	// The vertical flip in attic/camera_demo.cpp was from an older capture path.
	const int eye_width = both.cols / 2;
	left = both(cv::Rect(0, 0, eye_width, both.rows));
	right = both(cv::Rect(eye_width, 0, eye_width, both.rows));
}

bool outline_validated_region(cv::Mat &stereo_bgr, const mercury::hand_region_of_interest &region, int view,
                              const std::vector<cv::Rect> &contours = {})
{
	if (!region.found || (region.provenance != mercury::ROIProvenance::HAND_DETECTION &&
	                      region.provenance != mercury::ROIProvenance::POSE_PREDICTION) ||
	    (region.provenance == mercury::ROIProvenance::HAND_DETECTION && !region.hand_detection_confidence) ||
	    !std::isfinite(region.center_px.x) ||
	    !std::isfinite(region.center_px.y) || !std::isfinite(region.size_px) ||
	    region.size_px <= 0 || region.size_px > 2 * stereo_bgr.rows) { return false; }
	const int eye_width = stereo_bgr.cols / 2;
	const int half = cvRound(region.size_px / 2);
	const cv::Rect bounds(view * eye_width, 0, eye_width, stereo_bgr.rows);
	const cv::Rect box(cvRound(region.center_px.x) + bounds.x - half,
	                   cvRound(region.center_px.y) - half, 2 * half, 2 * half);
	const cv::Rect visible = box & bounds;
	if (visible.empty()) { return false; }
	for (const auto &contour : contours) {
		if ((visible & contour).area() > 0.25 * std::min(visible.area(), contour.area())) { return false; }
	}
	cv::rectangle(stereo_bgr, visible, kOutlineBlue, 2);
	return true;
}

bool estimated_hand_contour(const offline::PalmDetection &palm, const offline::HandLandmarkResult &hand,
                            cv::Size eye_size, std::vector<cv::Point> &contour)
{
	contour.clear();
	const float eye_area = float(eye_size.area());
	if (!std::isfinite(palm.score) || palm.score < 0.5f || !std::isfinite(hand.presence) ||
	    hand.presence < 0.95f || !std::isfinite(palm.raw_box.x) || !std::isfinite(palm.raw_box.y) ||
	    !std::isfinite(palm.raw_box.width) || !std::isfinite(palm.raw_box.height) ||
	    !std::isfinite(palm.box.x) || !std::isfinite(palm.box.y) ||
	    !std::isfinite(palm.box.width) || !std::isfinite(palm.box.height) ||
	    palm.raw_box.width <= 0 || palm.raw_box.height <= 0 ||
	    palm.raw_box.width > eye_size.width * 0.6f || palm.raw_box.height > eye_size.height * 0.6f ||
	    palm.box.width <= 0 || palm.box.height <= 0 || palm.box.x < 0 || palm.box.y < 0 ||
	    palm.box.br().x > eye_size.width || palm.box.br().y > eye_size.height ||
	    palm.box.area() < palm.raw_box.area() * 0.6f) { return false; }
	std::vector<cv::Point2f> points;
	points.reserve(hand.eye_landmarks.size());
	for (const auto &point : hand.eye_landmarks) {
		if (!std::isfinite(point.x) || !std::isfinite(point.y) || point.x < 0 || point.y < 0 ||
		    point.x >= eye_size.width || point.y >= eye_size.height) { return false; }
		points.push_back(point);
	}
	std::vector<cv::Point2f> hull;
	cv::convexHull(points, hull);
	const double area = cv::contourArea(hull);
	const cv::Rect bounds = cv::boundingRect(hull);
	if (area < eye_area * 0.0005f || area > eye_area * 0.55f ||
	    bounds.width < 12 || bounds.height < 12 ||
	    bounds.width > eye_size.width * 0.85f || bounds.height > eye_size.height * 0.85f ||
	    std::max(bounds.width, bounds.height) > 5 * std::min(bounds.width, bounds.height)) { return false; }
	const cv::Point2f center = cv::Point2f(bounds.x + bounds.width * 0.5f,
	                                       bounds.y + bounds.height * 0.5f);
	for (const auto &point : hull) {
		const cv::Point2f expanded = center + (point - center) * 1.08f;
		contour.emplace_back(std::clamp(cvRound(expanded.x), 0, eye_size.width - 1),
		                     std::clamp(cvRound(expanded.y), 0, eye_size.height - 1));
	}
	return contour.size() >= 3;
}

std::vector<std::vector<cv::Point>> estimate_eye_contours(const cv::Mat &eye,
                                                          offline::PalmDetector &palm_detector,
                                                          offline::HandLandmarkProbe &hand_landmarks)
{
	std::vector<std::vector<cv::Point>> contours;
	const auto palms = palm_detector.detect(eye, 0.5f);
	for (size_t i = 0; i < std::min<size_t>(palms.size(), 2); ++i) {
		const auto landmarks = hand_landmarks.infer(eye, palms[i]);
		std::vector<cv::Point> contour;
		if (estimated_hand_contour(palms[i], landmarks, eye.size(), contour)) {
			contours.push_back(std::move(contour));
		}
	}
	return contours;
}

bool steamvr_running()
{
	const wchar_t *names[] = {L"vrserver.exe", L"vrcompositor.exe", L"vrmonitor.exe",
	                          L"vrwebhelper.exe", L"steamvr.exe", L"vrstartup.exe"};
	HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
	if (snapshot == INVALID_HANDLE_VALUE) { throw std::runtime_error("SteamVR process guard unavailable"); }
	PROCESSENTRY32W entry{};
	entry.dwSize = sizeof(entry);
	bool found = false;
	if (!Process32FirstW(snapshot, &entry)) {
		CloseHandle(snapshot);
		throw std::runtime_error("SteamVR process enumeration failed");
	}
	do {
		for (const wchar_t *name : names) { found |= _wcsicmp(entry.szExeFile, name) == 0; }
	} while (!found && Process32NextW(snapshot, &entry));
	CloseHandle(snapshot);
	return found;
}

void require_steamvr_stopped()
{
	if (steamvr_running()) { throw std::runtime_error("SteamVR process detected; direct diagnostic aborted"); }
}

struct HeadsetMonitor {
	HMONITOR handle = nullptr;
	RECT bounds{};
};

bool is_index_monitor_id(const wchar_t *id)
{
	// Compare the complete EDID hardware-ID component, not a friendly name or substring.
	constexpr const wchar_t *prefixes[] = {L"MONITOR\\VLV91A8", L"DISPLAY\\VLV91A8",
	                                      L"\\\\?\\DISPLAY#VLV91A8"};
	for (const wchar_t *prefix : prefixes) {
		const size_t length = std::wcslen(prefix);
		if (_wcsnicmp(id, prefix, length) == 0 &&
		    (id[length] == L'\0' || id[length] == L'\\' || id[length] == L'#')) { return true; }
	}
	return false;
}

bool exclusive_index_output(DWORD active_children, DWORD active_index_children)
{
	return active_children == 1 && active_index_children == 1;
}

bool index_monitor_id_self_test()
{
	return is_index_monitor_id(L"MONITOR\\VLV91A8\\123") &&
	       is_index_monitor_id(L"\\\\?\\DISPLAY#VLV91A8#123") &&
	       !is_index_monitor_id(L"MONITOR\\GSM5B8E\\123") &&
	       !is_index_monitor_id(L"MONITOR\\VLV91A80\\123") &&
	       !is_index_monitor_id(L"OTHER\\VLV91A8\\123") &&
	       exclusive_index_output(1, 1) && !exclusive_index_output(2, 1) &&
	       !exclusive_index_output(1, 0) && !exclusive_index_output(2, 2);
}

struct MonitorScan {
	std::vector<HeadsetMonitor> matches;
	bool failed = false;
	bool cloned_output = false;
	DWORD index_identities = 0;
};

BOOL CALLBACK scan_index_monitor(HMONITOR handle, HDC, LPRECT, LPARAM context)
{
	auto &scan = *reinterpret_cast<MonitorScan *>(context);
	MONITORINFOEXW info{};
	info.cbSize = sizeof(info);
	if (!GetMonitorInfoW(handle, reinterpret_cast<MONITORINFO *>(&info))) {
		scan.failed = true;
		return FALSE;
	}
	DWORD active_children = 0;
	DWORD active_index_children = 0;
	for (DWORD index = 0;; ++index) {
		DISPLAY_DEVICEW child{};
		child.cb = sizeof(child);
		if (!EnumDisplayDevicesW(info.szDevice, index, &child, 0)) { break; }
		const bool index_identity = is_index_monitor_id(child.DeviceID);
		scan.index_identities += index_identity;
		if (child.StateFlags & DISPLAY_DEVICE_ACTIVE) {
			++active_children;
			active_index_children += index_identity;
		}
	}
	if (active_index_children) {
		if (exclusive_index_output(active_children, active_index_children)) {
			scan.matches.push_back({handle, info.rcMonitor});
		} else { scan.cloned_output = true; }
	}
	return TRUE;
}

HeadsetMonitor select_index_monitor()
{
	MonitorScan scan;
	if (!EnumDisplayMonitors(nullptr, nullptr, scan_index_monitor, reinterpret_cast<LPARAM>(&scan)) ||
	    scan.failed) { throw std::runtime_error("Active display enumeration failed"); }
	if (scan.cloned_output) { throw std::runtime_error("Index shares a cloned display; camera not opened"); }
	if (scan.index_identities > 1) { throw std::runtime_error("Ambiguous Index display identities; camera not opened"); }
	if (scan.matches.size() != 1) {
		throw std::runtime_error(scan.matches.empty() ? "No active Index VLV91A8 display; camera not opened"
		                                            : "Ambiguous active Index displays; camera not opened");
	}
	const RECT &bounds = scan.matches.front().bounds;
	if (bounds.right <= bounds.left || bounds.bottom <= bounds.top) {
		throw std::runtime_error("Invalid Index display bounds; camera not opened");
	}
	return scan.matches.front();
}

void verify_headset_window(const char *name, HMONITOR expected, const RECT &bounds)
{
	HWND window = static_cast<HWND>(cvGetWindowHandle(name));
	HWND frame = window ? GetAncestor(window, GA_ROOT) : nullptr;
	RECT actual{};
	if (!frame || MonitorFromWindow(frame, MONITOR_DEFAULTTONULL) != expected ||
	    !GetWindowRect(frame, &actual) || actual.left != bounds.left || actual.top != bounds.top ||
	    actual.right != bounds.right || actual.bottom != bounds.bottom) {
		throw std::runtime_error("Headset preview did not fill the Index display; camera not opened");
	}
}

void verify_live_index_window(const char *name, const HeadsetMonitor &target)
{
	const auto current = select_index_monitor();
	if (current.handle != target.handle || !EqualRect(&current.bounds, &target.bounds)) {
		throw std::runtime_error("Index display topology changed; preview aborted");
	}
	verify_headset_window(name, target.handle, target.bounds);
}

void open_headset_window(const char *name, const HeadsetMonitor &target)
{
	const int width = target.bounds.right - target.bounds.left;
	const int height = target.bounds.bottom - target.bounds.top;
	cv::namedWindow(name, cv::WINDOW_NORMAL);
	try {
		cv::resizeWindow(name, width / 2, height / 2);
		cv::moveWindow(name, target.bounds.left + width / 4, target.bounds.top + height / 4);
		cv::waitKey(1);
		cv::setWindowProperty(name, cv::WND_PROP_FULLSCREEN, cv::WINDOW_FULLSCREEN);
		cv::imshow(name, cv::Mat(height, width, CV_8UC3, cv::Scalar(0)));
		cv::waitKey(1);
		verify_live_index_window(name, target);
		HWND frame = GetAncestor(static_cast<HWND>(cvGetWindowHandle(name)), GA_ROOT);
		if (!SetWindowPos(frame, HWND_TOPMOST, 0, 0, 0, 0,
		                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)) {
			throw std::runtime_error("Could not raise the Index preview above other windows");
		}
		verify_live_index_window(name, target);
	} catch (...) {
		try { cv::destroyWindow(name); } catch (...) {}
		throw;
	}
}

int headset_test_pattern()
{
	require_steamvr_stopped();
	const auto target = select_index_monitor();
	constexpr const char *window = "Index display test - no camera";
	open_headset_window(window, target);
	cv::Mat image(target.bounds.bottom - target.bounds.top,
	              target.bounds.right - target.bounds.left, CV_8UC3, cv::Scalar(6, 6, 6));
	const int midpoint = image.cols / 2;
	const int pad_x = midpoint / 5;
	const int pad_y = image.rows / 5;
	const cv::Scalar test_blue(110, 50, 12);
	const int thickness = std::max(12, std::min(midpoint, image.rows) / 60);
	const cv::Point left_a(pad_x, pad_y);
	const cv::Point left_b(midpoint - pad_x, image.rows - pad_y);
	cv::line(image, left_a, left_b, test_blue, thickness, cv::LINE_AA);
	cv::line(image, cv::Point(left_b.x, left_a.y), cv::Point(left_a.x, left_b.y),
	         test_blue, thickness, cv::LINE_AA);
	cv::rectangle(image, cv::Rect(midpoint + pad_x, pad_y,
	                              midpoint - 2 * pad_x, image.rows - 2 * pad_y),
	              test_blue, thickness, cv::LINE_AA);
	cv::imshow(window, image);
	cv::waitKey(20000);
	cv::destroyWindow(window);
	std::puts("XR_INDEX_DISPLAY_TEST_PASS pattern=left-X/right-box camera_accessed=0 images_saved=0 steamvr=0");
	return 0;
}

void set_identity(double matrix[3][3])
{
	for (int row = 0; row < 3; ++row) {
		for (int col = 0; col < 3; ++col) { matrix[row][col] = row == col ? 1.0 : 0.0; }
	}
}

// Deliberate detector-only fixture. DirectShow YUY2 is not proven equivalent
// to OpenVR's undistorted stream, so these parameters cannot qualify joints.
t_stereo_camera_calibration *make_diagnostic_fixture()
{
	t_stereo_camera_calibration *calibration = nullptr;
	t_stereo_camera_calibration_alloc(&calibration, T_DISTORTION_FISHEYE_KB4);
	for (auto &camera : calibration->view) {
		camera.image_size_pixels.w = kEyeWidth;
		camera.image_size_pixels.h = kHeight;
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

struct Tracker {
	t_stereo_camera_calibration *calibration = make_diagnostic_fixture();
	t_hand_tracking_sync *sync = nullptr;
	Tracker()
	{
		t_camera_extra_info extra{};
		for (auto &view : extra.views) {
			view.boundary_type = HT_IMAGE_BOUNDARY_NONE;
			view.camera_orientation = CAMERA_ORIENTATION_0;
		}
		sync = t_hand_tracking_sync_mercury_create(calibration, extra, MERCURY_MODEL_DIR);
		if (!sync) {
			t_stereo_camera_calibration_reference(&calibration, nullptr);
			throw std::runtime_error("Mercury tracker creation failed");
		}
	}
	~Tracker()
	{
		t_ht_sync_destroy(&sync);
		t_stereo_camera_calibration_reference(&calibration, nullptr);
	}
};

std::wstring ffmpeg_command(const std::wstring &ffmpeg, const std::wstring &camera, bool preview)
{
	std::wstring args = L"\"" + ffmpeg + L"\" -nostdin -hide_banner -loglevel error -f dshow "
	                    L"-rtbufsize 16M -video_size 1920x960 -pixel_format yuyv422 -framerate 30 "
	                    L"-i \"video=" + camera + L"\" ";
	args += preview ? L"-pix_fmt bgr24 -f rawvideo pipe:1"
	                : L"-frames:v 30 -t 15 -pix_fmt gray -f rawvideo pipe:1";
	return args;
}

struct Child {
	HANDLE read = nullptr;
	HANDLE error_read = nullptr;
	HANDLE job = nullptr;
	PROCESS_INFORMATION process{};
	char error_text[513]{};
	size_t error_used = 0;
	Child(const std::wstring &application, std::wstring args)
	{
		SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
		HANDLE write = nullptr;
		HANDLE error_write = nullptr;
		// A sub-frame pipe buffer reduces 4 KiB-sized handoffs without queuing an entire stereo frame.
		if (!CreatePipe(&read, &write, &security, 64 * 1024)) {
			throw std::runtime_error("FFmpeg pipe creation failed");
		}
		try {
			if (!CreatePipe(&error_read, &error_write, &security, 0) ||
			    !SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0) ||
			    !SetHandleInformation(error_read, HANDLE_FLAG_INHERIT, 0) ||
			    !(job = CreateJobObjectW(nullptr, nullptr))) {
				throw std::runtime_error("FFmpeg pipe or job guard failed");
			}
			JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
			limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
			if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
				throw std::runtime_error("FFmpeg job kill-on-close setup failed");
			}
			STARTUPINFOW startup{};
			startup.cb = sizeof(startup);
			startup.dwFlags = STARTF_USESTDHANDLES;
			startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
			startup.hStdOutput = write;
			startup.hStdError = error_write;
			if (!CreateProcessW(application.c_str(), args.data(), nullptr, nullptr, TRUE,
			                    CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr, nullptr, &startup, &process)) {
				throw std::runtime_error("FFmpeg launch failed");
			}
			if (!AssignProcessToJobObject(job, process.hProcess) || ResumeThread(process.hThread) == DWORD(-1)) {
				throw std::runtime_error("FFmpeg job assignment failed");
			}
			CloseHandle(write);
			CloseHandle(error_write);
		} catch (...) {
			if (process.hProcess) {
				TerminateProcess(process.hProcess, 1);
				CloseHandle(process.hThread);
				CloseHandle(process.hProcess);
			}
			if (job) { CloseHandle(job); }
			CloseHandle(write);
			if (error_write) { CloseHandle(error_write); }
			if (error_read) { CloseHandle(error_read); }
			if (read) { CloseHandle(read); }
			throw;
		}
	}
	Child(const std::wstring &ffmpeg, const std::wstring &camera, bool preview)
	    : Child(ffmpeg, ffmpeg_command(ffmpeg, camera, preview)) {}
	~Child()
	{
		if (process.hProcess) {
			if (WaitForSingleObject(process.hProcess, 0) == WAIT_TIMEOUT) {
				TerminateProcess(process.hProcess, 1);
				WaitForSingleObject(process.hProcess, 3000);
			}
			CloseHandle(process.hThread);
			CloseHandle(process.hProcess);
		}
		if (read) { CloseHandle(read); }
		if (error_read) { CloseHandle(error_read); }
		if (job) { CloseHandle(job); }
		SecureZeroMemory(error_text, sizeof(error_text));
	}
};

void drain_child_error(Child &child)
{
	char chunk[1024];
	DWORD budget = 4096;
	DWORD available = 0;
	while (budget && PeekNamedPipe(child.error_read, nullptr, 0, nullptr, &available, nullptr) && available) {
		DWORD count = 0;
		const DWORD wanted = std::min<DWORD>(available, std::min<DWORD>(sizeof(chunk), budget));
		if (!ReadFile(child.error_read, chunk, wanted, &count, nullptr) || !count) { break; }
		budget -= count;
		for (DWORD i = 0; i < count && child.error_used < sizeof(child.error_text) - 1; ++i) {
			const unsigned char c = static_cast<unsigned char>(chunk[i]);
			child.error_text[child.error_used++] = c == '\r' || c == '\n' || c == '\t' ? ' '
			                                   : c >= 32 && c <= 126 ? static_cast<char>(c) : '?';
		}
	}
	SecureZeroMemory(chunk, sizeof(chunk));
}

void report_child_failure(Child &child)
{
	// Error text only, never raw camera pixels. Bound and flatten it for console diagnostics.
	if (WaitForSingleObject(child.process.hProcess, 1000) == WAIT_TIMEOUT) { return; }
	DWORD exit_code = 0;
	if (!GetExitCodeProcess(child.process.hProcess, &exit_code)) { return; }
	drain_child_error(child);
	std::fprintf(stderr, "XR_FFMPEG_EXIT code=%lu stderr=%.*s\n", exit_code,
	             static_cast<int>(child.error_used), child.error_text);
}

bool read_frame(Child &child, std::vector<uint8_t> &pixels,
                std::chrono::steady_clock::time_point deadline, uint64_t &empty_polls,
                const std::atomic_bool *cancelled = nullptr)
{
	size_t received = 0;
	auto next_guard = std::chrono::steady_clock::now();
	auto last_progress = next_guard;
	while (received < pixels.size() && std::chrono::steady_clock::now() < deadline &&
	       !(cancelled && cancelled->load())) {
		drain_child_error(child);
		if (std::chrono::steady_clock::now() >= next_guard) {
			require_steamvr_stopped();
			next_guard = std::chrono::steady_clock::now() + std::chrono::milliseconds(100);
		}
		DWORD available = 0;
		if (!PeekNamedPipe(child.read, nullptr, 0, nullptr, &available, nullptr)) { return false; }
		// Avoid a timer delay between flowing pipe chunks, then back off if stalled.
		if (!available) {
			++empty_polls;
			Sleep(received && std::chrono::steady_clock::now() - last_progress < std::chrono::milliseconds(10)
			          ? 0 : 2);
			continue;
		}
		DWORD count = 0;
		const DWORD wanted = static_cast<DWORD>(std::min<size_t>(available, pixels.size() - received));
		if (!ReadFile(child.read, pixels.data() + received, wanted, &count, nullptr) || count == 0) {
			return false;
		}
		received += count;
		last_progress = std::chrono::steady_clock::now();
	}
	return received == pixels.size();
}

struct FramePrefetch {
	Child &child;
	std::vector<uint8_t> queued;
	std::atomic_bool cancelled{false};
	std::future<bool> pending;
	FramePrefetch(Child &child, size_t bytes) : child(child), queued(bytes) {}
	void start(std::chrono::steady_clock::time_point deadline, uint64_t &empty_polls)
	{
		pending = std::async(std::launch::async, [this, deadline, &empty_polls] {
			return read_frame(child, queued, deadline, empty_polls, &cancelled);
		});
	}
	bool take(std::vector<uint8_t> &pixels)
	{
		const bool ready = pending.get();
		if (ready) { pixels.swap(queued); }
		return ready;
	}
	void stop() noexcept
	{
		cancelled.store(true);
		if (pending.valid()) { pending.wait(); }
		if (!queued.empty()) { SecureZeroMemory(queued.data(), queued.size()); }
	}
	~FramePrefetch() { stop(); }
};

// The image consumer may run slower than DirectShow or palm inference. Drain
// whole raw frames continuously and expose only the newest complete one; no
// partially read image can reach the renderer and no frame queue can grow.
struct LatestFrameReader {
	Child &child;
	std::vector<uint8_t> receiving;
	std::vector<uint8_t> latest;
	std::mutex mutex;
	std::condition_variable changed;
	std::thread worker;
	std::atomic_bool cancelled{false};
	std::chrono::steady_clock::time_point hard_deadline;
	uint64_t empty_polls = 0;
	uint64_t received_count = 0;
	uint64_t taken_count = 0;
	uint64_t dropped_count = 0;
	uint64_t latest_timestamp_ns = 0;
	bool ended = false;
	std::exception_ptr failure;
	LatestFrameReader(Child &source, size_t bytes, std::chrono::steady_clock::time_point deadline)
	    : child(source), receiving(bytes), latest(bytes), hard_deadline(deadline) {}
	void start()
	{
		worker = std::thread([this] {
			try {
				bool first = true;
				while (!cancelled.load()) {
					const auto idle_deadline = std::chrono::steady_clock::now() +
					                           std::chrono::seconds(first ? 15 : 3);
					const auto read_deadline = std::min(idle_deadline, hard_deadline);
					if (!read_frame(child, receiving, read_deadline, empty_polls, &cancelled)) { break; }
					first = false;
					const uint64_t timestamp_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
					    std::chrono::steady_clock::now().time_since_epoch()).count());
					{
						std::lock_guard<std::mutex> lock(mutex);
						receiving.swap(latest);
						++received_count;
						latest_timestamp_ns = timestamp_ns;
					}
					changed.notify_one();
				}
			} catch (...) {
				std::lock_guard<std::mutex> lock(mutex);
				failure = std::current_exception();
			}
			{
				std::lock_guard<std::mutex> lock(mutex);
				ended = true;
			}
			changed.notify_all();
		});
	}
	bool take(std::vector<uint8_t> &pixels, uint64_t &timestamp_ns,
	          std::chrono::steady_clock::time_point deadline)
	{
		std::unique_lock<std::mutex> lock(mutex);
		changed.wait_until(lock, deadline, [this] { return received_count > taken_count || ended; });
		if (received_count == taken_count) {
			if (failure) { std::rethrow_exception(failure); }
			return false;
		}
		if (received_count > taken_count + 1) { dropped_count += received_count - taken_count - 1; }
		taken_count = received_count;
		timestamp_ns = latest_timestamp_ns;
		pixels.swap(latest);
		return true;
	}
	void stop() noexcept
	{
		cancelled.store(true);
		if (worker.joinable()) { worker.join(); }
		if (!receiving.empty()) { SecureZeroMemory(receiving.data(), receiving.size()); }
		if (!latest.empty()) { SecureZeroMemory(latest.data(), latest.size()); }
	}
	~LatestFrameReader() { stop(); }
};

int run(const std::wstring &camera, const std::wstring &ffmpeg)
{
	require_steamvr_stopped();
	if (camera.empty() || camera.find_first_not_of(L"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ -") !=
	                          std::wstring::npos || ffmpeg.empty()) {
		throw std::runtime_error("Invalid DirectShow camera or FFmpeg path");
	}
	Tracker tracker;
	Child child(ffmpeg, camera, false);
	std::vector<uint8_t> pixels(static_cast<size_t>(kWidth) * kHeight);
	const auto deadline = std::chrono::steady_clock::now() + kDeadline;
	uint32_t frames = 0, nonflat = 0, distinct = 0, active = 0;
	uint64_t empty_polls = 0;
	double read_ms = 0.0, first_frame_read_ms = 0.0, completed_read_ms = 0.0, process_ms = 0.0;
	double gray_mean[2]{}, gray_stddev[2]{};
	double center_mean[2]{}, center_stddev[2]{};
	while (frames < kMaxFrames) {
		const auto read_begin = std::chrono::steady_clock::now();
		const bool frame_ready = read_frame(child, pixels, deadline, empty_polls);
		const double frame_read_ms = std::chrono::duration<double, std::milli>(
		    std::chrono::steady_clock::now() - read_begin).count();
		read_ms += frame_read_ms;
		if (!frame_ready) {
			report_child_failure(child);
			break;
		}
		if (frames == 0) { first_frame_read_ms = frame_read_ms; }
		else { completed_read_ms += frame_read_ms; }
		cv::Mat both(kHeight, kWidth, CV_8UC1, pixels.data());
		cv::Mat left, right;
		split_index_frame(both, left, right);
		cv::Scalar left_mean, left_dev, right_mean, right_dev;
		cv::meanStdDev(left, left_mean, left_dev);
		cv::meanStdDev(right, right_mean, right_dev);
		gray_mean[0] += left_mean[0];
		gray_mean[1] += right_mean[0];
		gray_stddev[0] += left_dev[0];
		gray_stddev[1] += right_dev[0];
		const cv::Rect center(kEyeWidth / 4, kHeight / 4, kEyeWidth / 2, kHeight / 2);
		cv::Scalar left_center_mean, left_center_dev, right_center_mean, right_center_dev;
		cv::meanStdDev(left(center), left_center_mean, left_center_dev);
		cv::meanStdDev(right(center), right_center_mean, right_center_dev);
		center_mean[0] += left_center_mean[0];
		center_mean[1] += right_center_mean[0];
		center_stddev[0] += left_center_dev[0];
		center_stddev[1] += right_center_dev[0];
		nonflat += left_dev[0] > 0.5 && right_dev[0] > 0.5;
		distinct += cv::norm(left, right, cv::NORM_L1) > 0.0;
		xrt_frame *wrapped[2] = {nullptr, nullptr};
		xat::FrameMat::Params params{};
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
		    std::chrono::steady_clock::now().time_since_epoch()).count());
		xat::FrameMat::wrapL8(left, &wrapped[0], params);
		xat::FrameMat::wrapL8(right, &wrapped[1], params);
		xrt_hand_joint_set hands[2]{};
		uint64_t output_timestamp = 0;
		const auto process_begin = std::chrono::steady_clock::now();
		t_ht_sync_process(tracker.sync, wrapped[0], wrapped[1], &hands[0], &hands[1], &output_timestamp);
		process_ms += std::chrono::duration<double, std::milli>(
		    std::chrono::steady_clock::now() - process_begin).count();
		active += hands[0].is_active + hands[1].is_active;
		for (auto *&frame : wrapped) { xrt_frame_reference(&frame, nullptr); }
		SecureZeroMemory(pixels.data(), pixels.size());
		++frames;
	}
	const auto &d = mercury::HandTracking::fromC(tracker.sync).diagnostics;
	std::printf("XR_MERCURY_DIRECT_EVAL frames=%u nonflat_pairs=%u distinct_stereo_pairs=%u "
	            "gray_left_mean=%.2f gray_right_mean=%.2f gray_left_stddev=%.2f gray_right_stddev=%.2f "
	            "center_left_mean=%.2f center_right_mean=%.2f "
	            "center_left_stddev=%.2f center_right_stddev=%.2f "
	            "first_frame_read_ms=%.3f later_frame_read_mean_ms=%.3f pipe_empty_polls=%llu "
	            "read_total_ms=%.3f process_total_ms=%.3f process_mean_ms=%.3f "
	            "active_hand_observations=%u detector_view_runs=%u detector_scores_nonfinite=%u "
	            "detector_scores_over_03=%u detector_scores_over_092=%u detector_combined_gate_pass=%u "
	            "detector_rois_accepted=%u keypoint_view_runs=%u keypoint_input_valid=%u "
	            "keypoint_score_pass=%u keypoint_view_active=%u optimizer_reprojection_rejections=%u "
	            "optimizer_distance_rejections=%u steamvr=0 calibration=synthetic_unverified "
	            "world_output=0 images_saved=0 joint_traces_saved=0\n",
	            frames, nonflat, distinct,
	            frames ? gray_mean[0] / frames : 0.0, frames ? gray_mean[1] / frames : 0.0,
	            frames ? gray_stddev[0] / frames : 0.0, frames ? gray_stddev[1] / frames : 0.0,
	            frames ? center_mean[0] / frames : 0.0, frames ? center_mean[1] / frames : 0.0,
	            frames ? center_stddev[0] / frames : 0.0, frames ? center_stddev[1] / frames : 0.0,
	            first_frame_read_ms, frames > 1 ? completed_read_ms / (frames - 1) : 0.0,
	            static_cast<unsigned long long>(empty_polls), read_ms, process_ms,
	            frames ? process_ms / frames : 0.0, active, d.detector_view_runs, d.detector_scores_nonfinite,
	            d.detector_scores_over_03, d.detector_scores_over_092, d.detector_combined_gate_pass,
	            d.detector_rois_accepted, d.keypoint_view_runs, d.keypoint_input_valid, d.keypoint_score_pass,
	            d.keypoint_view_active, d.optimizer_reprojection_rejections, d.optimizer_distance_rejections);
	SecureZeroMemory(pixels.data(), pixels.size());
	return frames == kMaxFrames ? 0 : 1;
}

void write_stereo_rgba(const cv::Mat &stereo, const std::array<uint32_t, 2> &contours)
{
	const uint32_t header[] = {0x42575258u, 1u, kEyeWidth, kHeight, contours[0], contours[1]};
	if (std::fwrite(header, sizeof(header), 1, stdout) != 1) { throw std::runtime_error("Stream header write failed"); }
	for (int view = 0; view < 2; ++view) {
		cv::Mat rgba;
		cv::cvtColor(stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight)), rgba, cv::COLOR_BGR2RGBA);
		const size_t bytes = rgba.total() * rgba.elemSize();
		if (!rgba.isContinuous() || std::fwrite(rgba.data, 1, bytes, stdout) != bytes) {
			throw std::runtime_error("Stream eye write failed");
		}
	}
	if (std::fflush(stdout)) { throw std::runtime_error("Stream flush failed"); }
}

struct GestureRecord {
	uint32_t view, candidate;
	float presence, palm_score;
	std::array<float, 42> eye_xy{};
};

// XRWM v2 is a single-capture, image-local video+gesture packet. All integers
// and IEEE-754 floats are little-endian; no padding or native structs go on the wire.
// Header: 16 u32 words followed by capture/publish monotonic nanoseconds (u64 each).
// Payload: left RGBA, right RGBA, then 0..4 XRWG-compatible 184-byte records.
constexpr uint32_t kStereoPacketMagic = 0x4d575258u; // "XRWM"
constexpr uint32_t kStereoPacketVersion = 2;
constexpr uint32_t kStereoPacketHeaderBytes = 80;
constexpr uint32_t kStereoPacketRecordBytes = 184;
constexpr uint32_t kStereoPacketFlags = 1u | 2u | 4u; // image-local, top-down, blue contours

void append_u32_le(std::vector<uint8_t> &bytes, uint32_t value)
{
	for (int shift = 0; shift < 32; shift += 8) { bytes.push_back(uint8_t(value >> shift)); }
}

void append_u64_le(std::vector<uint8_t> &bytes, uint64_t value)
{
	for (int shift = 0; shift < 64; shift += 8) { bytes.push_back(uint8_t(value >> shift)); }
}

void append_f32_le(std::vector<uint8_t> &bytes, float value)
{
	static_assert(sizeof(value) == sizeof(uint32_t), "XRWM requires 32-bit floats");
	uint32_t bits;
	std::memcpy(&bits, &value, sizeof(bits));
	append_u32_le(bytes, bits);
}

struct StereoPacketStats {
	uint32_t contours[2]{};
	uint32_t presence_low = 0;  // nonfinite or < 0.80
	uint32_t presence_mid = 0;  // [0.80, 0.95)
	uint32_t presence_high = 0; // >= 0.95
};

// A blocked binary-pipe write cannot be canceled by the read deadline. Keep
// this new camera mode process-bounded even if its consumer stops draining.
struct StereoPacketDeadline {
	std::atomic_bool finished{false};
	std::thread worker;
	explicit StereoPacketDeadline(bool enabled)
	{
		if (!enabled) { return; }
		worker = std::thread([this] {
			const auto deadline = std::chrono::steady_clock::now() + kStereoPacketDeadline +
			                      std::chrono::seconds(7);
			while (!finished.load() && std::chrono::steady_clock::now() < deadline) { Sleep(50); }
			if (!finished.load()) {
				// Closing this process also closes the FFmpeg kill-on-close job handle.
				TerminateProcess(GetCurrentProcess(), 124);
			}
		});
	}
	~StereoPacketDeadline()
	{
		finished.store(true);
		if (worker.joinable()) { worker.join(); }
	}
};

std::vector<uint8_t> stereo_packet_header(uint32_t sequence, uint64_t capture_ns,
                                         uint64_t publish_ns, const StereoPacketStats &stats,
                                         size_t gesture_count)
{
	if (gesture_count > 4 || publish_ns < capture_ns) {
		throw std::runtime_error("Invalid stereo packet metadata");
	}
	const uint64_t age_ms = (publish_ns - capture_ns) / 1'000'000ULL;
	std::vector<uint8_t> bytes;
	bytes.reserve(kStereoPacketHeaderBytes);
	for (uint32_t word : {kStereoPacketMagic, kStereoPacketVersion, kStereoPacketHeaderBytes,
	                      uint32_t(kEyeWidth), uint32_t(kHeight), sequence, kStereoPacketFlags,
	                      uint32_t(kEyeWidth * kHeight * 4), uint32_t(gesture_count),
	                      stats.contours[0], stats.contours[1], stats.presence_low,
	                      stats.presence_mid, stats.presence_high,
	                      uint32_t(std::min<uint64_t>(age_ms, UINT32_MAX)), 0u}) {
		append_u32_le(bytes, word);
	}
	append_u64_le(bytes, capture_ns);
	append_u64_le(bytes, publish_ns);
	if (bytes.size() != kStereoPacketHeaderBytes) { throw std::runtime_error("Stereo packet header size mismatch"); }
	return bytes;
}

std::vector<uint8_t> stereo_packet_record(const GestureRecord &record)
{
	std::vector<uint8_t> bytes;
	bytes.reserve(kStereoPacketRecordBytes);
	append_u32_le(bytes, record.view);
	append_u32_le(bytes, record.candidate);
	append_f32_le(bytes, record.presence);
	append_f32_le(bytes, record.palm_score);
	for (float point : record.eye_xy) { append_f32_le(bytes, point); }
	if (bytes.size() != kStereoPacketRecordBytes) { throw std::runtime_error("Stereo packet record size mismatch"); }
	return bytes;
}

void write_stereo_packet(const cv::Mat &stereo, uint32_t sequence, uint64_t capture_ns,
                         const StereoPacketStats &stats, const std::vector<GestureRecord> &records)
{
	if (stereo.cols != kWidth || stereo.rows != kHeight || stereo.type() != CV_8UC3) {
		throw std::runtime_error("Stereo packet dimensions or format invalid");
	}
	std::array<cv::Mat, 2> eyes;
	for (int view = 0; view < 2; ++view) {
		cv::cvtColor(stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight)),
		             eyes[view], cv::COLOR_BGR2RGBA);
		if (!eyes[view].isContinuous() || eyes[view].total() * eyes[view].elemSize() !=
		    size_t(kEyeWidth * kHeight * 4)) {
			throw std::runtime_error("Stereo packet eye conversion failed");
		}
	}
	const uint64_t publish_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
	    std::chrono::steady_clock::now().time_since_epoch()).count());
	const auto header = stereo_packet_header(sequence, capture_ns, publish_ns, stats, records.size());
	if (std::fwrite(header.data(), 1, header.size(), stdout) != header.size()) {
		throw std::runtime_error("Stereo packet header write failed");
	}
	for (int view = 0; view < 2; ++view) {
		const size_t count = eyes[view].total() * eyes[view].elemSize();
		if (std::fwrite(eyes[view].data, 1, count, stdout) != count) {
			throw std::runtime_error("Stereo packet eye write failed");
		}
		SecureZeroMemory(eyes[view].data, count);
	}
	for (const GestureRecord &record : records) {
		const auto bytes = stereo_packet_record(record);
		if (std::fwrite(bytes.data(), 1, bytes.size(), stdout) != bytes.size()) {
			throw std::runtime_error("Stereo packet gesture write failed");
		}
	}
	if (std::fflush(stdout)) { throw std::runtime_error("Stereo packet flush failed"); }
}

// XRWG v1: nine little-endian u32 words, then 4+4+4+4+42*4 bytes per record.
// Coordinates are eye-local pixels; world_output is always zero.
void write_gesture_frame(uint32_t sequence, uint64_t timestamp_ns, const std::vector<GestureRecord> &records)
{
	const uint32_t header[] = {0x47575258u, 1u, kEyeWidth, kHeight, sequence,
	                           uint32_t(timestamp_ns), uint32_t(timestamp_ns >> 32),
	                           uint32_t(records.size()), 0u};
	if (records.size() > 4 || std::fwrite(header, sizeof(header), 1, stdout) != 1) {
		throw std::runtime_error("Gesture header write failed");
	}
	for (const auto &record : records) {
		const uint32_t identity[] = {record.view, record.candidate};
		const float scores[] = {record.presence, record.palm_score};
		if (std::fwrite(identity, sizeof(identity), 1, stdout) != 1 ||
		    std::fwrite(scores, sizeof(scores), 1, stdout) != 1 ||
		    std::fwrite(record.eye_xy.data(), sizeof(float), record.eye_xy.size(), stdout) != record.eye_xy.size()) {
			throw std::runtime_error("Gesture record write failed");
		}
	}
	if (std::fflush(stdout)) { throw std::runtime_error("Gesture flush failed"); }
}

int run_preview(const std::wstring &camera, const std::wstring &ffmpeg, bool stream = false,
                bool gesture = false, bool gesture_preview = false, bool headset_preview = false,
                bool packet = false)
{
	StereoPacketDeadline packet_deadline(packet);
	require_steamvr_stopped();
	if (camera.empty() || camera.find_first_not_of(L"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ -") !=
	                          std::wstring::npos || ffmpeg.empty()) {
		throw std::runtime_error("Invalid DirectShow camera or FFmpeg path");
	}
	const HeadsetMonitor target = headset_preview ? select_index_monitor() : HeadsetMonitor{};
	Tracker tracker;
	auto &mercury_tracker = mercury::HandTracking::fromC(tracker.sync);
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	offline::PalmDetector palm_detector(model_dir / "palm_detection_MEDIAPIPE.onnx");
	offline::HandLandmarkProbe hand_landmarks(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	std::unique_ptr<offline::PalmDetector> headset_right_palm;
	std::unique_ptr<offline::HandLandmarkProbe> headset_right_landmarks;
	if (headset_preview || stream) {
		headset_right_palm = std::make_unique<offline::PalmDetector>(model_dir / "palm_detection_MEDIAPIPE.onnx");
		headset_right_landmarks = std::make_unique<offline::HandLandmarkProbe>(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	}
	std::vector<uint8_t> pixels(static_cast<size_t>(kWidth) * kHeight * 3);
	constexpr const char *window = "Index stereo LIVE - no recording (Esc/Q to close)";
	const bool show_window = headset_preview || (!stream && (!gesture || gesture_preview));
	if (stream || gesture || packet) {
		if (_setmode(_fileno(stdout), _O_BINARY) == -1) { throw std::runtime_error("Binary stdout unavailable"); }
	}
	if (show_window) {
		if (headset_preview) { open_headset_window(window, target); }
		else {
			cv::namedWindow(window, cv::WINDOW_NORMAL);
			cv::resizeWindow(window, 1280, 640);
		}
	}
	cv::Mat headset_image;
	cv::Mat headset_view;
	if (headset_preview) {
		headset_image = cv::Mat(target.bounds.bottom - target.bounds.top,
		                        target.bounds.right - target.bounds.left, CV_8UC3, cv::Scalar(0));
		headset_view = headset_image(centered_preview_rect(cv::Size(kWidth, kHeight), headset_image.size()));
	}
	// Camera access begins only after the Index target and fullscreen placement pass.
	require_steamvr_stopped();
	Child child(ffmpeg, camera, true);
	const auto headset_deadline = std::chrono::steady_clock::now() +
	                              (packet ? kStereoPacketDeadline : kDeadline);
	LatestFrameReader latest_reader(child, (stream || packet) ? pixels.size() : 0,
	    packet ? headset_deadline : std::chrono::steady_clock::time_point::max());
	if (stream || packet) { latest_reader.start(); }
	uint32_t frames = 0, mercury_regions = 0, estimated_contours = 0, late_frame_drops = 0;
	uint64_t empty_polls = 0;
	bool stream_failed = false;
	enum class PreviewEnd { None, Timed, UserClose };
	PreviewEnd preview_end = PreviewEnd::None;
	// One frame is read while the prior frame is processed; no unbounded frame queue.
	FramePrefetch prefetch(child, headset_preview ? pixels.size() : 0);
	if (headset_preview) { prefetch.start(headset_deadline, empty_polls); }
	while ((!headset_preview && !packet) || std::chrono::steady_clock::now() < headset_deadline) {
		const auto read_deadline = (headset_preview || packet) ? headset_deadline :
		    std::chrono::steady_clock::now() + std::chrono::seconds(frames ? 3 : 15);
		uint64_t capture_ns = 0;
		const bool frame_ready = (stream || packet) ? latest_reader.take(pixels, capture_ns, read_deadline) :
		                         headset_preview ? prefetch.take(pixels) :
		                         read_frame(child, pixels, read_deadline, empty_polls);
		if (!frame_ready) {
			if (stream || packet) { latest_reader.stop(); }
			if ((!headset_preview && !packet) || std::chrono::steady_clock::now() < headset_deadline) { stream_failed = true; }
			else { preview_end = PreviewEnd::Timed; }
			if (stream_failed) { report_child_failure(child); }
			break;
		}
		if (headset_preview) {
			prefetch.start(std::min(std::chrono::steady_clock::now() + std::chrono::seconds(3),
			                        headset_deadline), empty_polls);
		}
		if (!stream && !packet) {
			capture_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
			    std::chrono::steady_clock::now().time_since_epoch()).count());
		}
		cv::Mat stereo(kHeight, kWidth, CV_8UC3, pixels.data());
		cv::Mat gray, left, right;
		if (!gesture) {
			cv::cvtColor(stereo, gray, cv::COLOR_BGR2GRAY);
			split_index_frame(gray, left, right);
		}
		xat::FrameMat::Params params{};
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = capture_ns;
		if (!gesture) {
			xrt_frame *wrapped[2] = {nullptr, nullptr};
			xat::FrameMat::wrapL8(left, &wrapped[0], params);
			xat::FrameMat::wrapL8(right, &wrapped[1], params);
			xrt_hand_joint_set hands[2]{};
			uint64_t output_timestamp = 0;
			t_ht_sync_process(tracker.sync, wrapped[0], wrapped[1], &hands[0], &hands[1], &output_timestamp);
			for (auto *&frame : wrapped) { xrt_frame_reference(&frame, nullptr); }
		}
		std::array<std::vector<cv::Rect>, 2> contour_bounds;
		std::array<uint32_t, 2> frame_contours{};
		std::vector<GestureRecord> gesture_records;
		StereoPacketStats packet_stats{};
		bool estimated = false;
		if ((headset_preview || stream) && !gesture) {
			const cv::Mat left_eye = stereo(cv::Rect(0, 0, kEyeWidth, kHeight));
			const cv::Mat right_eye = stereo(cv::Rect(kEyeWidth, 0, kEyeWidth, kHeight));
			auto left_task = std::async(std::launch::async, [&] {
				return estimate_eye_contours(left_eye, palm_detector, hand_landmarks);
			});
			std::array<std::vector<std::vector<cv::Point>>, 2> eye_contours;
			eye_contours[1] = estimate_eye_contours(right_eye, *headset_right_palm, *headset_right_landmarks);
			eye_contours[0] = left_task.get();
			for (int view = 0; view < 2; ++view) {
				for (auto &contour : eye_contours[view]) {
					for (auto &point : contour) { point.x += view * kEyeWidth; }
					contour_bounds[view].push_back(cv::boundingRect(contour));
					cv::polylines(stereo, contour, true, kOutlineBlue, 1, cv::LINE_AA);
					estimated = true;
					++estimated_contours;
					++frame_contours[view];
				}
			}
		} else {
			for (int view = 0; view < 2; ++view) {
				cv::Mat eye = stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight));
				const auto palms = palm_detector.detect(eye, 0.5f);
				for (size_t i = 0; i < std::min<size_t>(palms.size(), 2); ++i) {
					const auto landmarks = hand_landmarks.infer(eye, palms[i]);
					if (!std::isfinite(landmarks.presence) || landmarks.presence < 0.8f) {
						++packet_stats.presence_low;
					} else if (landmarks.presence < 0.95f) {
						++packet_stats.presence_mid;
					} else {
						++packet_stats.presence_high;
					}
					std::vector<cv::Point> contour;
					if (!estimated_hand_contour(palms[i], landmarks, eye.size(), contour)) { continue; }
					if (gesture) {
						GestureRecord record{uint32_t(view), uint32_t(i), landmarks.presence, palms[i].score};
						for (size_t point = 0; point < landmarks.eye_landmarks.size(); ++point) {
							record.eye_xy[2 * point] = landmarks.eye_landmarks[point].x;
							record.eye_xy[2 * point + 1] = landmarks.eye_landmarks[point].y;
						}
						gesture_records.push_back(record);
					}
					for (auto &point : contour) { point.x += view * kEyeWidth; }
					contour_bounds[view].push_back(cv::boundingRect(contour));
					cv::polylines(stereo, contour, true, kOutlineBlue, 1, cv::LINE_AA);
					estimated = true;
					++estimated_contours;
					++frame_contours[view];
				}
			}
		}
		packet_stats.contours[0] = frame_contours[0];
		packet_stats.contours[1] = frame_contours[1];
		bool detected = false, tracked = false;
		for (int view = 0; !gesture && view < 2; ++view) {
			for (int hand = 0; hand < 2; ++hand) {
				const auto &v = mercury_tracker.views[view];
				const auto &region = v.regions_of_interest_this_frame[hand];
				if (v.keypoint_input_valid[hand] && v.keypoint_score_pass[hand] &&
				    mercury_tracker.keypoint_outputs[hand].views[view].active &&
				    outline_validated_region(stereo, region, view, contour_bounds[view])) {
					detected |= region.provenance == mercury::ROIProvenance::HAND_DETECTION;
					tracked |= region.provenance == mercury::ROIProvenance::POSE_PREDICTION;
					++mercury_regions;
				}
			}
		}
		const char *status = estimated ? "Live camera | estimated hand contour"
		                           : detected ? "Live camera | Mercury: detected hand region"
		                           : tracked ? "Live camera | Mercury: tracked hand region"
		                                     : "Live camera | Mercury: no validated hand region";
		if (show_window) {
			cv::putText(stereo, status,
			            cv::Point(16, 36), cv::FONT_HERSHEY_SIMPLEX, 0.8, cv::Scalar(240, 240, 240), 2);
			if (headset_preview) {
				const int remaining = preview_remaining_seconds(std::chrono::steady_clock::now(), headset_deadline);
				draw_preview_countdown(stereo, remaining);
			}
		}
		if (show_window) {
			if (headset_preview) {
				require_steamvr_stopped();
				verify_live_index_window(window, target);
				// Keep both complete raw camera halves visible; this is not an optically calibrated XR view.
				cv::resize(stereo, headset_view, headset_view.size());
				cv::imshow(window, headset_image);
				verify_live_index_window(window, target);
			} else { cv::imshow(window, stereo); }
		}
		if (packet) {
			require_steamvr_stopped();
			write_stereo_packet(stereo, frames, capture_ns, packet_stats, gesture_records);
		} else if (stream) {
			require_steamvr_stopped();
			const uint64_t now_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
			    std::chrono::steady_clock::now().time_since_epoch()).count());
			// v1 has no timestamp field. Never make a delayed inference look like a
			// fresh camera image to a receive-time-only Blender consumer.
			if (!legacy_source_frame_fresh(capture_ns, now_ns)) {
				++late_frame_drops;
			} else {
				write_stereo_rgba(stereo, frame_contours);
			}
		} else if (gesture) {
			require_steamvr_stopped();
			write_gesture_frame(frames, params.timestamp_ns, gesture_records);
		}
		const int key = show_window ? cv::waitKey(1) : -1;
		if (!gesture) { gray.setTo(0); }
		SecureZeroMemory(pixels.data(), pixels.size());
		++frames;
		if (show_window && (key == 27 || key == 'q' || key == 'Q' || cv::getWindowProperty(window, cv::WND_PROP_VISIBLE) < 1)) {
			if (headset_preview) { preview_end = PreviewEnd::UserClose; }
			break;
		}
	}
	if (headset_preview && preview_end == PreviewEnd::None &&
	    std::chrono::steady_clock::now() >= headset_deadline) { preview_end = PreviewEnd::Timed; }
	prefetch.stop();
	if (stream || packet) {
		latest_reader.stop();
		empty_polls = latest_reader.empty_polls;
	}
	if (show_window) { cv::destroyWindow(window); }
	if (headset_preview) { headset_image.setTo(cv::Scalar(0)); }
	SecureZeroMemory(pixels.data(), pixels.size());
	const auto &d = mercury_tracker.diagnostics;
	std::fprintf(stream || gesture || packet ? stderr : stdout, "XR_MERCURY_PREVIEW frames=%u estimated_contours=%u mercury_regions=%u "
	            "stream_failed=%u pipe_empty_polls=%llu "
	            "reader_complete_frames=%llu reader_dropped_frames=%llu late_frame_drops=%u "
	            "detector_scores_over_03=%u "
	            "detector_rois_accepted=%u keypoint_score_pass=%u headset_preview=%u "
	            "images_saved=0 joint_traces_saved=0\n",
	            frames, estimated_contours, mercury_regions, stream_failed,
	            static_cast<unsigned long long>(empty_polls),
	            static_cast<unsigned long long>(latest_reader.received_count),
	            static_cast<unsigned long long>(latest_reader.dropped_count),
	            late_frame_drops,
	            d.detector_scores_over_03, d.detector_rois_accepted,
	            d.keypoint_score_pass, headset_preview);
	if (headset_preview && frames && !stream_failed) {
		if (preview_end == PreviewEnd::Timed) {
			std::puts("XR_INDEX_PREVIEW_NORMAL_END reason=15s_safety_limit camera_accessed=1 images_saved=0");
		} else if (preview_end == PreviewEnd::UserClose) {
			std::puts("XR_INDEX_PREVIEW_NORMAL_END reason=user_close camera_accessed=1 images_saved=0");
		}
	}
	return frames && !stream_failed ? 0 : 1;
}

int synthetic_stream()
{
	if (_setmode(_fileno(stdout), _O_BINARY) == -1) { throw std::runtime_error("Binary stdout unavailable"); }
	for (int frame = 0; frame < 3; ++frame) {
		cv::Mat stereo(kHeight, kWidth, CV_8UC3, cv::Scalar(0));
		for (int view = 0; view < 2; ++view) {
			const cv::Point center(view * kEyeWidth + 400 + frame * 20, 480);
			cv::circle(stereo, center, 70, kOutlineBlue, 2, cv::LINE_AA);
			stereo.at<cv::Vec3b>(0, view * kEyeWidth) = view ? cv::Vec3b(80, 0, 0) : cv::Vec3b(0, 0, 80);
		}
		write_stereo_rgba(stereo, {1, 1});
	}
	return 0;
}

int stereo_packet_self_test()
{
	const StereoPacketStats stats{{2, 1}, 1, 2, 1};
	const auto header = stereo_packet_header(17, 5'000'000'000ULL, 5'042'000'000ULL, stats, 1);
	const auto u32_at = [](const std::vector<uint8_t> &bytes, size_t offset) {
		uint32_t value = 0;
		for (int byte = 0; byte < 4; ++byte) { value |= uint32_t(bytes.at(offset + byte)) << (byte * 8); }
		return value;
	};
	const auto u64_at = [](const std::vector<uint8_t> &bytes, size_t offset) {
		uint64_t value = 0;
		for (int byte = 0; byte < 8; ++byte) { value |= uint64_t(bytes.at(offset + byte)) << (byte * 8); }
		return value;
	};
	GestureRecord record{1, 0, 0.97f, 0.75f};
	record.eye_xy[0] = 420.5f;
	record.eye_xy[1] = 260.25f;
	const auto wire_record = stereo_packet_record(record);
	uint32_t first_x_bits = 0;
	std::memcpy(&first_x_bits, &record.eye_xy[0], sizeof(first_x_bits));
	bool ready = header.size() == 80 && wire_record.size() == 184 &&
	             u32_at(header, 0) == kStereoPacketMagic && u32_at(header, 4) == 2 &&
	             u32_at(header, 8) == 80 && u32_at(header, 12) == 960 &&
	             u32_at(header, 16) == 960 && u32_at(header, 20) == 17 &&
	             u32_at(header, 24) == 7 && u32_at(header, 28) == 3'686'400 &&
	             u32_at(header, 32) == 1 && u32_at(header, 36) == 2 &&
	             u32_at(header, 40) == 1 && u32_at(header, 44) == 1 &&
	             u32_at(header, 48) == 2 && u32_at(header, 52) == 1 &&
	             u32_at(header, 56) == 42 && u32_at(header, 60) == 0 &&
	             u64_at(header, 64) == 5'000'000'000ULL &&
	             u64_at(header, 72) == 5'042'000'000ULL &&
	             u32_at(wire_record, 0) == 1 && u32_at(wire_record, 4) == 0 &&
	             u32_at(wire_record, 16) == first_x_bits;
	try {
		(void)stereo_packet_header(0, 2, 1, stats, 0);
		ready = false;
	} catch (const std::runtime_error &) {}
	try {
		(void)stereo_packet_header(0, 1, 2, stats, 5);
		ready = false;
	} catch (const std::runtime_error &) {}
	std::printf("%s camera_accessed=0 images_saved=0 joint_traces_saved=0\n",
	            ready ? "MERCURY_STEREO_PACKET_SELFTEST_PASS" : "MERCURY_STEREO_PACKET_SELFTEST_FAIL");
	return ready ? 0 : 1;
}

int legacy_freshness_self_test()
{
	const uint64_t capture = 1'000'000'000ULL;
	const bool ready = legacy_source_frame_fresh(capture, capture) &&
	                   legacy_source_frame_fresh(capture, capture + kLegacyMaxSourceAgeNs) &&
	                   !legacy_source_frame_fresh(capture, capture + kLegacyMaxSourceAgeNs + 1) &&
	                   !legacy_source_frame_fresh(capture, capture - 1);
	std::printf("%s max_source_age_ms=500 camera_accessed=0 images_saved=0\n",
	            ready ? "MERCURY_LEGACY_FRESHNESS_SELFTEST_PASS" : "MERCURY_LEGACY_FRESHNESS_SELFTEST_FAIL");
	return ready ? 0 : 1;
}

int synthetic_gesture_stream(bool show_preview = false)
{
	if (_setmode(_fileno(stdout), _O_BINARY) == -1) { throw std::runtime_error("Binary stdout unavailable"); }
	constexpr const char *window = "Synthetic stereo gesture preview - no camera";
	if (show_preview) { cv::namedWindow(window, cv::WINDOW_NORMAL); cv::resizeWindow(window, 960, 480); }
	for (uint32_t frame = 0; frame < 3; ++frame) {
		if (show_preview) {
			cv::Mat stereo(kHeight, kWidth, CV_8UC3, cv::Scalar(0));
			for (int view = 0; view < 2; ++view) {
				cv::circle(stereo, cv::Point(view * kEyeWidth + 400 + int(frame) * 20, 480),
				           70, kOutlineBlue, 2, cv::LINE_AA);
			}
			cv::imshow(window, stereo);
		}
		GestureRecord record{0, 0, 0.99f, 0.9f};
		for (size_t point = 0; point < 21; ++point) {
			record.eye_xy[2 * point] = 400.0f + float(point);
			record.eye_xy[2 * point + 1] = 480.0f + float(point);
		}
		write_gesture_frame(frame, 1'000'000'000ULL + frame * 16'666'667ULL, {record});
		if (show_preview) { cv::waitKey(100); }
	}
	if (show_preview) { cv::destroyWindow(window); }
	return 0;
}

int synthetic_job_parent()
{
	wchar_t path[MAX_PATH]{};
	const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
	if (!length || length >= MAX_PATH) { throw std::runtime_error("Self-test executable path unavailable"); }
	const std::wstring application(path);
	Child child(application, L"\"" + application + L"\" --synthetic-job-child");
	std::printf("MERCURY_JOB_CHILD_PID=%lu\n", child.process.dwProcessId);
	std::fflush(stdout);
	Sleep(30000); // Test harness terminates this parent; job closure must stop only its child.
	return 0;
}

int synthetic_pipe_eof_self_test()
{
	wchar_t path[MAX_PATH]{};
	const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
	if (!length || length >= MAX_PATH) { throw std::runtime_error("Self-test executable path unavailable"); }
	Tracker tracker;
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	offline::PalmDetector palm_detector(model_dir / "palm_detection_MEDIAPIPE.onnx");
	offline::HandLandmarkProbe hand_landmarks(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	const std::wstring application(path);
	Child child(application, L"\"" + application + L"\" --synthetic-pipe-eof-child");
	std::vector<uint8_t> pixels(64);
	uint64_t empty_polls = 0;
	FramePrefetch prefetch(child, pixels.size());
	prefetch.start(std::chrono::steady_clock::now() + std::chrono::seconds(2), empty_polls);
	const bool frame_ready = prefetch.take(pixels);
	prefetch.stop();
	DWORD exit_code = 0;
	if (frame_ready || WaitForSingleObject(child.process.hProcess, 1000) != WAIT_OBJECT_0 ||
	    !GetExitCodeProcess(child.process.hProcess, &exit_code) || exit_code != 23) {
		throw std::runtime_error("Synthetic early EOF was not reported");
	}
	report_child_failure(child);
	{
		Child idle(application, L"\"" + application + L"\" --synthetic-job-child");
		FramePrefetch idle_prefetch(idle, pixels.size());
		idle_prefetch.start(std::chrono::steady_clock::now() + std::chrono::seconds(30), empty_polls);
		Sleep(10);
		const auto stop_begin = std::chrono::steady_clock::now();
		idle_prefetch.stop();
		if (std::chrono::steady_clock::now() - stop_begin > std::chrono::seconds(1)) {
			throw std::runtime_error("Synthetic pending read did not cancel promptly");
		}
	}
	std::puts("MERCURY_PIPE_EOF_SELFTEST_PASS camera_accessed=0");
	return 0;
}

int synthetic_frame_source()
{
	if (_setmode(_fileno(stdout), _O_BINARY) == -1) { return 1; }
	std::vector<uint8_t> pixels(static_cast<size_t>(kWidth) * kHeight * 3, 0);
	for (int frame = 0; frame < 20; ++frame) {
		Sleep(33);
		std::memcpy(pixels.data(), &frame, sizeof(frame));
		if (std::fwrite(pixels.data(), 1, pixels.size(), stdout) != pixels.size() ||
		    std::fflush(stdout)) { return 1; }
	}
	return 0;
}

int synthetic_latest_reader_self_test()
{
	wchar_t path[MAX_PATH]{};
	const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
	if (!length || length >= MAX_PATH) { throw std::runtime_error("Self-test executable path unavailable"); }
	const std::wstring application(path);
	Child child(application, L"\"" + application + L"\" --synthetic-frame-source");
	LatestFrameReader reader(child, size_t(kWidth) * kHeight * 3,
	                         std::chrono::steady_clock::now() + std::chrono::seconds(5));
	reader.start();
	std::vector<uint8_t> pixels(size_t(kWidth) * kHeight * 3);
	uint64_t first_timestamp = 0, second_timestamp = 0;
	const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
	const bool first_ready = reader.take(pixels, first_timestamp, deadline);
	int first_frame = -1;
	if (first_ready) { std::memcpy(&first_frame, pixels.data(), sizeof(first_frame)); }
	Sleep(280); // Fall deliberately behind the 33 ms source; complete frames must be dropped.
	const bool second_ready = reader.take(pixels, second_timestamp, deadline);
	int second_frame = -1;
	if (second_ready) { std::memcpy(&second_frame, pixels.data(), sizeof(second_frame)); }
	reader.stop();
	SecureZeroMemory(pixels.data(), pixels.size());
	const bool ready = first_ready && second_ready && first_frame >= 0 &&
	                   second_frame > first_frame + 1 && second_frame < 20 &&
	                   second_timestamp > first_timestamp && reader.dropped_count > 0 &&
	                   reader.received_count > 2;
	std::printf("%s received=%llu dropped=%llu camera_accessed=0 images_saved=0\n",
	            ready ? "MERCURY_LATEST_READER_SELFTEST_PASS" : "MERCURY_LATEST_READER_SELFTEST_FAIL",
	            static_cast<unsigned long long>(reader.received_count),
	            static_cast<unsigned long long>(reader.dropped_count));
	return ready ? 0 : 1;
}

int synthetic_prefetch_benchmark()
{
	wchar_t path[MAX_PATH]{};
	const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
	if (!length || length >= MAX_PATH) { throw std::runtime_error("Self-test executable path unavailable"); }
	const std::wstring application(path);
	const auto run_case = [&](bool overlap) {
		Child child(application, L"\"" + application + L"\" --synthetic-frame-source");
		std::vector<uint8_t> pixels(static_cast<size_t>(kWidth) * kHeight * 3);
		uint64_t empty_polls = 0;
		FramePrefetch prefetch(child, pixels.size());
		const auto begin = std::chrono::steady_clock::now();
		if (overlap) { prefetch.start(begin + std::chrono::seconds(5), empty_polls); }
		for (int frame = 0; frame < 20; ++frame) {
			const bool ready = overlap ? prefetch.take(pixels)
			                           : read_frame(child, pixels, begin + std::chrono::seconds(5), empty_polls);
			if (!ready) { throw std::runtime_error("Synthetic frame source ended early"); }
			if (overlap && frame < 19) { prefetch.start(begin + std::chrono::seconds(5), empty_polls); }
			Sleep(22); // Measured known-screenshot preview processing is approximately 22 ms/frame.
			SecureZeroMemory(pixels.data(), pixels.size());
		}
		prefetch.stop();
		return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count();
	};
	const double serial_ms = run_case(false);
	const double overlap_ms = run_case(true);
	std::printf("MERCURY_PREFETCH_BENCH frames=20 serial_ms=%.3f overlap_ms=%.3f camera_accessed=0 images_saved=0\n",
	            serial_ms, overlap_ms);
	return 0;
}

int synthetic_predict_history_self_test()
{
	Tracker tracker;
	auto &hgt = mercury::HandTracking::fromC(tracker.sync);
	const auto joints = Eigen::Array<float, 3, 21>::Zero();
	hgt.history_hands[0].push_back(joints);
	hgt.history_hands[0].push_back(joints);
	hgt.history_timestamps.push_back(1'000'000ULL);
	hgt.current_frame_timestamp = 2'000'000ULL;
	if (hgt.history_hands[0].size() != 2 || hgt.history_timestamps.size() != 1) {
		throw std::runtime_error("Synthetic history fixture is invalid");
	}
	mercury::predict_new_regions_of_interest(&hgt);
	hgt.history_timestamps.push_back(1'000'000ULL);
	mercury::predict_new_regions_of_interest(&hgt); // A repeated timestamp must not divide by zero.
	std::puts("MERCURY_PREDICT_HISTORY_SELFTEST_PASS hand_history=2 timestamp_history=1 camera_accessed=0");
	return 0;
}

int benchmark_synthetic()
{
	Tracker tracker;
	cv::Mat pattern(kHeight, kEyeWidth, CV_8UC1);
	for (int y = 0; y < pattern.rows; ++y) {
		for (int x = 0; x < pattern.cols; ++x) {
			pattern.at<uint8_t>(y, x) = static_cast<uint8_t>((x * 13 + y * 7) & 255);
		}
	}
	double process_ms = 0.0;
	uint32_t active = 0;
	for (int i = 0; i < kMaxFrames; ++i) {
		xrt_frame *wrapped[2] = {nullptr, nullptr};
		xat::FrameMat::Params params{};
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = 1'000'000ULL + static_cast<uint64_t>(i) * 16'666'667ULL;
		xat::FrameMat::wrapL8(pattern, &wrapped[0], params);
		xat::FrameMat::wrapL8(pattern, &wrapped[1], params);
		xrt_hand_joint_set hands[2]{};
		uint64_t timestamp = 0;
		const auto start = std::chrono::steady_clock::now();
		t_ht_sync_process(tracker.sync, wrapped[0], wrapped[1], &hands[0], &hands[1], &timestamp);
		process_ms += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
		active += hands[0].is_active + hands[1].is_active;
		for (auto *&frame : wrapped) { xrt_frame_reference(&frame, nullptr); }
		if (timestamp != params.timestamp_ns) { return 1; }
	}
	const auto &d = mercury::HandTracking::fromC(tracker.sync).diagnostics;
	std::printf("MERCURY_DIRECT_SYNTHETIC_BENCHMARK frames=%d process_total_ms=%.3f "
	            "process_mean_ms=%.3f detector_view_runs=%u keypoint_view_runs=%u "
	            "active_hand_observations=%u camera_accessed=0 images_saved=0 joint_traces_saved=0\n",
	            kMaxFrames, process_ms, process_ms / kMaxFrames, d.detector_view_runs,
	            d.keypoint_view_runs, active);
	return 0;
}

int known_user_screenshot_smoke(const std::filesystem::path &path, bool rgba_pipe = false)
{
	// This opt-in check reads the known UI screenshot only in memory. It never
	// opens a camera, displays a window, or writes an annotated image.
	const cv::Mat screenshot = cv::imread(path.string(), cv::IMREAD_COLOR);
	if (screenshot.cols != 1449 || screenshot.rows != 620) {
		throw std::runtime_error("Known screenshot dimensions do not match");
	}
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	offline::PalmDetector palm_detector(model_dir / "palm_detection_MEDIAPIPE.onnx");
	offline::HandLandmarkProbe hand_landmarks(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	const std::array<cv::Rect, 2> eyes = {cv::Rect(195, 27, 600, 593), cv::Rect(795, 27, 600, 593)};
	std::array<unsigned, 2> drawn{};
	std::array<cv::Mat, 2> annotated;
	for (size_t view = 0; view < eyes.size(); ++view) {
		const cv::Mat eye = screenshot(eyes[view]);
		cv::Mat &display = annotated[view];
		display = eye.clone();
		const auto palms = palm_detector.detect(eye, 0.5f);
		for (size_t i = 0; i < std::min<size_t>(palms.size(), 2); ++i) {
			const auto landmarks = hand_landmarks.infer(eye, palms[i]);
			std::vector<cv::Point> contour;
			if (!estimated_hand_contour(palms[i], landmarks, eye.size(), contour)) { continue; }
			cv::polylines(display, contour, true, kOutlineBlue, 1, cv::LINE_AA);
			++drawn[view];
		}
	}
	if (rgba_pipe) {
		if (!drawn[0] || !drawn[1] || _setmode(_fileno(stdout), _O_BINARY) == -1) {
			throw std::runtime_error("Stereo annotated frame unavailable for binary pipe");
		}
		const uint32_t header[] = {0x42575258u, 1u, 600u, 593u, drawn[0], drawn[1]}; // XRWB, version, eye geometry, counts
		if (std::fwrite(header, sizeof(header), 1, stdout) != 1) {
			throw std::runtime_error("Binary pipe header write failed");
		}
		for (const cv::Mat &eye : annotated) {
			cv::Mat rgba;
			cv::cvtColor(eye, rgba, cv::COLOR_BGR2RGBA);
			const size_t bytes = rgba.total() * rgba.elemSize();
			if (!rgba.isContinuous() || std::fwrite(rgba.data, 1, bytes, stdout) != bytes) {
				throw std::runtime_error("Binary pipe eye write failed");
			}
		}
		if (std::fflush(stdout)) { throw std::runtime_error("Binary pipe flush failed"); }
		return 0;
	}
	std::printf("MERCURY_KNOWN_SCREENSHOT left_estimated_contours=%u right_estimated_contours=%u "
	            "camera_accessed=0 images_saved=0 joint_traces_saved=0\n", drawn[0], drawn[1]);
	return drawn[0] && drawn[1] ? 0 : 1;
}

int benchmark_known_user_preview(const std::filesystem::path &path, bool parallel = false)
{
	// In-memory timing fixture: same models, frame geometry, and output resize as the live preview.
	const cv::Mat screenshot = cv::imread(path.string(), cv::IMREAD_COLOR);
	if (screenshot.cols != 1449 || screenshot.rows != 620) {
		throw std::runtime_error("Known screenshot dimensions do not match");
	}
	const std::array<cv::Rect, 2> eyes = {cv::Rect(195, 27, 600, 593), cv::Rect(795, 27, 600, 593)};
	cv::Mat input(kHeight, kWidth, CV_8UC3);
	for (int view = 0; view < 2; ++view) {
		cv::resize(screenshot(eyes[view]), input(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight)),
		           cv::Size(kEyeWidth, kHeight));
	}
	Tracker tracker;
	auto &mercury_tracker = mercury::HandTracking::fromC(tracker.sync);
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	offline::PalmDetector palm_detector(model_dir / "palm_detection_MEDIAPIPE.onnx");
	offline::HandLandmarkProbe hand_landmarks(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	std::unique_ptr<offline::PalmDetector> right_palm;
	std::unique_ptr<offline::HandLandmarkProbe> right_landmarks;
	if (parallel) {
		right_palm = std::make_unique<offline::PalmDetector>(model_dir / "palm_detection_MEDIAPIPE.onnx");
		right_landmarks = std::make_unique<offline::HandLandmarkProbe>(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	}
	cv::Mat panel(1600, 2880, CV_8UC3, cv::Scalar(0));
	cv::Mat panel_view = panel(centered_preview_rect(input.size(), panel.size()));
	double mercury_ms = 0, palm_ms = 0, landmark_ms = 0, eyes_ms = 0, present_ms = 0;
	uint32_t contours = 0;
	constexpr int runs = 30;
	const auto total_begin = std::chrono::steady_clock::now();
	for (int frame_index = 0; frame_index < runs; ++frame_index) {
		cv::Mat stereo = input.clone();
		cv::Mat gray, left, right;
		const auto mercury_begin = std::chrono::steady_clock::now();
		cv::cvtColor(stereo, gray, cv::COLOR_BGR2GRAY);
		split_index_frame(gray, left, right);
		xat::FrameMat::Params params{};
		params.stereo_format = XRT_STEREO_FORMAT_NONE;
		params.timestamp_ns = 1'000'000'000ULL + uint64_t(frame_index) * 33'333'333ULL;
		xrt_frame *wrapped[2] = {nullptr, nullptr};
		xat::FrameMat::wrapL8(left, &wrapped[0], params);
		xat::FrameMat::wrapL8(right, &wrapped[1], params);
		xrt_hand_joint_set hands[2]{};
		uint64_t output_timestamp = 0;
		t_ht_sync_process(tracker.sync, wrapped[0], wrapped[1], &hands[0], &hands[1], &output_timestamp);
		for (auto *&wrapped_frame : wrapped) { xrt_frame_reference(&wrapped_frame, nullptr); }
		mercury_ms += std::chrono::duration<double, std::milli>(
		    std::chrono::steady_clock::now() - mercury_begin).count();
		if (parallel) {
			const cv::Mat left_eye = stereo(cv::Rect(0, 0, kEyeWidth, kHeight));
			const cv::Mat right_eye = stereo(cv::Rect(kEyeWidth, 0, kEyeWidth, kHeight));
			const auto eyes_begin = std::chrono::steady_clock::now();
			auto left_task = std::async(std::launch::async, [&] {
				return estimate_eye_contours(left_eye, palm_detector, hand_landmarks);
			});
			std::array<std::vector<std::vector<cv::Point>>, 2> eye_contours;
			eye_contours[1] = estimate_eye_contours(right_eye, *right_palm, *right_landmarks);
			eye_contours[0] = left_task.get();
			eyes_ms += std::chrono::duration<double, std::milli>(
			    std::chrono::steady_clock::now() - eyes_begin).count();
			for (int view = 0; view < 2; ++view) {
				for (auto &contour : eye_contours[view]) {
					for (auto &point : contour) { point.x += view * kEyeWidth; }
					cv::polylines(stereo, contour, true, kOutlineBlue, 1, cv::LINE_AA);
					++contours;
				}
			}
		} else {
			for (int view = 0; view < 2; ++view) {
				cv::Mat eye = stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight));
				const auto palm_begin = std::chrono::steady_clock::now();
				const auto palms = palm_detector.detect(eye, 0.5f);
				palm_ms += std::chrono::duration<double, std::milli>(
				    std::chrono::steady_clock::now() - palm_begin).count();
				for (size_t i = 0; i < std::min<size_t>(palms.size(), 2); ++i) {
					const auto landmark_begin = std::chrono::steady_clock::now();
					const auto landmarks = hand_landmarks.infer(eye, palms[i]);
					landmark_ms += std::chrono::duration<double, std::milli>(
					    std::chrono::steady_clock::now() - landmark_begin).count();
					std::vector<cv::Point> contour;
					if (!estimated_hand_contour(palms[i], landmarks, eye.size(), contour)) { continue; }
					for (auto &point : contour) { point.x += view * kEyeWidth; }
					cv::polylines(stereo, contour, true, kOutlineBlue, 1, cv::LINE_AA);
					++contours;
				}
			}
		}
		const auto present_begin = std::chrono::steady_clock::now();
		cv::resize(stereo, panel_view, panel_view.size());
		present_ms += std::chrono::duration<double, std::milli>(
		    std::chrono::steady_clock::now() - present_begin).count();
	}
	const double total_ms = std::chrono::duration<double, std::milli>(
	    std::chrono::steady_clock::now() - total_begin).count();
	std::printf("MERCURY_KNOWN_PREVIEW_BENCH frames=%d parallel=%u contours=%u mercury_ms=%.3f palm_ms=%.3f landmark_ms=%.3f eyes_ms=%.3f resize_ms=%.3f total_ms=%.3f camera_accessed=0 images_saved=0 joint_traces_saved=0\n",
	            runs, parallel, contours, mercury_ms / runs, palm_ms / runs,
	            landmark_ms / runs, eyes_ms / runs, present_ms / runs, total_ms / runs);
	return contours ? 0 : 1;
}

int benchmark_parallel_eyes(const std::filesystem::path &path)
{
	const cv::Mat screenshot = cv::imread(path.string(), cv::IMREAD_COLOR);
	if (screenshot.cols != 1449 || screenshot.rows != 620) {
		throw std::runtime_error("Known screenshot dimensions do not match");
	}
	const std::array<cv::Rect, 2> eyes = {cv::Rect(195, 27, 600, 593), cv::Rect(795, 27, 600, 593)};
	cv::Mat stereo(kHeight, kWidth, CV_8UC3);
	for (int view = 0; view < 2; ++view) {
		cv::resize(screenshot(eyes[view]), stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight)),
		           cv::Size(kEyeWidth, kHeight));
	}
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	std::array<std::unique_ptr<offline::PalmDetector>, 2> palms;
	std::array<std::unique_ptr<offline::HandLandmarkProbe>, 2> landmarks;
	for (int view = 0; view < 2; ++view) {
		palms[view] = std::make_unique<offline::PalmDetector>(model_dir / "palm_detection_MEDIAPIPE.onnx");
		landmarks[view] = std::make_unique<offline::HandLandmarkProbe>(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	}
	const auto process_eye = [&](int view) {
		const cv::Mat eye = stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight));
		const auto candidates = palms[view]->detect(eye, 0.5f);
		unsigned valid = 0;
		uint64_t geometry = 1469598103934665603ULL;
		for (size_t i = 0; i < std::min<size_t>(candidates.size(), 2); ++i) {
			const auto hand = landmarks[view]->infer(eye, candidates[i]);
			std::vector<cv::Point> contour;
			if (!estimated_hand_contour(candidates[i], hand, eye.size(), contour)) { continue; }
			++valid;
			for (const auto &point : contour) {
				geometry = (geometry ^ static_cast<uint32_t>(point.x)) * 1099511628211ULL;
				geometry = (geometry ^ static_cast<uint32_t>(point.y)) * 1099511628211ULL;
			}
		}
		return std::pair<unsigned, uint64_t>{valid, geometry};
	};
	process_eye(0);
	process_eye(1);
	const auto measure = [&](bool parallel) {
		unsigned valid = 0;
		uint64_t geometry = 0;
		const auto begin = std::chrono::steady_clock::now();
		for (int frame = 0; frame < 30; ++frame) {
			std::pair<unsigned, uint64_t> left, right;
			if (parallel) {
				auto left_task = std::async(std::launch::async, [&] { return process_eye(0); });
				right = process_eye(1);
				left = left_task.get();
			} else {
				left = process_eye(0);
				right = process_eye(1);
			}
			valid += left.first + right.first;
			geometry += left.second + 3 * right.second;
		}
		return std::tuple<double, unsigned, uint64_t>{
		    std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count(), valid, geometry};
	};
	const auto serial = measure(false);
	const auto parallel = measure(true);
	const bool same_geometry = std::get<1>(serial) == std::get<1>(parallel) &&
	                           std::get<2>(serial) == std::get<2>(parallel);
	std::printf("MERCURY_PARALLEL_EYES_BENCH frames=30 serial_ms=%.3f parallel_ms=%.3f serial_contours=%u parallel_contours=%u same_geometry=%u camera_accessed=0 images_saved=0\n",
	            std::get<0>(serial), std::get<0>(parallel), std::get<1>(serial),
	            std::get<1>(parallel), same_geometry);
	return same_geometry && std::get<1>(serial) > 0 ? 0 : 1;
}

int synthetic_parallel_eyes_self_test()
{
	const auto model_dir = std::filesystem::path(MERCURY_MODEL_DIR);
	std::array<std::unique_ptr<offline::PalmDetector>, 2> palms;
	std::array<std::unique_ptr<offline::HandLandmarkProbe>, 2> landmarks;
	for (int view = 0; view < 2; ++view) {
		palms[view] = std::make_unique<offline::PalmDetector>(model_dir / "palm_detection_MEDIAPIPE.onnx");
		landmarks[view] = std::make_unique<offline::HandLandmarkProbe>(model_dir / "hand_landmark_MEDIAPIPE.onnx");
	}
	cv::Mat stereo(kHeight, kWidth, CV_8UC3, cv::Scalar(36, 42, 49));
	for (int view = 0; view < 2; ++view) {
		cv::ellipse(stereo, cv::Point(view * kEyeWidth + 480, 480), cv::Size(130, 210),
		            12, 0, 360, cv::Scalar(120, 165, 204), -1);
	}
	const auto process_eye = [&](int view) {
		const cv::Mat eye = stereo(cv::Rect(view * kEyeWidth, 0, kEyeWidth, kHeight));
		const auto contours = estimate_eye_contours(eye, *palms[view], *landmarks[view]);
		uint64_t geometry = 1469598103934665603ULL;
		for (const auto &contour : contours) {
			for (const auto &point : contour) {
				geometry = (geometry ^ uint32_t(point.x)) * 1099511628211ULL;
				geometry = (geometry ^ uint32_t(point.y)) * 1099511628211ULL;
			}
		}
		return std::pair<size_t, uint64_t>{contours.size(), geometry};
	};
	const auto begin_serial = std::chrono::steady_clock::now();
	const auto left_serial = process_eye(0);
	const auto right_serial = process_eye(1);
	const double serial_ms = std::chrono::duration<double, std::milli>(
	    std::chrono::steady_clock::now() - begin_serial).count();
	const auto begin_parallel = std::chrono::steady_clock::now();
	auto left_future = std::async(std::launch::async, [&] { return process_eye(0); });
	const auto right_parallel = process_eye(1);
	const auto left_parallel = left_future.get();
	const double parallel_ms = std::chrono::duration<double, std::milli>(
	    std::chrono::steady_clock::now() - begin_parallel).count();
	const bool ready = left_serial == left_parallel && right_serial == right_parallel;
	std::printf("%s serial_ms=%.3f parallel_ms=%.3f model_contours=%zu camera_accessed=0 images_saved=0\n",
	            ready ? "MERCURY_PARALLEL_EYES_SELFTEST_PASS" : "MERCURY_PARALLEL_EYES_SELFTEST_FAIL",
	            serial_ms, parallel_ms, left_serial.first + right_serial.first);
	return ready ? 0 : 1;
}
} // namespace

int wmain(int argc, wchar_t **argv)
{
	if (argc == 2 && std::wcscmp(argv[1], L"--parallel-eyes-self-test") == 0) {
		try { return synthetic_parallel_eyes_self_test(); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_PARALLEL_EYES_SELFTEST_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--legacy-freshness-self-test") == 0) {
		return legacy_freshness_self_test();
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--latest-reader-self-test") == 0) {
		try { return synthetic_latest_reader_self_test(); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_LATEST_READER_SELFTEST_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--packet-self-test") == 0) {
		try { return stereo_packet_self_test(); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_STEREO_PACKET_SELFTEST_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--headset-id-self-test") == 0) {
		const bool ready = index_monitor_id_self_test();
		std::puts(ready ? "XR_INDEX_ID_SELFTEST_PASS camera_accessed=0"
		                : "XR_INDEX_ID_SELFTEST_FAIL camera_accessed=0");
		return ready ? 0 : 1;
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--headset-display-probe") == 0) {
		try {
			require_steamvr_stopped();
			select_index_monitor();
			std::puts("XR_INDEX_DISPLAY_READY camera_accessed=0 steamvr=0");
			return 0;
		} catch (const std::exception &error) {
			std::fprintf(stderr, "XR_INDEX_DISPLAY_UNAVAILABLE %s camera_accessed=0\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--headset-test-pattern") == 0) {
		try { return headset_test_pattern(); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "XR_INDEX_DISPLAY_TEST_FAIL %s camera_accessed=0\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-job-child") == 0) { Sleep(30000); return 0; }
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-pipe-eof-child") == 0) {
		std::fputs("Synthetic input failed before first frame\n", stderr);
		return 23;
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-frame-source") == 0) {
		return synthetic_frame_source();
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-prefetch-benchmark") == 0) {
		try { return synthetic_prefetch_benchmark(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_PREFETCH_BENCH_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-pipe-eof-self-test") == 0) {
		try { return synthetic_pipe_eof_self_test(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_PIPE_EOF_SELFTEST_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-predict-history-self-test") == 0) {
		try { return synthetic_predict_history_self_test(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_PREDICT_HISTORY_SELFTEST_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-job-parent") == 0) {
		try { return synthetic_job_parent(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_JOB_SELFTEST_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-stream-rgba") == 0) {
		try { return synthetic_stream(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_SYNTHETIC_STREAM_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-gesture-stream") == 0) {
		try { return synthetic_gesture_stream(); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_SYNTHETIC_GESTURE_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-gesture-preview") == 0) {
		try { return synthetic_gesture_stream(true); }
		catch (const std::exception &error) { std::fprintf(stderr, "MERCURY_SYNTHETIC_GESTURE_PREVIEW_FAIL %s\n", error.what()); return 1; }
	}
	if (argc == 3 && std::wcscmp(argv[1], L"--known-user-screenshot-rgba") == 0) {
		try { return known_user_screenshot_smoke(argv[2], true); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_KNOWN_SCREENSHOT_RGBA_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 3 && std::wcscmp(argv[1], L"--known-user-screenshot") == 0) {
		try { return known_user_screenshot_smoke(argv[2]); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_KNOWN_SCREENSHOT_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 3 && std::wcscmp(argv[1], L"--known-user-preview-benchmark") == 0) {
		try { return benchmark_known_user_preview(argv[2]); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_KNOWN_PREVIEW_BENCH_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 3 && std::wcscmp(argv[1], L"--known-user-preview-parallel-benchmark") == 0) {
		try { return benchmark_known_user_preview(argv[2], true); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_KNOWN_PREVIEW_BENCH_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 3 && std::wcscmp(argv[1], L"--known-user-parallel-benchmark") == 0) {
		try { return benchmark_parallel_eyes(argv[2]); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_PARALLEL_EYES_BENCH_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--synthetic-benchmark") == 0) {
		try { return benchmark_synthetic(); }
		catch (const std::exception &error) {
			std::fprintf(stderr, "MERCURY_DIRECT_SYNTHETIC_BENCHMARK_FAIL %s\n", error.what());
			return 1;
		}
	}
	if (argc == 2 && std::wcscmp(argv[1], L"--self-test") == 0) {
		bool ready = !steamvr_running() && kWidth == 2 * kEyeWidth && kMaxFrames == 30;
		const auto start = std::chrono::steady_clock::time_point{};
		ready = ready && preview_remaining_seconds(start, start + kDeadline) == 15 &&
		        preview_remaining_seconds(start + std::chrono::milliseconds(10'001), start + kDeadline) == 5 &&
		        preview_remaining_seconds(start + kDeadline, start + kDeadline) == 0;
		ready = ready && centered_preview_rect(cv::Size(1920, 960), cv::Size(2880, 1600)) ==
		                   cv::Rect(0, 80, 2880, 1440);
		cv::Mat countdown_probe(kHeight, kWidth, CV_8UC3, cv::Scalar(0));
		draw_preview_countdown(countdown_probe, 5);
		for (int eye = 0; eye < 2; ++eye) {
			cv::Mat band = countdown_probe(cv::Rect(eye * kEyeWidth + 200, 78, 560, 110));
			cv::Mat light;
			cv::inRange(band, cv::Scalar(180, 180, 180), cv::Scalar(255, 255, 255), light);
			ready = ready && cv::countNonZero(light) > 1000;
		}
		ready = ready && index_monitor_id_self_test();
		if (ready) {
			const auto diagnostic = ffmpeg_command(L"ffmpeg", L"Index", false);
			const auto preview = ffmpeg_command(L"ffmpeg", L"Index", true);
			ready = diagnostic.find(L"-framerate 30") != std::wstring::npos &&
			        preview.find(L"-framerate 30") != std::wstring::npos &&
			        preview.find(L"-rtbufsize 16M") != std::wstring::npos &&
			        diagnostic.find(L"-pix_fmt gray") != std::wstring::npos &&
			        preview.find(L"-pix_fmt bgr24") != std::wstring::npos;
		}
		if (ready) {
			uint8_t rows[] = {1, 2, 3, 4, 5, 6, 7, 8};
			cv::Mat row_probe(2, 4, CV_8UC1, rows);
			cv::Mat left, right;
			split_index_frame(row_probe, left, right);
			ready = left.at<uint8_t>(0, 0) == 1 && left.at<uint8_t>(1, 0) == 5 &&
			        right.at<uint8_t>(0, 0) == 3 && right.at<uint8_t>(1, 0) == 7;
		}
		if (ready) {
			cv::Mat overlay(20, 40, CV_8UC3, cv::Scalar(0));
			mercury::hand_region_of_interest detected{};
			detected.found = true;
			detected.provenance = mercury::ROIProvenance::HAND_DETECTION;
			detected.hand_detection_confidence = true;
			detected.center_px = {10.0f, 10.0f};
			detected.size_px = 8.0f;
			ready = outline_validated_region(overlay, detected, 0) &&
			        outline_validated_region(overlay, detected, 1) &&
			        overlay.at<cv::Vec3b>(6, 6) == cv::Vec3b(210, 105, 25) &&
			        overlay.at<cv::Vec3b>(6, 26) == cv::Vec3b(210, 105, 25);
			detected.provenance = mercury::ROIProvenance::POSE_PREDICTION;
			detected.hand_detection_confidence = false;
			ready = ready && outline_validated_region(overlay, detected, 0);
			ready = ready && !outline_validated_region(overlay, detected, 0, {cv::Rect(6, 6, 8, 8)});
			detected.found = false;
			ready = ready && !outline_validated_region(overlay, detected, 0) &&
			        overlay.at<cv::Vec3b>(0, 0) == cv::Vec3b(0, 0, 0);
		}
		if (ready) {
			offline::PalmDetection palm{};
			palm.score = 0.75f;
			palm.raw_box = palm.box = cv::Rect2f(430, 430, 100, 100);
			offline::HandLandmarkResult hand{};
			hand.presence = 0.98f;
			for (size_t i = 0; i < hand.eye_landmarks.size(); ++i) {
				const float angle = float(i) * 6.2831853f / float(hand.eye_landmarks.size());
				hand.eye_landmarks[i] = {480 + 40 * std::cos(angle), 480 + 60 * std::sin(angle)};
			}
			std::vector<cv::Point> contour;
			ready = estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
			if (ready) {
				cv::Mat overlay(960, 1920, CV_8UC3, cv::Scalar(0));
				for (auto &point : contour) { point.x += 960; }
				cv::polylines(overlay, contour, true, kOutlineBlue, 1, cv::LINE_AA);
				ready = overlay.at<cv::Vec3b>(contour[0].y, contour[0].x)[0] > 0 &&
				        overlay.at<cv::Vec3b>(contour[0].y, contour[0].x - 960) == cv::Vec3b(0, 0, 0);
			}
			palm.score = 0.49f;
			ready = ready && !estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
			palm.score = 0.75f;
			hand.presence = 0.94f;
			ready = ready && !estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
			hand.presence = 0.98f;
			hand.eye_landmarks[0].x = std::numeric_limits<float>::quiet_NaN();
			ready = ready && !estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
			hand.eye_landmarks[0].x = -1.0f;
			ready = ready && !estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
			palm.raw_box.width = 700.0f;
			ready = ready && !estimated_hand_contour(palm, hand, cv::Size(960, 960), contour);
		}
		if (ready) {
			Tracker tracker;
			cv::Mat blank(kHeight, kEyeWidth, CV_8UC1, cv::Scalar(0));
			xrt_frame *wrapped[2] = {nullptr, nullptr};
			xat::FrameMat::Params params{};
			params.stereo_format = XRT_STEREO_FORMAT_NONE;
			params.timestamp_ns = 1'000'000;
			xat::FrameMat::wrapL8(blank, &wrapped[0], params);
			xat::FrameMat::wrapL8(blank, &wrapped[1], params);
			xrt_hand_joint_set hands[2]{};
			uint64_t timestamp = 0;
			t_ht_sync_process(tracker.sync, wrapped[0], wrapped[1], &hands[0], &hands[1], &timestamp);
			for (auto *&frame : wrapped) { xrt_frame_reference(&frame, nullptr); }
			const auto &d = mercury::HandTracking::fromC(tracker.sync).diagnostics;
			ready = timestamp == params.timestamp_ns && !hands[0].is_active && !hands[1].is_active &&
			        d.detector_view_runs > 0 && d.detector_scores_over_03 == 0 && d.keypoint_view_runs == 0;
		}
		std::puts(ready ? "MERCURY_DIRECT_EVAL_SELFTEST_PASS camera_accessed=0 openvr_calls=0"
		                : "MERCURY_DIRECT_EVAL_SELFTEST_FAIL");
		return ready ? 0 : 1;
	}
	if (argc != 4 || (std::wcscmp(argv[1], L"--consented-dshow") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-preview") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-headset-preview") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-stream-rgba") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-stereo-packet-stream") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-gesture-stream") != 0 &&
	                  std::wcscmp(argv[1], L"--consented-gesture-preview") != 0)) {
		std::fputs("Camera use requires --consented-dshow, --consented-preview, --consented-headset-preview, --consented-stream-rgba, --consented-stereo-packet-stream, --consented-gesture-stream, or --consented-gesture-preview <camera-name> <ffmpeg-path>.\n", stderr);
		return 2;
	}
	try {
		return std::wcscmp(argv[1], L"--consented-dshow") == 0 ? run(argv[2], argv[3])
		       : run_preview(argv[2], argv[3], std::wcscmp(argv[1], L"--consented-stream-rgba") == 0,
		                     std::wcscmp(argv[1], L"--consented-gesture-stream") == 0 ||
		                     std::wcscmp(argv[1], L"--consented-gesture-preview") == 0 ||
		                     std::wcscmp(argv[1], L"--consented-stereo-packet-stream") == 0,
		                     std::wcscmp(argv[1], L"--consented-gesture-preview") == 0,
		                     std::wcscmp(argv[1], L"--consented-headset-preview") == 0,
		                     std::wcscmp(argv[1], L"--consented-stereo-packet-stream") == 0);
	}
	catch (const std::exception &error) {
		std::fprintf(stderr, "XR_MERCURY_DIRECT_EVAL_FAIL %s world_output=0\n", error.what());
		return 1;
	}
}
