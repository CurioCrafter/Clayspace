#include <openxr/openxr.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char **argv)
{
    if (argc != 2 || std::strcmp(argv[1], "--probe-system") != 0) {
        std::fprintf(stderr, "Usage: index_runtime_system_probe --probe-system\n");
        return 64;
    }
    const char *manifest = std::getenv("XR_RUNTIME_JSON");
    const char *extended = std::getenv("XRT_COMPOSITOR_INDEX_EXTENDED");
    if (!manifest || !*manifest || !extended || std::strcmp(extended, "1") != 0 ||
        std::getenv("SIMULATED_ENABLE") != nullptr) {
        std::fprintf(stderr, "MONADO_INDEX_SYSTEM_FAIL stage=preflight reason=isolated_physical_environment_required\n");
        return 2;
    }

    XrInstanceCreateInfo create{XR_TYPE_INSTANCE_CREATE_INFO};
    std::snprintf(create.applicationInfo.applicationName,
                  sizeof(create.applicationInfo.applicationName), "Monado Index system probe");
    create.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
    XrInstance instance = XR_NULL_HANDLE;
    XrResult result = xrCreateInstance(&create, &instance);
    std::printf("MONADO_INDEX_SYSTEM_STEP stage=xrCreateInstance result=%d\n", result);
    std::fflush(stdout);
    if (XR_FAILED(result)) { return 1; }

    XrSystemGetInfo get{XR_TYPE_SYSTEM_GET_INFO};
    get.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    result = xrGetSystem(instance, &get, &system);
    std::printf("MONADO_INDEX_SYSTEM_STEP stage=xrGetSystem result=%d system=%llu\n",
                result, static_cast<unsigned long long>(system));
    std::fflush(stdout);
    bool index_identified = false;
    if (XR_SUCCEEDED(result)) {
        XrSystemProperties properties{XR_TYPE_SYSTEM_PROPERTIES};
        result = xrGetSystemProperties(instance, system, &properties);
        if (XR_SUCCEEDED(result)) {
            index_identified = std::strstr(properties.systemName, "Valve Index") != nullptr;
            std::printf("MONADO_INDEX_SYSTEM_ID name=%s vendor=%u orientation=%u position=%u\n",
                        properties.systemName, properties.vendorId,
                        properties.trackingProperties.orientationTracking,
                        properties.trackingProperties.positionTracking);
        } else {
            std::printf("MONADO_INDEX_SYSTEM_STEP stage=xrGetSystemProperties result=%d\n", result);
        }
        std::fflush(stdout);
    }
    XrResult destroy_result = xrDestroyInstance(instance);
    if (XR_FAILED(result) || XR_FAILED(destroy_result) || !index_identified) {
        std::fprintf(stderr, "MONADO_INDEX_SYSTEM_FAIL stage=%s result=%d destroy=%d index_identified=%d\n",
                     XR_FAILED(result) ? "system" : XR_FAILED(destroy_result) ? "destroy" : "identity",
                     result, destroy_result, index_identified);
        return 1;
    }
    std::puts("MONADO_INDEX_SYSTEM_PASS instance=1 system=1 camera_requested=0 session=0 frame=0");
    return 0;
}
