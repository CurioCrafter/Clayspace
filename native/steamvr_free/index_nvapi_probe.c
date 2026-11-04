// Read-only NVAPI connector and EDID enumeration; no display acquisition.
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint32_t version; int connector; uint32_t display_id, flags; } DisplayId;
typedef struct { uint32_t version; uint8_t *bytes; uint32_t size, reserved[8]; } EdidData;
typedef void *(__cdecl *QueryInterface)(uint32_t);
typedef int (__cdecl *Simple)(void);
typedef int (__cdecl *EnumGpus)(void **, uint32_t *);
typedef int (__cdecl *ConnectedIds)(void *, DisplayId *, uint32_t *, uint32_t);
typedef int (__cdecl *AllIds)(void *, DisplayId *, uint32_t *);
typedef int (__cdecl *GetEdid)(uint32_t, EdidData *, int *);
typedef int (__cdecl *EnumDisplay)(uint32_t, void **);
typedef int (__cdecl *DisplayName)(void *, char *);

int main(void)
{
    HMODULE dll = LoadLibraryA("nvapi64.dll");
    if (!dll) { fprintf(stderr, "nvapi64.dll unavailable\n"); return 1; }
    QueryInterface query = (QueryInterface)GetProcAddress(dll, "nvapi_QueryInterface");
    if (!query) { fprintf(stderr, "nvapi_QueryInterface unavailable\n"); return 1; }
    Simple init = (Simple)query(0x0150e828), unload = (Simple)query(0xd22bdd7e);
    EnumGpus enum_gpus = (EnumGpus)query(0xe5ac921f);
    ConnectedIds connected = (ConnectedIds)query(0x0078dba2);
    AllIds all_ids = (AllIds)query(0x785210a2);
    GetEdid get_edid = (GetEdid)query(0x436ced76);
    EnumDisplay attached = (EnumDisplay)query(0x9abdd40d);
    EnumDisplay unattached = (EnumDisplay)query(0x20de9260);
    DisplayName attached_name = (DisplayName)query(0x22a78b05);
    DisplayName unattached_name = (DisplayName)query(0x4888d790);
    if (!init || !unload || !enum_gpus || !connected || !get_edid) {
        fprintf(stderr, "required NVAPI entry point unavailable\n"); return 1;
    }
    int status = init();
    if (status) { fprintf(stderr, "NvAPI_Initialize: %d\n", status); return 1; }
    void *gpus[64] = {0};
    uint32_t gpu_count = 0;
    status = enum_gpus(gpus, &gpu_count);
    if (status) { fprintf(stderr, "NvAPI_EnumPhysicalGPUs: %d\n", status); unload(); return 1; }
    printf("NVAPI GPUs=%u\n", gpu_count);
    for (uint32_t gpu = 0; gpu < gpu_count && gpu < 64; ++gpu) {
        uint32_t count = 0;
        status = connected(gpus[gpu], NULL, &count, 0);
        printf("GPU %u connected-display count=%u status=%d\n", gpu, count, status);
        if (status || !count || count > 64) continue;
        DisplayId *list = calloc(count, sizeof(*list));
        if (!list) continue;
        for (uint32_t i = 0; i < count; ++i) list[i].version = sizeof(*list) | (3u << 16);
        status = connected(gpus[gpu], list, &count, 0);
        if (status) { printf("  enumeration status=%d\n", status); free(list); continue; }
        for (uint32_t i = 0; i < count; ++i) {
            printf("  display id=0x%08x connector=%d active=%u OS-visible=%u connected=%u physical=%u\n",
                   list[i].display_id, list[i].connector, (list[i].flags >> 2) & 1,
                   (list[i].flags >> 4) & 1, (list[i].flags >> 6) & 1, (list[i].flags >> 17) & 1);
            EdidData edid = { .version = sizeof(edid) | (2u << 16) };
            int flag = 0;
            status = get_edid(list[i].display_id, &edid, &flag);
            if (status || edid.size < 128 || edid.size > 4096) {
                printf("    EDID size=%u status=%d\n", edid.size, status); continue;
            }
            edid.bytes = calloc(edid.size, 1);
            if (!edid.bytes) continue;
            status = get_edid(list[i].display_id, &edid, &flag);
            if (!status && edid.size >= 128) {
                unsigned a = (edid.bytes[8] << 8) | edid.bytes[9];
                printf("    EDID vendor=%c%c%c product=0x%04x", 'A' + ((a >> 10) & 31) - 1,
                       'A' + ((a >> 5) & 31) - 1, 'A' + (a & 31) - 1,
                       edid.bytes[10] | (edid.bytes[11] << 8));
                for (int pos = 54; pos <= 108; pos += 18) {
                    if (!edid.bytes[pos] && !edid.bytes[pos + 1] && !edid.bytes[pos + 2] && edid.bytes[pos + 3] == 0xfc) {
                        char name[14] = {0}; memcpy(name, edid.bytes + pos + 5, 13);
                        name[strcspn(name, "\r\n")] = 0;
                        printf(" name=%s", name);
                    }
                }
                putchar('\n');
            } else printf("    EDID read status=%d\n", status);
            free(edid.bytes);
        }
        free(list);
        if (!all_ids) continue;
        count = 0;
        status = all_ids(gpus[gpu], NULL, &count);
        printf("GPU %u all-display count=%u status=%d\n", gpu, count, status);
        if (status || !count || count > 64) continue;
        list = calloc(count, sizeof(*list));
        if (!list) continue;
        for (uint32_t i = 0; i < count; ++i) list[i].version = sizeof(*list) | (3u << 16);
        status = all_ids(gpus[gpu], list, &count);
        if (!status) for (uint32_t i = 0; i < count; ++i) {
            printf("  all id=0x%08x connector=%d active=%u OS-visible=%u connected=%u physical=%u\n",
                   list[i].display_id, list[i].connector, (list[i].flags >> 2) & 1,
                   (list[i].flags >> 4) & 1, (list[i].flags >> 6) & 1, (list[i].flags >> 17) & 1);
            for (int requested_flag = 0; requested_flag <= 5; requested_flag += 5) {
                EdidData edid = { .version = sizeof(edid) | (2u << 16) };
                int flag = requested_flag;
                int edid_status = get_edid(list[i].display_id, &edid, &flag);
                if (edid_status || edid.size < 128 || edid.size > 4096) {
                    printf("    %s EDID size=%u status=%d\n", requested_flag ? "hardware" : "default", edid.size, edid_status);
                    continue;
                }
                edid.bytes = calloc(edid.size, 1);
                if (!edid.bytes) continue;
                flag = requested_flag;
                edid_status = get_edid(list[i].display_id, &edid, &flag);
                if (!edid_status && edid.size >= 128) {
                    unsigned a = (edid.bytes[8] << 8) | edid.bytes[9];
                    int is_index = 0;
                    printf("    %s EDID vendor=%c%c%c product=0x%04x", requested_flag ? "hardware" : "default",
                           'A' + ((a >> 10) & 31) - 1, 'A' + ((a >> 5) & 31) - 1,
                           'A' + (a & 31) - 1, edid.bytes[10] | (edid.bytes[11] << 8));
                    for (int pos = 54; pos <= 108; pos += 18) {
                        if (!edid.bytes[pos] && !edid.bytes[pos + 1] && !edid.bytes[pos + 2] && edid.bytes[pos + 3] == 0xfc) {
                            char name[14] = {0}; memcpy(name, edid.bytes + pos + 5, 13);
                            name[strcspn(name, "\r\n")] = 0;
                            printf(" name=%s", name);
                            if (!strcmp(name, "Index HMD")) is_index = 1;
                        }
                    }
                    putchar('\n');
                    if (is_index && requested_flag == 5) {
                        unsigned declared = edid.bytes[126], available = edid.size / 128 - 1;
                        unsigned cta = 0, ms_hmd = 0, ms_other = 0;
                        for (unsigned ext = 0; ext < declared && ext < available; ++ext) {
                            const uint8_t *block = edid.bytes + (ext + 1) * 128;
                            if (block[0] != 0x02) continue;
                            ++cta;
                            unsigned end = block[2];
                            if (end < 4 || end > 127) continue;
                            for (unsigned pos = 4; pos < end;) {
                                unsigned tag = block[pos] >> 5, length = block[pos] & 31;
                                if (pos + 1 + length > end) break;
                                if (tag == 3 && length >= 4 && block[pos + 1] == 0x5c &&
                                    block[pos + 2] == 0x12 && block[pos + 3] == 0xca) {
                                    if (block[pos + 4] == 1 || block[pos + 4] == 2) ++ms_hmd;
                                    else ++ms_other;
                                }
                                pos += 1 + length;
                            }
                        }
                        printf("    Index hardware EDID bytes=%u extensions=%u available=%u first-extension-tag=0x%02x revision=0x%02x CTA=%u Microsoft-HMD-VSDB=%u Microsoft-other-VSDB=%u\n",
                               edid.size, declared, available, available ? edid.bytes[128] : 0,
                               available ? edid.bytes[129] : 0, cta, ms_hmd, ms_other);
                    }
                } else printf("    %s EDID read status=%d\n", requested_flag ? "hardware" : "default", edid_status);
                free(edid.bytes);
            }
        }
        else printf("  all-display enumeration status=%d\n", status);
        free(list);
    }
    for (int kind = 0; kind < 2; ++kind) {
        EnumDisplay enumerate = kind ? unattached : attached;
        DisplayName name_for = kind ? unattached_name : attached_name;
        if (!enumerate || !name_for) continue;
        unsigned count = 0;
        for (; count < 64; ++count) {
            void *handle = NULL;
            if (enumerate(count, &handle)) break;
            char name[64] = {0};
            status = name_for(handle, name);
            printf("%s display %u: %s (name status=%d)\n", kind ? "unattached" : "attached",
                   count, status ? "(unavailable)" : name, status);
        }
        printf("%s display handles=%u\n", kind ? "unattached" : "attached", count);
    }
    unload();
    FreeLibrary(dll);
    return 0;
}
