// Read-only Vulkan display enumeration. Never acquire a display or create a surface.
#define VK_NO_PROTOTYPES
#include <windows.h>
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    HMODULE loader = LoadLibraryA("vulkan-1.dll");
    if (!loader) { fprintf(stderr, "vulkan-1.dll unavailable\n"); return 1; }
    PFN_vkGetInstanceProcAddr get = (PFN_vkGetInstanceProcAddr)GetProcAddress(loader, "vkGetInstanceProcAddr");
    if (!get) { fprintf(stderr, "vkGetInstanceProcAddr unavailable\n"); return 1; }
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)get(NULL, "vkCreateInstance");
    const char *extension = VK_KHR_DISPLAY_EXTENSION_NAME;
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "Index display probe", .apiVersion = VK_API_VERSION_1_0 };
    VkInstanceCreateInfo info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app, .enabledExtensionCount = 1, .ppEnabledExtensionNames = &extension };
    VkInstance instance = VK_NULL_HANDLE;
    VkResult result = create(&info, NULL, &instance);
    if (result != VK_SUCCESS) { fprintf(stderr, "vkCreateInstance (VK_KHR_display): %d\n", result); return 1; }

    PFN_vkDestroyInstance destroy = (PFN_vkDestroyInstance)get(instance, "vkDestroyInstance");
    PFN_vkEnumeratePhysicalDevices enumerate = (PFN_vkEnumeratePhysicalDevices)get(instance, "vkEnumeratePhysicalDevices");
    PFN_vkGetPhysicalDeviceProperties properties = (PFN_vkGetPhysicalDeviceProperties)get(instance, "vkGetPhysicalDeviceProperties");
    PFN_vkGetPhysicalDeviceDisplayPropertiesKHR displays = (PFN_vkGetPhysicalDeviceDisplayPropertiesKHR)get(instance, "vkGetPhysicalDeviceDisplayPropertiesKHR");
    PFN_vkGetDisplayModePropertiesKHR modes = (PFN_vkGetDisplayModePropertiesKHR)get(instance, "vkGetDisplayModePropertiesKHR");
    if (!destroy || !enumerate || !properties || !displays || !modes) {
        fprintf(stderr, "Vulkan display enumeration entry points unavailable\n");
        return 1;
    }

    uint32_t gpu_count = 0;
    result = enumerate(instance, &gpu_count, NULL);
    if (result != VK_SUCCESS) { fprintf(stderr, "GPU count: %d\n", result); destroy(instance, NULL); return 1; }
    VkPhysicalDevice *gpus = calloc(gpu_count ? gpu_count : 1, sizeof(*gpus));
    if (!gpus) { destroy(instance, NULL); return 1; }
    result = enumerate(instance, &gpu_count, gpus);
    if (result != VK_SUCCESS && result != VK_INCOMPLETE) { fprintf(stderr, "GPU enumeration: %d\n", result); free(gpus); destroy(instance, NULL); return 1; }
    int found_index = 0;
    for (uint32_t gpu = 0; gpu < gpu_count; ++gpu) {
        VkPhysicalDeviceProperties device;
        properties(gpus[gpu], &device);
        printf("GPU %u: %s vendor=0x%04x device=0x%04x\n", gpu, device.deviceName, device.vendorID, device.deviceID);
        uint32_t display_count = 0;
        result = displays(gpus[gpu], &display_count, NULL);
        if (result != VK_SUCCESS) { printf("  display count error=%d\n", result); continue; }
        VkDisplayPropertiesKHR *list = calloc(display_count ? display_count : 1, sizeof(*list));
        if (!list) continue;
        result = displays(gpus[gpu], &display_count, list);
        if (result != VK_SUCCESS && result != VK_INCOMPLETE) { printf("  display enumeration error=%d\n", result); free(list); continue; }
        printf("  displays=%u\n", display_count);
        for (uint32_t display = 0; display < display_count; ++display) {
            const char *name = list[display].displayName ? list[display].displayName : "(unnamed)";
            printf("  display %u: %s physical=%ux%u\n", display, name,
                   list[display].physicalResolution.width, list[display].physicalResolution.height);
            if (strstr(name, "Index HMD")) found_index = 1;
            uint32_t mode_count = 0;
            result = modes(gpus[gpu], list[display].display, &mode_count, NULL);
            if (result != VK_SUCCESS) { printf("    mode count error=%d\n", result); continue; }
            VkDisplayModePropertiesKHR *mode_list = calloc(mode_count ? mode_count : 1, sizeof(*mode_list));
            if (!mode_list) continue;
            result = modes(gpus[gpu], list[display].display, &mode_count, mode_list);
            if (result == VK_SUCCESS || result == VK_INCOMPLETE) {
                for (uint32_t mode = 0; mode < mode_count; ++mode)
                    printf("    mode %u: %ux%u @ %.3f Hz\n", mode,
                           mode_list[mode].parameters.visibleRegion.width,
                           mode_list[mode].parameters.visibleRegion.height,
                           mode_list[mode].parameters.refreshRate / 1000.0);
            } else printf("    mode enumeration error=%d\n", result);
            free(mode_list);
        }
        free(list);
    }
    printf("Index display found: %s\n", found_index ? "yes" : "no");
    free(gpus);
    destroy(instance, NULL);
    FreeLibrary(loader);
    return found_index ? 0 : 2;
}
