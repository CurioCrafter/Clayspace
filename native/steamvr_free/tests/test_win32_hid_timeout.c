// Copyright 2026, Andrew Rainsberger
// SPDX-License-Identifier: BSL-1.0
#include "os/os_hid.h"
#include <windows.h>
#include <setupapi.h>
#include <hidclass.h>
#include <hidsdi.h>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>

enum scenario
{
	IMMEDIATE,
	PENDING,
	TIMEOUT,
	SHORT_REPORT,
	DEVICE_ERROR,
	GEN_FAILURE_ONCE,
	GEN_FAILURE_ALWAYS,
	BAD_REPORT_ID,
};
static enum scenario current;
static int calls;
static int scenario_calls;
static int cancellations;
static int drains;

static BOOL WINAPI
mock_DeviceIoControl(HANDLE, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
static DWORD WINAPI
mock_WaitForSingleObject(HANDLE, DWORD);
static BOOL WINAPI
mock_CancelIoEx(HANDLE, LPOVERLAPPED);
static BOOL WINAPI
mock_GetOverlappedResult(HANDLE, LPOVERLAPPED, LPDWORD, BOOL);

#define DeviceIoControl mock_DeviceIoControl
#define WaitForSingleObject mock_WaitForSingleObject
#define CancelIoEx mock_CancelIoEx
#define GetOverlappedResult mock_GetOverlappedResult
#include "../../providers/mercury-research/monado/src/xrt/auxiliary/os/os_hid_win32.c"
#undef DeviceIoControl
#undef WaitForSingleObject
#undef CancelIoEx
#undef GetOverlappedResult

static BOOL WINAPI
mock_DeviceIoControl(HANDLE handle,
	                 DWORD code,
	                 LPVOID input,
	                 DWORD input_size,
	                 LPVOID output,
	                 DWORD output_size,
	                 LPDWORD bytes,
	                 LPOVERLAPPED operation)
{
	assert(handle == (HANDLE)1 && code == IOCTL_HID_GET_FEATURE && input == NULL && input_size == 0);
	assert(output_size == 8 && bytes == NULL && operation != NULL && operation->hEvent != NULL);
	uint8_t *report = (uint8_t *)output;
	assert(report[0] == 0x42 && report[1] == 0x55 && report[2] == 0 && report[7] == 0);
	calls++;
	scenario_calls++;
	if (current == DEVICE_ERROR) {
		SetLastError(ERROR_ACCESS_DENIED);
		return FALSE;
	}
	if (current == GEN_FAILURE_ALWAYS || (current == GEN_FAILURE_ONCE && scenario_calls == 1)) {
		report[1] = 0xee; // A retry must restore the caller's original request bytes.
		SetLastError(ERROR_GEN_FAILURE);
		return FALSE;
	}
	report[1] = 0xa1;
	if (current == BAD_REPORT_ID) { report[0] = 0x43; }
	if (current == IMMEDIATE) {
		return TRUE;
	}
	SetLastError(ERROR_IO_PENDING);
	return FALSE;
}

static DWORD WINAPI
mock_WaitForSingleObject(HANDLE event, DWORD timeout)
{
	assert(event != NULL && timeout > 0 && timeout <= 15);
	return current == TIMEOUT ? WAIT_TIMEOUT : WAIT_OBJECT_0;
}

static BOOL WINAPI
mock_CancelIoEx(HANDLE handle, LPOVERLAPPED operation)
{
	assert(handle == (HANDLE)1 && operation != NULL);
	cancellations++;
	return TRUE;
}

static BOOL WINAPI
mock_GetOverlappedResult(HANDLE handle, LPOVERLAPPED operation, LPDWORD transferred, BOOL wait)
{
	assert(handle == (HANDLE)1 && operation != NULL);
	if (current == TIMEOUT) {
		assert(wait);
		drains++;
		SetLastError(ERROR_OPERATION_ABORTED);
		return FALSE;
	}
	assert(!wait);
	*transferred = current == SHORT_REPORT ? 0 : 7; // Windows counts payload bytes after the report ID.
	return TRUE;
}

int
main(void)
{
	struct hid_win32 hid = {0};
	hid.handle = (HANDLE)1;
	hid.feature_report_length = 8;
	uint8_t report[2] = {0x42, 0x55};
	assert(hid_win32_get_feature_timeout(&hid.base, NULL, sizeof(report), 15) == -1);
	assert(hid_win32_get_feature_timeout(&hid.base, report, 9, 15) == -1);
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), INFINITE) == -1);
	assert(calls == 0);
	current = IMMEDIATE;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == 2);
	assert(report[0] == 0x42 && report[1] == 0xa1);
	report[1] = 0x55;
	current = PENDING;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == 2);
	assert(report[1] == 0xa1);
	report[1] = 0x55;
	current = SHORT_REPORT;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == -1);
	assert(report[1] == 0x55);
	report[1] = 0x55;
	current = TIMEOUT;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == -1);
	assert(report[1] == 0x55 && cancellations == 1 && drains == 1);
	current = DEVICE_ERROR;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == -1);
	assert(report[1] == 0x55 && calls == 5);
	scenario_calls = 0;
	current = GEN_FAILURE_ONCE;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == 2);
	assert(report[0] == 0x42 && report[1] == 0xa1 && scenario_calls == 2);
	report[1] = 0x55;
	scenario_calls = 0;
	current = GEN_FAILURE_ALWAYS;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == -1);
	assert(report[1] == 0x55 && scenario_calls == 2);
	scenario_calls = 0;
	current = BAD_REPORT_ID;
	assert(hid_win32_get_feature_timeout(&hid.base, report, sizeof(report), 15) == -1);
	assert(report[0] == 0x42 && report[1] == 0x55 && scenario_calls == 1);
	puts("WIN32_HID_TIMEOUT_PASS physical_opens=0");
	return 0;
}
