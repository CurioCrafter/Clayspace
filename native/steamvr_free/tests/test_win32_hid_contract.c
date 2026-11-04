// Copyright 2026, Andrew Rainsberger
// SPDX-License-Identifier: BSL-1.0
#include "os/os_hid.h"
#include "p_win32_index_group.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>

int os_hid_win32_prepare_feature_report(uint8_t *target, size_t report_length, const uint8_t *source, size_t size);
uint32_t os_hid_win32_data_access(uint16_t feature_report_length, uint16_t output_report_length);
int os_hid_win32_feature_retry_allowed(unsigned attempt, uint32_t error, uint32_t transferred,
                                       uint32_t elapsed_ms, uint32_t timeout_ms);

struct mock_hid
{
	struct os_hid_device base;
	int reads;
	int writes;
	int gets;
	int sets;
};

static int
mock_read(struct os_hid_device *base, uint8_t *data, size_t size, int timeout)
{
	struct mock_hid *mock = (struct mock_hid *)base;
	assert(size >= 2 && timeout == 12);
	mock->reads++;
	data[0] = 0x20;
	data[1] = 0x21;
	return 2;
}

static int
mock_write(struct os_hid_device *base, const uint8_t *data, size_t size)
{
	struct mock_hid *mock = (struct mock_hid *)base;
	assert(size == 2 && data[0] == 0x30);
	mock->writes++;
	return (int)size;
}

static int
mock_get(struct os_hid_device *base, uint8_t report, uint8_t *data, size_t size)
{
	struct mock_hid *mock = (struct mock_hid *)base;
	assert(report == 0x40 && size >= 2);
	mock->gets++;
	data[0] = report;
	data[1] = 0x41;
	return 2;
}

static int
mock_get_timeout(struct os_hid_device *base, void *data, size_t size, uint32_t timeout)
{
	struct mock_hid *mock = (struct mock_hid *)base;
	assert(size >= 2 && timeout == 15);
	mock->gets++;
	((uint8_t *)data)[1] = 0x42;
	return 2;
}

static int
mock_set(struct os_hid_device *base, const uint8_t *data, size_t size)
{
	struct mock_hid *mock = (struct mock_hid *)base;
	assert(size == 2 && data[0] == 0x50);
	mock->sets++;
	return (int)size;
}

int
main(void)
{
	assert(os_hid_win32_path_matches(L"\\\\?\\hid#vid_28de&pid_2300&mi_00#abc", 0x28de, 0x2300, 0));
	assert(os_hid_win32_path_matches(L"\\\\?\\HID#VID_28DE&PID_2300&MI_01#abc", 0x28de, 0x2300, 1));
	assert(os_hid_win32_path_matches(L"\\\\?\\hid#vid_28de&pid_2300&mi_00&col01#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"\\\\?\\hid#vid_28de&pid_2300&mi_00#abc", 0x28de, 0x2300, 1));
	assert(!os_hid_win32_path_matches(L"\\\\?\\hid#vid_28de&pid_2400&mi_00#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"\\\\?\\hid#other&vid_28de&pid_2300&mi_00#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"\\\\?\\hid#vid_28de&pid_2300&mi_000#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"\\\\?\\usb#vid_28de&pid_2300&mi_00#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"xvid_28de&pid_2300&mi_00#abc", 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(NULL, 0x28de, 0x2300, 0));
	assert(!os_hid_win32_path_matches(L"x", 0x28de, 0x2300, -1));
	assert(os_hid_open_win32(0x28de, 0x2300, 0, NULL) == -1);
	const uint8_t first_container[16] = {1};
	const uint8_t other_container[16] = {2};
	struct p_win32_index_group group = {0};
	p_win32_index_group_add(&group, first_container, 0);
	assert(!p_win32_index_group_ready(&group));
	p_win32_index_group_add(&group, first_container, 1);
	assert(p_win32_index_group_ready(&group));
	p_win32_index_group_add(&group, other_container, 1);
	assert(!p_win32_index_group_ready(&group));
	uint8_t padded[4] = {0xff, 0xff, 0xff, 0xff};
	const uint8_t short_report[2] = {0x50, 0x51};
	assert(os_hid_win32_prepare_feature_report(padded, sizeof(padded), short_report, sizeof(short_report)) == 0);
	assert(padded[0] == 0x50 && padded[1] == 0x51 && padded[2] == 0 && padded[3] == 0);
	assert(os_hid_win32_prepare_feature_report(padded, 1, short_report, sizeof(short_report)) == -1);
	assert(os_hid_win32_prepare_feature_report(padded, sizeof(padded), short_report, 0) == -1);
	assert(os_hid_win32_data_access(0, 0) == GENERIC_READ);
	assert(os_hid_win32_data_access(64, 0) == (GENERIC_READ | GENERIC_WRITE));
	assert(os_hid_win32_data_access(0, 64) == (GENERIC_READ | GENERIC_WRITE));
	assert(os_hid_win32_feature_retry_allowed(0, ERROR_GEN_FAILURE, 0, 0, 100));
	assert(!os_hid_win32_feature_retry_allowed(1, ERROR_GEN_FAILURE, 0, 0, 100));
	assert(!os_hid_win32_feature_retry_allowed(0, ERROR_GEN_FAILURE, 1, 0, 100));
	assert(!os_hid_win32_feature_retry_allowed(0, ERROR_ACCESS_DENIED, 0, 0, 100));
	assert(!os_hid_win32_feature_retry_allowed(0, ERROR_GEN_FAILURE, 0, 100, 100));

	struct mock_hid mock = {0};
	mock.base.read = mock_read;
	mock.base.write = mock_write;
	mock.base.get_feature = mock_get;
	mock.base.get_feature_timeout = mock_get_timeout;
	mock.base.set_feature = mock_set;
	uint8_t data[2] = {0};
	assert(os_hid_read(&mock.base, data, sizeof(data), 12) == 2 && data[1] == 0x21);
	data[0] = 0x30;
	assert(os_hid_write(&mock.base, data, sizeof(data)) == 2);
	assert(os_hid_get_feature(&mock.base, 0x40, data, sizeof(data)) == 2 && data[1] == 0x41);
	assert(os_hid_get_feature_timeout(&mock.base, data, sizeof(data), 15) == 2 && data[1] == 0x42);
	data[0] = 0x50;
	assert(os_hid_set_feature(&mock.base, data, sizeof(data)) == 2);
	assert(mock.reads == 1 && mock.writes == 1 && mock.gets == 2 && mock.sets == 1);
	puts("WIN32_HID_CONTRACT_PASS matched_paths=3 rejected_paths=8 mock_reports=5 padded_feature=1 input_only_access=1 physical_opens=0");
	return 0;
}
