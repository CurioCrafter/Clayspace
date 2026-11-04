// Simulator-only OpenXR projection submission. Synthetic eye pixels stay in RAM.
#define WIN32_LEAN_AND_MEAN
#define XR_USE_PLATFORM_WIN32
#define XR_USE_GRAPHICS_API_OPENGL
#include <Windows.h>
#include <unknwn.h>
#include <gl/GL.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(XrResult result, const char *operation)
{
	if (XR_FAILED(result)) { throw std::runtime_error(std::string(operation) + " failed: " + std::to_string(result)); }
}
void require_simulator()
{
	char enabled[8]{}, manifest[4096]{};
	GetEnvironmentVariableA("SIMULATED_ENABLE", enabled, sizeof(enabled));
	GetEnvironmentVariableA("XR_RUNTIME_JSON", manifest, sizeof(manifest));
	if (std::strcmp(enabled, "1") || !std::strstr(manifest, "monado-inproc-build")) {
		throw std::runtime_error("Requires isolated SIMULATED_ENABLE=1 and local Monado XR_RUNTIME_JSON");
	}
}
constexpr uint32_t kOfflineEyeWidth = 600;
constexpr uint32_t kOfflineEyeHeight = 593;
constexpr size_t kOfflineEyeBytes = size_t(kOfflineEyeWidth) * kOfflineEyeHeight * 4;
struct OfflineFrame {
	std::vector<unsigned char> pixels;
	uint32_t contours[2]{};
	uint32_t width{kOfflineEyeWidth}, height{kOfflineEyeHeight};
};
struct StreamSource {
	std::wstring annotator, camera, ffmpeg;
	bool synthetic{};
};
struct AnnotatorChild {
	HANDLE read{};
	PROCESS_INFORMATION process{};
	~AnnotatorChild()
	{
		if (read) { CloseHandle(read); }
		if (process.hProcess) {
			if (WaitForSingleObject(process.hProcess, 0) == WAIT_TIMEOUT) {
				TerminateProcess(process.hProcess, 1);
				WaitForSingleObject(process.hProcess, 3000);
			}
			CloseHandle(process.hThread);
			CloseHandle(process.hProcess);
		}
	}
};
void read_exact(HANDLE pipe, HANDLE process, void *destination, size_t bytes,
                std::chrono::steady_clock::time_point deadline)
{
	auto *out = static_cast<unsigned char *>(destination);
	size_t done = 0;
	while (done < bytes) {
		if (std::chrono::steady_clock::now() >= deadline) { throw std::runtime_error("Annotator pipe timed out"); }
		DWORD available = 0;
		if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
			throw std::runtime_error("Annotator pipe closed before complete frame");
		}
		if (!available) {
			if (WaitForSingleObject(process, 0) == WAIT_OBJECT_0) {
				throw std::runtime_error("Annotator exited before complete frame");
			}
			Sleep(2);
			continue;
		}
		DWORD count = 0;
		const DWORD wanted = DWORD(std::min<size_t>(available, bytes - done));
		if (!ReadFile(pipe, out + done, wanted, &count, nullptr) || !count) {
			throw std::runtime_error("Annotator pipe read failed");
		}
		done += count;
	}
}
void start_stream(AnnotatorChild &child, const StreamSource &source)
{
	if (source.annotator.empty() || source.annotator.find(L'"') != std::wstring::npos ||
	    (!source.synthetic && (source.camera.empty() || source.ffmpeg.empty() ||
	                           source.camera.find(L'"') != std::wstring::npos ||
	                           source.ffmpeg.find(L'"') != std::wstring::npos))) {
		throw std::runtime_error("Invalid stream arguments");
	}
	HANDLE writer = nullptr;
	SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
	if (!CreatePipe(&child.read, &writer, &security, 0)) { throw std::runtime_error("Stream pipe creation failed"); }
	try {
		if (!SetHandleInformation(child.read, HANDLE_FLAG_INHERIT, 0)) { throw std::runtime_error("Stream pipe guard failed"); }
		STARTUPINFOW startup{};
		startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
		startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
		startup.hStdOutput = writer; startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
		std::wstring command = L"\"" + source.annotator + L"\"";
		command += source.synthetic ? L" --synthetic-stream-rgba"
		                            : L" --consented-stream-rgba \"" + source.camera + L"\" \"" + source.ffmpeg + L"\"";
		if (!CreateProcessW(source.annotator.c_str(), command.data(), nullptr, nullptr, TRUE,
		                    CREATE_NO_WINDOW, nullptr, nullptr, &startup, &child.process)) {
			throw std::runtime_error("Stream process launch failed");
		}
		CloseHandle(writer);
		return;
	} catch (...) { if (writer) { CloseHandle(writer); } throw; }
}
OfflineFrame read_stream_frame(AnnotatorChild &child)
{
	const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
	std::array<uint32_t, 6> header{};
	read_exact(child.read, child.process.hProcess, header.data(), sizeof(header), deadline);
	if (header[0] != 0x42575258u || header[1] != 1u || header[2] != 960u || header[3] != 960u ||
	    header[4] > 4 || header[5] > 4) { throw std::runtime_error("Stream frame header invalid"); }
	OfflineFrame frame;
	frame.width = header[2]; frame.height = header[3];
	frame.contours[0] = header[4]; frame.contours[1] = header[5];
	frame.pixels.resize(size_t(2) * frame.width * frame.height * 4);
	read_exact(child.read, child.process.hProcess, frame.pixels.data(), frame.pixels.size(), deadline);
	return frame;
}
OfflineFrame read_annotated_screenshot(const std::wstring &screenshot, const std::wstring &annotator)
{
	if (screenshot.empty() || annotator.empty() || screenshot.find(L'"') != std::wstring::npos ||
	    annotator.find(L'"') != std::wstring::npos) {
		throw std::runtime_error("Invalid screenshot or annotator path");
	}
	AnnotatorChild child;
	HANDLE writer = nullptr;
	SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
	if (!CreatePipe(&child.read, &writer, &security, 0)) { throw std::runtime_error("Annotator pipe creation failed"); }
	try {
		if (!SetHandleInformation(child.read, HANDLE_FLAG_INHERIT, 0)) {
			throw std::runtime_error("Annotator pipe inheritance guard failed");
		}
		STARTUPINFOW startup{};
		startup.cb = sizeof(startup);
		startup.dwFlags = STARTF_USESTDHANDLES;
		startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
		startup.hStdOutput = writer;
		startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
		std::wstring command = L"\"" + annotator + L"\" --known-user-screenshot-rgba \"" + screenshot + L"\"";
		if (!CreateProcessW(annotator.c_str(), command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
		                    nullptr, nullptr, &startup, &child.process)) {
			throw std::runtime_error("Annotator process launch failed");
		}
		CloseHandle(writer); writer = nullptr;
		const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
		std::array<uint32_t, 6> header{};
		read_exact(child.read, child.process.hProcess, header.data(), sizeof(header), deadline);
		if (header[0] != 0x42575258u || header[1] != 1u || header[2] != kOfflineEyeWidth ||
		    header[3] != kOfflineEyeHeight || !header[4] || !header[5]) {
			throw std::runtime_error("Annotator frame header or contour counts invalid");
		}
		OfflineFrame frame;
		frame.contours[0] = header[4]; frame.contours[1] = header[5];
		frame.pixels.resize(2 * kOfflineEyeBytes);
		read_exact(child.read, child.process.hProcess, frame.pixels.data(), frame.pixels.size(), deadline);
		const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline -
		                       std::chrono::steady_clock::now()).count();
		if (remaining < 0 || WaitForSingleObject(child.process.hProcess, DWORD(remaining)) != WAIT_OBJECT_0) {
			throw std::runtime_error("Annotator did not exit within frame deadline");
		}
		DWORD exit_code = 1, trailing = 0;
		if (!GetExitCodeProcess(child.process.hProcess, &exit_code) || exit_code != 0 ||
		    (PeekNamedPipe(child.read, nullptr, 0, nullptr, &trailing, nullptr) && trailing)) {
			throw std::runtime_error("Annotator failed or emitted extra bytes");
		}
		return frame;
	} catch (...) {
		if (writer) { CloseHandle(writer); }
		throw;
	}
}
struct Context {
	HWND window{};
	HDC dc{};
	HGLRC gl{};
	void release()
	{
		wglMakeCurrent(nullptr, nullptr);
		if (gl) { wglDeleteContext(gl); gl = nullptr; }
		if (dc) { ReleaseDC(window, dc); dc = nullptr; }
		if (window) { DestroyWindow(window); window = nullptr; }
	}
	Context()
	{
		try {
		window = CreateWindowExW(0, L"STATIC", L"XR simulator texture source", WS_POPUP,
		                         0, 0, 32, 32, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
		if (!window || !(dc = GetDC(window))) { throw std::runtime_error("WGL hidden window failed"); }
		PIXELFORMATDESCRIPTOR pfd{};
		pfd.nSize = sizeof(pfd); pfd.nVersion = 1;
		pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
		pfd.iPixelType = PFD_TYPE_RGBA; pfd.cColorBits = 32;
		const int format = ChoosePixelFormat(dc, &pfd);
		if (!format || !SetPixelFormat(dc, format, &pfd) || !(gl = wglCreateContext(dc)) ||
		    !wglMakeCurrent(dc, gl)) { throw std::runtime_error("WGL context failed"); }
		} catch (...) { release(); throw; }
	}
	~Context() { release(); }
};
struct Eye {
	XrSwapchain chain{XR_NULL_HANDLE};
	std::vector<XrSwapchainImageOpenGLKHR> images;
	std::vector<unsigned char> pixels;
	uint32_t width{}, height{};
	~Eye() { if (chain) { xrDestroySwapchain(chain); } }
};
void fill_eye(Eye &eye, int index, const OfflineFrame *offline)
{
	eye.pixels.resize(size_t(eye.width) * eye.height * 4);
	if (offline) {
		const size_t eye_bytes = size_t(offline->width) * offline->height * 4;
		for (uint32_t y = 0; y < eye.height; ++y) {
			for (uint32_t x = 0; x < eye.width; ++x) {
				const size_t from = size_t(index) * eye_bytes +
				                    (size_t(y) * offline->height / eye.height * offline->width +
				                     size_t(x) * offline->width / eye.width) * 4;
				const size_t to = (size_t(y) * eye.width + x) * 4;
				std::memcpy(eye.pixels.data() + to, offline->pixels.data() + from, 4);
			}
		}
		return;
	}
	for (uint32_t y = 0; y < eye.height; ++y) {
		for (uint32_t x = 0; x < eye.width; ++x) {
			const size_t offset = (size_t(y) * eye.width + x) * 4;
			const bool marker = x < eye.width / 8 && y < eye.height / 8;
			eye.pixels[offset + 0] = index == 0 ? 80 : 0;
			eye.pixels[offset + 1] = marker ? 255 : 0;
			eye.pixels[offset + 2] = index == 1 ? 80 : 0;
			eye.pixels[offset + 3] = 255;
		}
	}
	if (eye.pixels[0] != (index == 0 ? 80 : 0) || eye.pixels[2] != (index == 1 ? 80 : 0) ||
	    eye.pixels[1] != 255) { throw std::runtime_error("synthetic eye-order assertion failed"); }
}
void upload(Eye &eye, bool verify_pixels)
{
	uint32_t image_index = 0;
	XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
	check(xrAcquireSwapchainImage(eye.chain, &acquire, &image_index), "xrAcquireSwapchainImage");
	XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
	wait.timeout = XR_INFINITE_DURATION;
	check(xrWaitSwapchainImage(eye.chain, &wait), "xrWaitSwapchainImage");
	glBindTexture(GL_TEXTURE_2D, eye.images.at(image_index).image);
	glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
	glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, GLsizei(eye.width), GLsizei(eye.height),
	                GL_RGBA, GL_UNSIGNED_BYTE, eye.pixels.data());
	if (glGetError() != GL_NO_ERROR) { throw std::runtime_error("OpenGL eye upload failed"); }
	if (verify_pixels) {
		std::vector<unsigned char> readback(eye.pixels.size());
		glPixelStorei(GL_PACK_ALIGNMENT, 1);
		glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, readback.data());
		if (glGetError() != GL_NO_ERROR || readback != eye.pixels) {
			throw std::runtime_error("Annotated eye pixels differ after GPU upload");
		}
	}
	glFinish();
	XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
	check(xrReleaseSwapchainImage(eye.chain, &release), "xrReleaseSwapchainImage");
}
int run(const OfflineFrame *offline, const StreamSource *source = nullptr)
{
	require_simulator();
	XrInstance instance = XR_NULL_HANDLE;
	XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO};
	std::strcpy(create.applicationInfo.applicationName,
	            source ? "Monado stream stereo smoke" : offline ? "Monado screenshot stereo smoke" : "Monado synthetic stereo smoke");
	create.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
	const char *extension = XR_KHR_OPENGL_ENABLE_EXTENSION_NAME;
	create.enabledExtensionCount = 1; create.enabledExtensionNames = &extension;
	check(xrCreateInstance(&create, &instance), "xrCreateInstance");
	try {
		XrSystemGetInfo system_info{XR_TYPE_SYSTEM_GET_INFO};
		system_info.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
		XrSystemId system = XR_NULL_SYSTEM_ID;
		check(xrGetSystem(instance, &system_info, &system), "xrGetSystem");
		PFN_xrGetOpenGLGraphicsRequirementsKHR requirements_fn{};
		check(xrGetInstanceProcAddr(instance, "xrGetOpenGLGraphicsRequirementsKHR",
		                            reinterpret_cast<PFN_xrVoidFunction *>(&requirements_fn)), "xrGetInstanceProcAddr");
		XrGraphicsRequirementsOpenGLKHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_KHR};
		check(requirements_fn(instance, system, &requirements), "xrGetOpenGLGraphicsRequirementsKHR");
		Context context;
		const char *gl_version = reinterpret_cast<const char *>(glGetString(GL_VERSION));
		unsigned major = 0, minor = 0;
		if (!gl_version || std::sscanf(gl_version, "%u.%u", &major, &minor) != 2 ||
		    XR_MAKE_VERSION(major, minor, 0) < requirements.minApiVersionSupported) {
			throw std::runtime_error("WGL context below runtime OpenGL requirement");
		}
		XrGraphicsBindingOpenGLWin32KHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_WIN32_KHR};
		binding.hDC = context.dc; binding.hGLRC = context.gl;
		XrSessionCreateInfo session_info{XR_TYPE_SESSION_CREATE_INFO};
		session_info.next = &binding; session_info.systemId = system;
		XrSession session = XR_NULL_HANDLE;
		check(xrCreateSession(instance, &session_info, &session), "xrCreateSession");
		try {
			uint32_t count = 0;
			check(xrEnumerateViewConfigurationViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
			                                        0, &count, nullptr), "xrEnumerateViewConfigurationViews count");
			if (count != 2) { throw std::runtime_error("Expected exactly two stereo views"); }
			std::array<XrViewConfigurationView, 2> config{{{XR_TYPE_VIEW_CONFIGURATION_VIEW}, {XR_TYPE_VIEW_CONFIGURATION_VIEW}}};
			check(xrEnumerateViewConfigurationViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
			                                        2, &count, config.data()), "xrEnumerateViewConfigurationViews");
			uint32_t format_count = 0;
			check(xrEnumerateSwapchainFormats(session, 0, &format_count, nullptr), "xrEnumerateSwapchainFormats count");
			std::vector<int64_t> formats(format_count);
			check(xrEnumerateSwapchainFormats(session, format_count, &format_count, formats.data()), "xrEnumerateSwapchainFormats");
			if (std::find(formats.begin(), formats.end(), int64_t(GL_RGBA8)) == formats.end()) {
				throw std::runtime_error("GL_RGBA8 swapchain unavailable");
			}
			std::array<Eye, 2> eyes;
			for (int eye_index = 0; eye_index < 2; ++eye_index) {
				Eye &eye = eyes[eye_index];
				eye.width = config[eye_index].recommendedImageRectWidth;
				eye.height = config[eye_index].recommendedImageRectHeight;
				if (!eye.width || !eye.height) { throw std::runtime_error("Invalid eye dimensions"); }
				fill_eye(eye, eye_index, offline);
				XrSwapchainCreateInfo info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
				info.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
				info.format = GL_RGBA8; info.sampleCount = 1; info.width = eye.width; info.height = eye.height;
				info.faceCount = 1; info.arraySize = 1; info.mipCount = 1;
				check(xrCreateSwapchain(session, &info, &eye.chain), "xrCreateSwapchain");
				uint32_t image_count = 0;
				check(xrEnumerateSwapchainImages(eye.chain, 0, &image_count, nullptr), "xrEnumerateSwapchainImages count");
				eye.images.resize(image_count);
				for (auto &image : eye.images) { image.type = XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_KHR; }
				check(xrEnumerateSwapchainImages(eye.chain, image_count, &image_count,
				                                 reinterpret_cast<XrSwapchainImageBaseHeader *>(eye.images.data())),
				      "xrEnumerateSwapchainImages");
			}
			XrReferenceSpaceCreateInfo space_info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
			space_info.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
			space_info.poseInReferenceSpace.orientation.w = 1;
			XrSpace space = XR_NULL_HANDLE;
			check(xrCreateReferenceSpace(session, &space_info, &space), "xrCreateReferenceSpace");
			try {
				AnnotatorChild stream;
				if (source) { start_stream(stream, *source); }
				bool begun = false;
				unsigned submitted = 0;
				const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
				while (submitted < 3 && std::chrono::steady_clock::now() < deadline) {
					XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
					XrResult poll_result = XR_SUCCESS;
					while ((poll_result = xrPollEvent(instance, &event)) == XR_SUCCESS) {
						if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
							const auto &state = *reinterpret_cast<const XrEventDataSessionStateChanged *>(&event);
							if (state.state == XR_SESSION_STATE_READY && !begun) {
								XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
								begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
								check(xrBeginSession(session, &begin), "xrBeginSession"); begun = true;
							}
						}
						event = {XR_TYPE_EVENT_DATA_BUFFER};
					}
					if (poll_result != XR_EVENT_UNAVAILABLE) { check(poll_result, "xrPollEvent"); }
					if (!begun) { Sleep(10); continue; }
					XrFrameWaitInfo wait{XR_TYPE_FRAME_WAIT_INFO}; XrFrameState frame{XR_TYPE_FRAME_STATE};
					check(xrWaitFrame(session, &wait, &frame), "xrWaitFrame");
					XrFrameBeginInfo frame_begin{XR_TYPE_FRAME_BEGIN_INFO};
					check(xrBeginFrame(session, &frame_begin), "xrBeginFrame");
					std::array<XrView, 2> views{{{XR_TYPE_VIEW}, {XR_TYPE_VIEW}}};
					XrViewState view_state{XR_TYPE_VIEW_STATE};
					XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO};
					locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
					locate.displayTime = frame.predictedDisplayTime; locate.space = space;
					uint32_t view_count = 0;
					check(xrLocateViews(session, &locate, &view_state, 2, &view_count, views.data()), "xrLocateViews");
					if (view_count != 2) { throw std::runtime_error("Stereo eye-order count changed"); }
					OfflineFrame streamed;
					if (source && frame.shouldRender) {
						streamed = read_stream_frame(stream);
						for (int i = 0; i < 2; ++i) { fill_eye(eyes[i], i, &streamed); }
					}
					std::array<XrCompositionLayerProjectionView, 2> projection{{{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW},
					                                                        {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}}};
					for (int i = 0; i < 2; ++i) {
						upload(eyes[i], (offline && submitted == 0) || source);
						projection[i].pose = views[i].pose; projection[i].fov = views[i].fov;
						projection[i].subImage.swapchain = eyes[i].chain;
						projection[i].subImage.imageRect.extent = {int32_t(eyes[i].width), int32_t(eyes[i].height)};
						if (projection[i].subImage.swapchain != eyes[i].chain) {
							throw std::runtime_error("eye-order swapchain assertion failed");
						}
					}
					XrCompositionLayerProjection layer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
					layer.space = space; layer.viewCount = 2; layer.views = projection.data();
					const XrCompositionLayerBaseHeader *layers[] = {
					    reinterpret_cast<const XrCompositionLayerBaseHeader *>(&layer)};
					XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};
					end.displayTime = frame.predictedDisplayTime;
					end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
					end.layerCount = frame.shouldRender ? 1 : 0;
					end.layers = frame.shouldRender ? layers : nullptr;
					check(xrEndFrame(session, &end), "xrEndFrame");
					if (end.layerCount == 1) { ++submitted; }
				}
				if (submitted != 3) { throw std::runtime_error("Projection layers were not submitted"); }
				check(xrDestroySpace(space), "xrDestroySpace"); space = XR_NULL_HANDLE;
				for (auto &eye : eyes) {
					check(xrDestroySwapchain(eye.chain), "xrDestroySwapchain");
					eye.chain = XR_NULL_HANDLE;
				}
				if (source) {
					std::printf("MONADO_SIM_STREAM_PASS frames=%u views=2 layers_submitted=%u "
					            "source=%s gpu_upload_readback=1 images_saved=0 index_display_verified=0\n",
					            submitted, submitted, source->synthetic ? "synthetic" : "consented_camera");
				} else if (offline) {
					std::printf("MONADO_SIM_OFFLINE_SCREENSHOT_PASS frames=%u views=2 "
					            "left_contours=%u right_contours=%u layers_submitted=%u "
					            "gpu_upload_readback=1 camera_accessed=0 images_saved=0 index_display_verified=0\n",
					            submitted, offline->contours[0], offline->contours[1], submitted);
				} else {
					std::printf("MONADO_SIM_PROJECTION_PASS frames=%u views=2 eye_order=left_red_right_blue "
					            "layers_submitted=%u synthetic=1 camera_accessed=0 images_saved=0 index_display_verified=0\n",
					            submitted, submitted);
				}
			}
			catch (...) { if (space) { xrDestroySpace(space); } throw; }
		}
		catch (...) { xrDestroySession(session); throw; }
		check(xrDestroySession(session), "xrDestroySession");
	}
	catch (...) { xrDestroyInstance(instance); throw; }
	check(xrDestroyInstance(instance), "xrDestroyInstance");
	return 0;
}
}
int wmain(int argc, wchar_t **argv)
{
	try {
		require_simulator();
		if (argc == 1) { return run(nullptr); }
		if (argc == 4 && std::wcscmp(argv[1], L"--offline-screenshot") == 0) {
			const OfflineFrame frame = read_annotated_screenshot(argv[2], argv[3]);
			return run(&frame);
		}
		if (argc == 3 && std::wcscmp(argv[1], L"--synthetic-stream") == 0) {
			StreamSource source{argv[2], L"", L"", true};
			return run(nullptr, &source);
		}
		if (argc == 5 && std::wcscmp(argv[1], L"--consented-stream") == 0) {
			StreamSource source{argv[4], argv[2], argv[3], false};
			return run(nullptr, &source);
		}
		throw std::runtime_error("Expected no arguments, --offline-screenshot <image> <annotator>, --synthetic-stream <annotator>, or --consented-stream <camera> <ffmpeg> <annotator>");
	}
	catch (const std::exception &error) { std::fprintf(stderr, "MONADO_SIM_PROJECTION_FAIL %s\n", error.what()); return 1; }
}
