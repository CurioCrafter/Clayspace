// Copyright 2026, Andrew Rainsberger
// SPDX-License-Identifier: BSL-1.0
/* Explicitly gated, bounded Index MI_00 config-start feature comparison.
 * Never prints the device path, container ID, report bytes, or serial number. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winioctl.h>
#include <initguid.h>
#include <devpkey.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <hidpi.h>
#include <hidclass.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int os_hid_win32_path_matches(const wchar_t *path, uint16_t vendor_id, uint16_t product_id, int interface_number);

struct candidate {
    wchar_t *path;
    GUID container;
    unsigned count;
    USHORT feature_length;
};

static int collect(struct candidate found[2])
{
    GUID hid_guid;
    HidD_GetHidGuid(&hid_guid);
    HDEVINFO devices = SetupDiGetClassDevsW(&hid_guid, NULL, NULL, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devices == INVALID_HANDLE_VALUE) { return 0; }
    for (DWORD index = 0;; index++) {
        SP_DEVICE_INTERFACE_DATA entry = {.cbSize = sizeof(entry)};
        if (!SetupDiEnumDeviceInterfaces(devices, NULL, &hid_guid, index, &entry)) { break; }
        DWORD required = 0;
        SetupDiGetDeviceInterfaceDetailW(devices, &entry, NULL, 0, &required, NULL);
        if (required < sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)) { continue; }
        SP_DEVICE_INTERFACE_DETAIL_DATA_W *detail = (SP_DEVICE_INTERFACE_DETAIL_DATA_W *)calloc(1, required);
        if (!detail) { break; }
        detail->cbSize = sizeof(*detail);
        SP_DEVINFO_DATA device = {.cbSize = sizeof(device)};
        if (SetupDiGetDeviceInterfaceDetailW(devices, &entry, detail, required, NULL, &device)) {
            int mi = os_hid_win32_path_matches(detail->DevicePath, 0x28de, 0x2300, 0) ? 0 :
                     os_hid_win32_path_matches(detail->DevicePath, 0x28de, 0x2300, 1) ? 1 : -1;
            if (mi >= 0) {
                HANDLE metadata = CreateFileW(detail->DevicePath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                              NULL, OPEN_EXISTING, 0, NULL);
                HIDD_ATTRIBUTES attributes = {.Size = sizeof(attributes)};
                GUID container;
                DEVPROPTYPE type = 0;
                if (metadata != INVALID_HANDLE_VALUE && HidD_GetAttributes(metadata, &attributes) &&
                    attributes.VendorID == 0x28de && attributes.ProductID == 0x2300 &&
                    SetupDiGetDevicePropertyW(devices, &device, &DEVPKEY_Device_ContainerId,
                                              &type, (PBYTE)&container, sizeof(container), NULL, 0) &&
                    type == DEVPROP_TYPE_GUID) {
                    found[mi].count++;
                    if (found[mi].count == 1) {
                        PHIDP_PREPARSED_DATA preparsed = NULL;
                        HIDP_CAPS caps = {0};
                        if (HidD_GetPreparsedData(metadata, &preparsed)) {
                            if (HidP_GetCaps(preparsed, &caps) == HIDP_STATUS_SUCCESS) {
                                found[mi].feature_length = caps.FeatureReportByteLength;
                            }
                            HidD_FreePreparsedData(preparsed);
                        }
                        size_t bytes = (wcslen(detail->DevicePath) + 1) * sizeof(wchar_t);
                        found[mi].path = (wchar_t *)malloc(bytes);
                        if (found[mi].path) { memcpy(found[mi].path, detail->DevicePath, bytes); }
                        found[mi].container = container;
                    }
                }
                if (metadata != INVALID_HANDLE_VALUE) { CloseHandle(metadata); }
            }
        }
        free(detail);
    }
    SetupDiDestroyDeviceInfoList(devices);
    return found[0].count == 1 && found[1].count == 1 && found[0].path != NULL &&
           found[0].feature_length == 65 && found[1].feature_length == 0 &&
           memcmp(&found[0].container, &found[1].container, sizeof(GUID)) == 0;
}

struct operation {
    HANDLE handle;
    int ioctl;
    uint8_t report_id;
    DWORD error;
    DWORD transferred;
    BOOL success;
    BOOL returned_id_ok;
    DWORD elapsed_ms;
};

static DWORD WINAPI run_feature(void *context)
{
    struct operation *op = (struct operation *)context;
    uint8_t report[65] = {0};
    report[0] = op->report_id;
    DWORD start = GetTickCount();
    if (op->ioctl) {
        OVERLAPPED pending = {0};
        pending.hEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
        if (pending.hEvent) {
            op->success = DeviceIoControl(op->handle, IOCTL_HID_GET_FEATURE, NULL, 0,
                                          report, sizeof(report), NULL, &pending);
            op->error = op->success ? 0 : GetLastError();
            if (!op->success && op->error == ERROR_IO_PENDING) {
                DWORD wait = WaitForSingleObject(pending.hEvent, 100);
                if (wait != WAIT_OBJECT_0) {
                    op->error = wait == WAIT_TIMEOUT ? WAIT_TIMEOUT : GetLastError();
                    CancelIoEx(op->handle, &pending);
                    /* The kernel can still access the report and event until completion. */
                    GetOverlappedResult(op->handle, &pending, &op->transferred, TRUE);
                }
            }
            if (op->error == 0 || op->error == ERROR_IO_PENDING) {
                op->success = GetOverlappedResult(op->handle, &pending, &op->transferred, FALSE);
                op->error = op->success ? 0 : GetLastError();
            }
            CloseHandle(pending.hEvent);
        } else { op->error = GetLastError(); }
    } else {
        op->success = HidD_GetFeature(op->handle, report, sizeof(report));
        op->error = op->success ? 0 : GetLastError();
    }
    op->elapsed_ms = GetTickCount() - start;
    op->returned_id_ok = op->success && report[0] == op->report_id;
    SecureZeroMemory(report, sizeof(report));
    return 0;
}

