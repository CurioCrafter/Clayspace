// Read-only Vulkan display enumeration. Never acquires a display or creates a surface.
#include <vulkan/vulkan.h>

#include <cstdio>
#include <cstring>
#include <vector>

int main()
{
    uint32_t extension_count = 0;
    if (vkEnumerateInstanceExtensionProperties(nullptr, &extension_count, nullptr) != VK_SUCCESS) return 1;
    std::vector<VkExtensionProperties> extensions(extension_count);
    if (vkEnumerateInstanceExtensionProperties(nullptr, &extension_count, extensions.data()) != VK_SUCCESS) return 1;
    bool has_display = false;
    for (const auto &extension : extensions) {
        has_display |= std::strcmp(extension.extensionName, VK_KHR_DISPLAY_EXTENSION_NAME) == 0;
    }
    if (!has_display) {
        std::puts("VK_KHR_display unavailable");
        return 2;
    }

    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "Read-only display probe";
    app.apiVersion = VK_API_VERSION_1_0;
    const char *instance_extensions[] = {VK_KHR_DISPLAY_EXTENSION_NAME};
    VkInstanceCreateInfo create{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    create.pApplicationInfo = &app;
    create.enabledExtensionCount = 1;
    create.ppEnabledExtensionNames = instance_extensions;
    VkInstance instance = VK_NULL_HANDLE;
    VkResult result = vkCreateInstance(&create, nullptr, &instance);
    if (result != VK_SUCCESS) {
        std::printf("vkCreateInstance failed: %d\n", result);
        return 3;
    }

    int exit_code = 0;
    auto get_displays = reinterpret_cast<PFN_vkGetPhysicalDeviceDisplayPropertiesKHR>(
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceDisplayPropertiesKHR"));
    auto get_modes = reinterpret_cast<PFN_vkGetDisplayModePropertiesKHR>(
        vkGetInstanceProcAddr(instance, "vkGetDisplayModePropertiesKHR"));
    uint32_t gpu_count = 0;
    result = vkEnumeratePhysicalDevices(instance, &gpu_count, nullptr);
    if (result != VK_SUCCESS || !get_displays || !get_modes) {
        std::printf("Vulkan display query unavailable: %d\n", result);
        exit_code = 4;
    } else {
        std::vector<VkPhysicalDevice> gpus(gpu_count);
        result = vkEnumeratePhysicalDevices(instance, &gpu_count, gpus.data());
        if (result != VK_SUCCESS) {
            std::printf("vkEnumeratePhysicalDevices failed: %d\n", result);
            exit_code = 5;
        } else {
            for (uint32_t gpu = 0; gpu < gpu_count; ++gpu) {
                VkPhysicalDeviceProperties gpu_properties{};
                vkGetPhysicalDeviceProperties(gpus[gpu], &gpu_properties);
                uint32_t display_count = 0;
                result = get_displays(gpus[gpu], &display_count, nullptr);
                std::printf("GPU %u: %s; display query=%d; displays=%u\n", gpu,
                            gpu_properties.deviceName, result, display_count);
                if (result != VK_SUCCESS) { exit_code = 6; continue; }
                std::vector<VkDisplayPropertiesKHR> displays(display_count);
                if (display_count && get_displays(gpus[gpu], &display_count, displays.data()) != VK_SUCCESS) {
                    exit_code = 7;
                    continue;
                }
                for (uint32_t display = 0; display < display_count; ++display) {
                    const auto &p = displays[display];
                    uint32_t mode_count = 0;
                    result = get_modes(gpus[gpu], p.display, &mode_count, nullptr);
                    std::printf("  display %u: %s; %ux%u; modes=%u; mode query=%d\n", display,
                                p.displayName ? p.displayName : "(unnamed)", p.physicalResolution.width,
                                p.physicalResolution.height, mode_count, result);
                    if (result != VK_SUCCESS) exit_code = 8;
                }
            }
        }
    }
    vkDestroyInstance(instance, nullptr);
    return exit_code;
}
