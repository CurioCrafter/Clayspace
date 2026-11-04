// Copyright 2026, Andrew Rainsberger
// SPDX-License-Identifier: BSL-1.0
// Process-isolated, synthetic-only physical Index OpenXR frame probe.
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
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(XrResult result, const char *step)
{
    if (XR_FAILED(result)) { throw std::runtime_error(std::string(step) + " result=" + std::to_string(result)); }
}
void step(const char *name, XrResult result)
{
    std::printf("MONADO_INDEX_FRAME_STEP stage=%s result=%d\n", name, result);
    std::fflush(stdout);
    check(result, name);
}
struct Probe {
    XrInstance instance{XR_NULL_HANDLE};
    XrSession session{XR_NULL_HANDLE};
    XrSpace space{XR_NULL_HANDLE};
    std::array<XrSwapchain, 2> chains{XR_NULL_HANDLE, XR_NULL_HANDLE};
    HWND window{};
    HDC dc{};
    HGLRC gl{};
    ~Probe()
    {
        if (space) { xrDestroySpace(space); }
        for (auto chain : chains) { if (chain) { xrDestroySwapchain(chain); } }
        if (session) { xrDestroySession(session); }
        if (instance) { xrDestroyInstance(instance); }
        wglMakeCurrent(nullptr, nullptr);
        if (gl) { wglDeleteContext(gl); }
        if (dc) { ReleaseDC(window, dc); }
        if (window) { DestroyWindow(window); }
    }
    void make_gl()
    {
        window = CreateWindowExW(0, L"STATIC", L"Index OpenXR frame probe", WS_POPUP,
                                 0, 0, 32, 32, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        if (!window || !(dc = GetDC(window))) { throw std::runtime_error("WGL hidden window creation failed"); }
        PIXELFORMATDESCRIPTOR pfd{};
        pfd.nSize = sizeof(pfd); pfd.nVersion = 1;
        pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
        pfd.iPixelType = PFD_TYPE_RGBA; pfd.cColorBits = 32;
        int format = ChoosePixelFormat(dc, &pfd);
        if (!format || !SetPixelFormat(dc, format, &pfd) || !(gl = wglCreateContext(dc)) ||
            !wglMakeCurrent(dc, gl)) { throw std::runtime_error("WGL context creation failed"); }
    }
};
struct Eye {
    uint32_t width{}, height{};
    std::vector<XrSwapchainImageOpenGLKHR> images;
    std::vector<uint8_t> pixels;
    std::vector<bool> uploaded;
};
void upload(XrSwapchain chain, Eye &eye)
{
    uint32_t image_index = 0;
    XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
    check(xrAcquireSwapchainImage(chain, &acquire, &image_index), "xrAcquireSwapchainImage");
    bool released = false;
    try {
        XrSwapchainImageWaitInfo wait{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
        wait.timeout = XR_INFINITE_DURATION; // The owning wrapper has a hard 10-second child limit.
        check(xrWaitSwapchainImage(chain, &wait), "xrWaitSwapchainImage");
        if (!eye.uploaded.at(image_index)) {
            glBindTexture(GL_TEXTURE_2D, eye.images.at(image_index).image);
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, GLsizei(eye.width), GLsizei(eye.height),
                            GL_RGBA, GL_UNSIGNED_BYTE, eye.pixels.data());
            if (glGetError() != GL_NO_ERROR) { throw std::runtime_error("OpenGL texture upload failed"); }
            glFinish();
            eye.uploaded[image_index] = true;
        }
        XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
        check(xrReleaseSwapchainImage(chain, &release), "xrReleaseSwapchainImage");
        released = true;
    } catch (...) {
        if (!released) {
            XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
            xrReleaseSwapchainImage(chain, &release);
        }
        throw;
    }
}
void make_pixels(Eye &eye, int index)
{
    if (!eye.width || !eye.height || eye.width > 4096 || eye.height > 4096) {
        throw std::runtime_error("Unsafe or empty eye dimensions");
    }
    eye.pixels.resize(size_t(eye.width) * eye.height * 4);
    for (uint32_t y = 0; y < eye.height; y++) {
        for (uint32_t x = 0; x < eye.width; x++) {
            size_t offset = (size_t(y) * eye.width + x) * 4;
            bool corner = x < eye.width / 8 && y < eye.height / 8;
            eye.pixels[offset] = index == 0 ? 96 : 0;
            eye.pixels[offset + 1] = corner ? 192 : 0;
            eye.pixels[offset + 2] = index == 1 ? 96 : 0;
            eye.pixels[offset + 3] = 255;
        }
    }
}
void run()
{
    Probe probe;
    const char *extension = XR_KHR_OPENGL_ENABLE_EXTENSION_NAME;
    XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO};
    std::snprintf(create.applicationInfo.applicationName, sizeof(create.applicationInfo.applicationName),
                  "Monado Index stereo color probe");
    create.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
    create.enabledExtensionCount = 1;
    create.enabledExtensionNames = &extension;
    step("xrCreateInstance", xrCreateInstance(&create, &probe.instance));
    XrSystemGetInfo get{XR_TYPE_SYSTEM_GET_INFO};
    get.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    step("xrGetSystem", xrGetSystem(probe.instance, &get, &system));
    XrSystemProperties properties{XR_TYPE_SYSTEM_PROPERTIES};
    step("xrGetSystemProperties", xrGetSystemProperties(probe.instance, system, &properties));
    if (!std::strstr(properties.systemName, "Valve Index")) {
        throw std::runtime_error("The active isolated runtime did not identify a Valve Index");
    }
    PFN_xrGetOpenGLGraphicsRequirementsKHR requirements_fn{};
    step("xrGetInstanceProcAddr", xrGetInstanceProcAddr(
        probe.instance, "xrGetOpenGLGraphicsRequirementsKHR",
        reinterpret_cast<PFN_xrVoidFunction *>(&requirements_fn)));
    XrGraphicsRequirementsOpenGLKHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_KHR};
    step("xrGetOpenGLGraphicsRequirementsKHR", requirements_fn(probe.instance, system, &requirements));
    probe.make_gl();
    const char *version = reinterpret_cast<const char *>(glGetString(GL_VERSION));
    unsigned major = 0, minor = 0;
    if (!version || std::sscanf(version, "%u.%u", &major, &minor) != 2 ||
        XR_MAKE_VERSION(major, minor, 0) < requirements.minApiVersionSupported) {
        throw std::runtime_error("OpenGL context below runtime requirement");
    }
    XrGraphicsBindingOpenGLWin32KHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_WIN32_KHR};
    binding.hDC = probe.dc; binding.hGLRC = probe.gl;
    XrSessionCreateInfo session_info{XR_TYPE_SESSION_CREATE_INFO};
    session_info.next = &binding; session_info.systemId = system;
    step("xrCreateSession", xrCreateSession(probe.instance, &session_info, &probe.session));
    uint32_t count = 0;
    step("xrEnumerateViewConfigurationViews_count", xrEnumerateViewConfigurationViews(
        probe.instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 0, &count, nullptr));
    if (count != 2) { throw std::runtime_error("Index runtime did not expose two stereo views"); }
    std::array<XrViewConfigurationView, 2> config{{{XR_TYPE_VIEW_CONFIGURATION_VIEW},
                                                    {XR_TYPE_VIEW_CONFIGURATION_VIEW}}};
    step("xrEnumerateViewConfigurationViews", xrEnumerateViewConfigurationViews(
        probe.instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 2, &count, config.data()));
    uint32_t format_count = 0;
    step("xrEnumerateSwapchainFormats_count", xrEnumerateSwapchainFormats(probe.session, 0, &format_count, nullptr));
    std::vector<int64_t> formats(format_count);
    step("xrEnumerateSwapchainFormats", xrEnumerateSwapchainFormats(
        probe.session, format_count, &format_count, formats.data()));
    if (std::find(formats.begin(), formats.end(), int64_t(GL_RGBA8)) == formats.end()) {
        throw std::runtime_error("GL_RGBA8 swapchain unavailable");
    }
    std::array<Eye, 2> eyes;
    for (int i = 0; i < 2; i++) {
        eyes[i].width = config[i].recommendedImageRectWidth;
        eyes[i].height = config[i].recommendedImageRectHeight;
        make_pixels(eyes[i], i);
        XrSwapchainCreateInfo chain_info{XR_TYPE_SWAPCHAIN_CREATE_INFO};
        chain_info.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT;
        chain_info.format = GL_RGBA8; chain_info.sampleCount = 1;
        chain_info.width = eyes[i].width; chain_info.height = eyes[i].height;
        chain_info.faceCount = 1; chain_info.arraySize = 1; chain_info.mipCount = 1;
        step("xrCreateSwapchain", xrCreateSwapchain(probe.session, &chain_info, &probe.chains[i]));
        uint32_t image_count = 0;
        step("xrEnumerateSwapchainImages_count", xrEnumerateSwapchainImages(
            probe.chains[i], 0, &image_count, nullptr));
        eyes[i].images.resize(image_count);
        eyes[i].uploaded.resize(image_count, false);
        for (auto &image : eyes[i].images) { image.type = XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_KHR; }
        step("xrEnumerateSwapchainImages", xrEnumerateSwapchainImages(
            probe.chains[i], image_count, &image_count,
            reinterpret_cast<XrSwapchainImageBaseHeader *>(eyes[i].images.data())));
    }
    XrReferenceSpaceCreateInfo space_info{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    space_info.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    space_info.poseInReferenceSpace.orientation.w = 1;
    step("xrCreateReferenceSpace", xrCreateReferenceSpace(probe.session, &space_info, &probe.space));
    bool begun = false;
    unsigned submitted = 0;
    unsigned located = 0;
    unsigned orientation_valid = 0;
    unsigned position_valid = 0;
    unsigned both_valid = 0;
    std::chrono::steady_clock::time_point first_submit{};
    std::chrono::steady_clock::time_point last_submit{};
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline) {
        XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
        XrResult result;
        while ((result = xrPollEvent(probe.instance, &event)) == XR_SUCCESS) {
            if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                const auto &state = *reinterpret_cast<const XrEventDataSessionStateChanged *>(&event);
                if (state.state == XR_SESSION_STATE_READY && !begun) {
                    XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO};
                    begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    step("xrBeginSession", xrBeginSession(probe.session, &begin));
                    begun = true;
                    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
                }
                if (state.state == XR_SESSION_STATE_STOPPING) {
                    throw std::runtime_error("OpenXR session stopped before probe completed");
                }
            }
            event = {XR_TYPE_EVENT_DATA_BUFFER};
        }
        if (result != XR_EVENT_UNAVAILABLE) { check(result, "xrPollEvent"); }
        if (!begun) { Sleep(10); continue; }
        XrFrameWaitInfo frame_wait{XR_TYPE_FRAME_WAIT_INFO};
        XrFrameState frame{XR_TYPE_FRAME_STATE};
        check(xrWaitFrame(probe.session, &frame_wait, &frame), "xrWaitFrame");
        XrFrameBeginInfo frame_begin{XR_TYPE_FRAME_BEGIN_INFO};
        check(xrBeginFrame(probe.session, &frame_begin), "xrBeginFrame");
        if (!frame.shouldRender) {
            XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};
            end.displayTime = frame.predictedDisplayTime;
            end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
            check(xrEndFrame(probe.session, &end), "xrEndFrame");
            continue;
        }
        std::array<XrView, 2> views{{{XR_TYPE_VIEW}, {XR_TYPE_VIEW}}};
        XrViewState view_state{XR_TYPE_VIEW_STATE};
        XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO};
        locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
        locate.displayTime = frame.predictedDisplayTime; locate.space = probe.space;
        uint32_t view_count = 0;
        check(xrLocateViews(probe.session, &locate, &view_state, 2, &view_count, views.data()), "xrLocateViews");
        const bool orientation_ok = (view_state.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT) != 0;
        const bool position_ok = (view_state.viewStateFlags & XR_VIEW_STATE_POSITION_VALID_BIT) != 0;
        ++located;
        orientation_valid += orientation_ok;
        position_valid += position_ok;
        both_valid += orientation_ok && position_ok;
        if (view_count != 2 || !(view_state.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT)) {
            throw std::runtime_error("Stereo view poses are unavailable");
        }
        std::array<XrCompositionLayerProjectionView, 2> projection{{
            {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}, {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}}};
        for (int i = 0; i < 2; i++) {
            projection[i].pose = views[i].pose; projection[i].fov = views[i].fov;
            projection[i].subImage.swapchain = probe.chains[i];
            projection[i].subImage.imageRect.extent = {int32_t(eyes[i].width), int32_t(eyes[i].height)};
            upload(probe.chains[i], eyes[i]);
        }
        XrCompositionLayerProjection layer{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
        layer.space = probe.space; layer.viewCount = 2; layer.views = projection.data();
        const XrCompositionLayerBaseHeader *layers[] = {
            reinterpret_cast<const XrCompositionLayerBaseHeader *>(&layer)};
        XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO};
        end.displayTime = frame.predictedDisplayTime;
        end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        end.layerCount = 1;
        end.layers = layers;
        check(xrEndFrame(probe.session, &end), "xrEndFrame");
        auto submitted_at = std::chrono::steady_clock::now();
        if (submitted == 0) { first_submit = submitted_at; }
        last_submit = submitted_at;
        submitted++;
    }
    auto span_ms = submitted ? std::chrono::duration_cast<std::chrono::milliseconds>(
        last_submit - first_submit).count() : 0;
    if (submitted < 3 || span_ms < 4000) {
        throw std::runtime_error("Stereo projection was not sustained: frames=" +
                                 std::to_string(submitted) + " span_ms=" + std::to_string(span_ms));
    }
    std::printf("MONADO_INDEX_FRAME_PASS frames=%u span_ms=%lld views=2 located=%u orientation_valid=%u position_valid=%u both_valid=%u position_invalid=%u left=red right=blue camera_requested=0 images_saved=0\n",
                submitted, static_cast<long long>(span_ms), located, orientation_valid, position_valid,
                both_valid, located - position_valid);
    std::fflush(stdout);
}
} // namespace

int main(int argc, char **argv)
{
    if (argc != 2 || std::strcmp(argv[1], "--probe-frame") != 0) {
        std::fprintf(stderr, "Explicit --probe-frame required; no runtime started.\n");
        return 64;
    }
    const char *manifest = std::getenv("XR_RUNTIME_JSON");
    const char *extended = std::getenv("XRT_COMPOSITOR_INDEX_EXTENDED");
    if (!manifest || !std::strstr(manifest, "monado-index-experimental-build") ||
        !extended || std::strcmp(extended, "1") || std::getenv("SIMULATED_ENABLE")) {
        std::fprintf(stderr, "MONADO_INDEX_FRAME_FAIL stage=preflight isolated_physical_environment_required\n");
        return 2;
    }
    try { run(); return 0; }
    catch (const std::exception &error) {
        std::fprintf(stderr, "MONADO_INDEX_FRAME_FAIL %s\n", error.what());
        return 1;
    }
}