static DWORD compare_one(const wchar_t *path, int ioctl, uint8_t report_id)
{
    HANDLE handle = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                NULL, OPEN_EXISTING, ioctl ? FILE_FLAG_OVERLAPPED : 0, NULL);
    if (handle == INVALID_HANDLE_VALUE) {
        printf("INDEX_FEATURE id=%02x mode=%s stage=open error=%lu\n", report_id,
               ioctl ? "ioctl" : "hidd", GetLastError());
        return GetLastError();
    }
    struct operation op = {.handle = handle, .ioctl = ioctl, .report_id = report_id};
    HANDLE worker = CreateThread(NULL, 0, run_feature, &op, 0, NULL);
    if (!worker) {
        printf("INDEX_FEATURE id=%02x mode=%s stage=thread error=%lu\n", report_id,
               ioctl ? "ioctl" : "hidd", GetLastError());
        CloseHandle(handle);
        return GetLastError();
    }
    DWORD wait = WaitForSingleObject(worker, 100);
    if (wait != WAIT_OBJECT_0) {
        CancelSynchronousIo(worker);
        CancelIoEx(handle, NULL);
        printf("INDEX_FEATURE id=%02x mode=%s stage=timeout timeout_ms=100\n", report_id,
               ioctl ? "ioctl" : "hidd");
        fflush(stdout);
        if (WaitForSingleObject(worker, 200) != WAIT_OBJECT_0) { ExitProcess(3); }
    } else {
        printf("INDEX_FEATURE id=%02x mode=%s stage=complete ok=%u id_ok=%u error=%lu transferred=%lu expected=65 elapsed_ms=%lu\n",
               report_id, ioctl ? "ioctl" : "hidd", op.success != 0, op.returned_id_ok != 0,
               op.error, op.transferred, op.elapsed_ms);
    }
    CloseHandle(worker);
    CloseHandle(handle);
    return wait == WAIT_OBJECT_0 ? (op.success && op.returned_id_ok ? 0 : op.error) : WAIT_TIMEOUT;
}

int main(int argc, char **argv)
{
    int hold_second_handle = argc == 4 && strcmp(argv[3], "--hold-second-handle") == 0;
    int retry_config_start = argc == 3 && strcmp(argv[1], "--probe-config-retry") == 0;
    if ((argc != 3 && !hold_second_handle) ||
        (strcmp(argv[1], "--probe-config-start") != 0 && !retry_config_start) ||
        strcmp(argv[2], "--display-pattern-seen") != 0) {
        fprintf(stderr, "Explicit probe mode and --display-pattern-seen required.\n");
        return 2;
    }
    struct candidate found[2] = {0};
    int ready = collect(found);
    if (!ready) {
        printf("INDEX_CONFIG_START stage=identity_guard mi00_count=%u mi01_count=%u mi00_feature=%u mi01_feature=%u\n",
               found[0].count, found[1].count, found[0].feature_length, found[1].feature_length);
        free(found[0].path);
        free(found[1].path);
        return 2;
    }
    HANDLE hold = INVALID_HANDLE_VALUE;
    if (hold_second_handle) {
        hold = CreateFileW(found[0].path, GENERIC_READ | GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        if (hold == INVALID_HANDLE_VALUE) {
            printf("INDEX_FEATURE stage=second_handle_open error=%lu\n", GetLastError());
            free(found[0].path);
            free(found[1].path);
            return 3;
        }
        printf("INDEX_FEATURE stage=second_handle_open ok=1\n");
    }
    if (retry_config_start) {
        DWORD first = compare_one(found[0].path, 1, 0x10);
        if (first == ERROR_GEN_FAILURE) {
            printf("INDEX_FEATURE id=10 stage=retry reason=ERROR_GEN_FAILURE\n");
            compare_one(found[0].path, 1, 0x10);
        }
    } else {
        /* Reverse the earlier comparison order to separate API from device state. */
        compare_one(found[0].path, 1, 0x01);
        if (!hold_second_handle) {
            compare_one(found[0].path, 0, 0x01);
            /* Config-start is stateful; ask for it only once, using the bounded path. */
            compare_one(found[0].path, 1, 0x10);
        }
    }
    if (hold != INVALID_HANDLE_VALUE) { CloseHandle(hold); }
    free(found[0].path);
    free(found[1].path);
    return 0;
}
