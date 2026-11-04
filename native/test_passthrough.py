"""Compile and exercise the actual native passthrough methods against a fake OpenXR runtime.

Run from a Visual Studio developer shell. No runtime or headset is started.
"""
import argparse
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--openxr-include", type=pathlib.Path, required=True)
    args = parser.parse_args()
    source = (ROOT / "blender-5.1.2/intern/ghost/intern/GHOST_XrSession.cc").read_text()
    methods = source[source.index("void GHOST_XrSession::enablePassthrough()") :]
    methods = methods[:methods.index("/** \\} */")]
    preamble = r'''
#include <openxr/openxr.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>
#define CHECK_XR_ASSERT(call) assert((call) == XR_SUCCESS)
struct Data {
  XrSession session = (XrSession)1;
  bool passthrough_supported = true;
  XrPassthroughFB passthrough = XR_NULL_HANDLE;
  XrCompositionLayerPassthroughFB passthrough_layer = {};
  PFN_xrCreatePassthroughFB create_passthrough = nullptr;
  PFN_xrDestroyPassthroughFB destroy_passthrough = nullptr;
  PFN_xrCreatePassthroughLayerFB create_passthrough_layer = nullptr;
  PFN_xrDestroyPassthroughLayerFB destroy_passthrough_layer = nullptr;
};
struct Context { XrInstance getInstance() { return (XrInstance)1; } };
struct GHOST_XrSession {
  std::unique_ptr<Data> oxr_ = std::make_unique<Data>();
  Context context;
  Context *context_ = &context;
  void enablePassthrough();
  void destroyPassthrough();
};
static int fail_at = 0;
static std::vector<int> calls;
static XrResult XRAPI_CALL create_parent(XrSession, const XrPassthroughCreateInfoFB *info,
                                       XrPassthroughFB *handle) {
  calls.push_back(1);
  assert(info->flags == XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB);
  if (fail_at == 1) return XR_ERROR_RUNTIME_FAILURE;
  *handle = (XrPassthroughFB)2;
  return XR_SUCCESS;
}
static XrResult XRAPI_CALL create_layer(XrSession, const XrPassthroughLayerCreateInfoFB *info,
                                      XrPassthroughLayerFB *handle) {
  calls.push_back(2);
  assert(info->passthrough == (XrPassthroughFB)2);
  assert(info->flags == XR_PASSTHROUGH_IS_RUNNING_AT_CREATION_BIT_FB);
  if (fail_at == 2) return XR_ERROR_RUNTIME_FAILURE;
  *handle = (XrPassthroughLayerFB)3;
  return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroy_layer(XrPassthroughLayerFB handle) {
  assert(handle == (XrPassthroughLayerFB)3); calls.push_back(3); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroy_parent(XrPassthroughFB handle) {
  assert(handle == (XrPassthroughFB)2); calls.push_back(4); return XR_SUCCESS;
}
extern "C" XRAPI_ATTR XrResult XRAPI_CALL xrGetInstanceProcAddr(
    XrInstance, const char *name, PFN_xrVoidFunction *function) {
  if (fail_at == 3) return XR_ERROR_FUNCTION_UNSUPPORTED;
  if (!strcmp(name, "xrCreatePassthroughFB")) *function = (PFN_xrVoidFunction)create_parent;
  else if (!strcmp(name, "xrDestroyPassthroughFB")) *function = (PFN_xrVoidFunction)destroy_parent;
  else if (!strcmp(name, "xrCreatePassthroughLayerFB")) *function = (PFN_xrVoidFunction)create_layer;
  else if (!strcmp(name, "xrDestroyPassthroughLayerFB")) *function = (PFN_xrVoidFunction)destroy_layer;
  else return XR_ERROR_FUNCTION_UNSUPPORTED;
  return XR_SUCCESS;
}
'''
    checks = r'''
int main() {
  for (int failure : {0, 1, 2, 3}) {
    fail_at = failure; calls.clear(); GHOST_XrSession session;
    session.enablePassthrough();
    if (failure == 0) {
      assert(session.oxr_->passthrough_layer.layerHandle == (XrPassthroughLayerFB)3);
      session.enablePassthrough(); // Repeated frames must reuse the existing layer.
      assert((calls == std::vector<int>{1, 2}));
    } else {
      assert(session.oxr_->passthrough == XR_NULL_HANDLE);
      assert(session.oxr_->passthrough_layer.layerHandle == XR_NULL_HANDLE);
    }
    session.destroyPassthrough(); session.destroyPassthrough();
    if (failure == 0) assert((calls == std::vector<int>{1, 2, 3, 4}));
    if (failure == 1) assert((calls == std::vector<int>{1}));
    if (failure == 2) assert((calls == std::vector<int>{1, 2, 4}));
    if (failure == 3) assert(calls.empty());
  }
  calls.clear(); GHOST_XrSession unsupported;
  unsupported.oxr_->passthrough_supported = false;
  unsupported.enablePassthrough(); unsupported.destroyPassthrough();
  assert(calls.empty());
  puts("XR_WORKBENCH_PASSTHROUGH_LIFETIME_PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-passthrough-") as directory:
        build = pathlib.Path(directory)
        test = build / "test.cc"
        test.write_text(preamble + methods + checks)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4",
                        "/I" + str(args.openxr_include), str(test),
                        "/Fe:" + str(build / "test.exe")], cwd=build, check=True)
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
