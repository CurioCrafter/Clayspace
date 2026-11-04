// Copyright 2026, Andrew Rainsberger
// SPDX-License-Identifier: BSL-1.0
#include "main/comp_window_mswin_index.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>

int
main(void)
{
	assert(comp_mswin_is_index_id(L"MONITOR\\VLV91A8\\instance"));
	assert(comp_mswin_is_index_id(L"\\\\?\\DISPLAY#VLV91A8#instance"));
	assert(!comp_mswin_is_index_id(L"MONITOR\\VLV91A80\\instance"));
	assert(!comp_mswin_is_index_id(L"MONITOR\\GSM5B8E\\instance"));
	assert(!comp_mswin_is_index_id(L"other VLV91A8"));
	assert(!comp_mswin_is_index_id(L"MON"));
	assert(comp_mswin_index_is_exclusive(1, 1));
	assert(!comp_mswin_index_is_exclusive(2, 1)); // clone
	assert(!comp_mswin_index_is_exclusive(2, 2)); // two Index children
	assert(comp_mswin_index_is_unique(1, 1));
	assert(!comp_mswin_index_is_unique(2, 1)); // duplicate identity
	assert(!comp_mswin_index_is_unique(1, 0)); // absent
	assert(!comp_mswin_index_is_unique(2, 2)); // ambiguous
	assert(comp_mswin_index_is_extended(2, false));
	assert(!comp_mswin_index_is_extended(1, false)); // not a second display
	assert(!comp_mswin_index_is_extended(2, true)); // Index is primary
	assert(_putenv_s("XRT_COMPOSITOR_INDEX_EXTENDED", "0") == 0);
	assert(!comp_mswin_index_extended_requested());
	assert(_putenv_s("XRT_COMPOSITOR_INDEX_EXTENDED", "1") == 0);
	assert(comp_mswin_index_extended_requested());
	assert(_putenv_s("XRT_COMPOSITOR_INDEX_EXTENDED", "") == 0);
	return 0;
}
