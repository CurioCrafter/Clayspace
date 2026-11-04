// Metadata-only Index HID collection inventory. Does not read or write reports.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <hidpi.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int os_hid_win32_path_matches(const wchar_t *path, uint16_t vendor_id, uint16_t product_id, int interface_number);

int main(int argc, char **argv)
{
    int interface_number = argc == 2 && strcmp(argv[1], "--sensors") == 0 ? 0 : 1;
    if (argc > 2 || (argc == 2 && interface_number != 0)) { return 2; }
    GUID hid_guid;
    HidD_GetHidGuid(&hid_guid);
    HDEVINFO devices = SetupDiGetClassDevsW(&hid_guid, NULL, NULL, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devices == INVALID_HANDLE_VALUE) { return 1; }
    unsigned matches = 0;
    for (DWORD index = 0;; ++index) {
        SP_DEVICE_INTERFACE_DATA found = {.cbSize = sizeof(found)};
        if (!SetupDiEnumDeviceInterfaces(devices, NULL, &hid_guid, index, &found)) { break; }
        DWORD required = 0;
        SetupDiGetDeviceInterfaceDetailW(devices, &found, NULL, 0, &required, NULL);
        if (required < sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)) { continue; }
        SP_DEVICE_INTERFACE_DETAIL_DATA_W *detail = (SP_DEVICE_INTERFACE_DETAIL_DATA_W *)calloc(1, required);
        if (!detail) { break; }
        detail->cbSize = sizeof(*detail);
        if (SetupDiGetDeviceInterfaceDetailW(devices, &found, detail, required, NULL, NULL) &&
            os_hid_win32_path_matches(detail->DevicePath, 0x28de, 0x2300, interface_number)) {
            ++matches;
            HANDLE handle = CreateFileW(detail->DevicePath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                        NULL, OPEN_EXISTING, 0, NULL);
            if (handle == INVALID_HANDLE_VALUE) {
                printf("INDEX_MI%02d_CAPS collection=%u metadata_open_error=%lu\n", interface_number, matches, GetLastError());
            } else {
                HIDD_ATTRIBUTES attributes = {.Size = sizeof(attributes)};
                BOOL attributes_ok = HidD_GetAttributes(handle, &attributes);
                DWORD attributes_error = attributes_ok ? 0 : GetLastError();
                printf("INDEX_MI%02d_ATTRIBUTES collection=%u ok=%u error=%lu vid=0x%04x pid=0x%04x\n",
                       interface_number, matches, attributes_ok, attributes_error,
                       attributes.VendorID, attributes.ProductID);
                PHIDP_PREPARSED_DATA preparsed = NULL;
                if (!HidD_GetPreparsedData(handle, &preparsed)) {
                    printf("INDEX_MI%02d_CAPS collection=%u preparsed_error=%lu\n", interface_number, matches, GetLastError());
                } else {
                    HIDP_CAPS caps = {0};
                    NTSTATUS status = HidP_GetCaps(preparsed, &caps);
                    printf("INDEX_MI%02d_CAPS collection=%u status=0x%08lx input=%u output=%u feature=%u usage_page=0x%04x usage=0x%04x\n",
                           interface_number, matches, (unsigned long)status, caps.InputReportByteLength,
                           caps.OutputReportByteLength, caps.FeatureReportByteLength,
                           caps.UsagePage, caps.Usage);
                    if (status == HIDP_STATUS_SUCCESS && interface_number == 0) {
                        unsigned char ids[256] = {0};
                        USHORT value_count = caps.NumberFeatureValueCaps;
                        HIDP_VALUE_CAPS *values = value_count ? (HIDP_VALUE_CAPS *)calloc(value_count, sizeof(*values)) : NULL;
                        if (values && HidP_GetValueCaps(HidP_Feature, values, &value_count, preparsed) == HIDP_STATUS_SUCCESS) {
                            for (USHORT i = 0; i < value_count; i++) { ids[values[i].ReportID] = 1; }
                        }
                        free(values);
                        USHORT button_count = caps.NumberFeatureButtonCaps;
                        HIDP_BUTTON_CAPS *buttons = button_count ? (HIDP_BUTTON_CAPS *)calloc(button_count, sizeof(*buttons)) : NULL;
                        if (buttons && HidP_GetButtonCaps(HidP_Feature, buttons, &button_count, preparsed) == HIDP_STATUS_SUCCESS) {
                            for (USHORT i = 0; i < button_count; i++) { ids[buttons[i].ReportID] = 1; }
                        }
                        free(buttons);
                        printf("INDEX_MI00_FEATURE_IDS collection=%u", matches);
                        for (int id = 0; id < 256; id++) { if (ids[id]) { printf(" %02x", id); } }
                        printf("\n");
                    }
                    HidD_FreePreparsedData(preparsed);
                }
                CloseHandle(handle);
            }
            HANDLE input = CreateFileW(detail->DevicePath, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                       NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
            DWORD input_error = input == INVALID_HANDLE_VALUE ? GetLastError() : 0;
            printf("INDEX_MI%02d_INPUT_OPEN collection=%u opened=%u error=%lu reports_accessed=0\n",
                   interface_number, matches, input != INVALID_HANDLE_VALUE, input_error);
            if (input != INVALID_HANDLE_VALUE) { CloseHandle(input); }
        }
        free(detail);
    }
    SetupDiDestroyDeviceInfoList(devices);
    printf("INDEX_MI%02d_CAPS_DONE collections=%u reports_accessed=0\n", interface_number, matches);
    return matches ? 0 : 2;
}
