"""Compile the actual interop destructor against checked GL/COM lifetimes.

Run in a Visual Studio developer shell. No graphics driver or headset is used.
"""

from pathlib import Path
import subprocess
import tempfile


def body(source, signature):
    start = source.index("{", source.index(signature))
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    root = Path(__file__).resolve().parent / "blender-5.1.2/intern/ghost/intern"
    source = (root / "GHOST_ContextD3D.cc").read_text()
    session = body(
        (root / "GHOST_XrSession.cc").read_text(), "GHOST_XrSession::~GHOST_XrSession()"
    )
    assert session.index("gpu_ctx_->activateDrawingContext()") < session.index(
        "gpu_binding_.reset()"
    )
    assert session.index("gpu_binding_.reset()") < session.index(
        "unbindGraphicsContext()"
    )
    fixture = r"""
#include <cassert>
#include <cstdio>
#include <vector>
using HANDLE = void*;
using GLuint = unsigned int;
static std::vector<int> calls;
static bool com_alive=true, registered=true, device_open=true;
static bool fail_unregister=false, fail_close=false;
struct Com {
  int id;
  void Release() { assert(!device_open || fail_close); calls.push_back(id); com_alive=false; }
};
static void glFinish() { assert(com_alive); calls.push_back(1); }
static bool wglDXUnregisterObjectNV(HANDLE, HANDLE) {
  assert(com_alive && device_open); calls.push_back(3); registered=false;
  return !fail_unregister;
}
static bool wglDXCloseDeviceNV(HANDLE) {
  assert(com_alive && !registered); calls.push_back(4); device_open=false;
  return !fail_close;
}
static unsigned long GetLastError() { return 123; }
static void glDeleteFramebuffers(int, GLuint*) { assert(com_alive); calls.push_back(5); }
static void glDeleteTextures(int, GLuint*) { assert(com_alive); calls.push_back(6); }
static void glDeleteRenderbuffers(int, GLuint*) { assert(com_alive); calls.push_back(7); }
struct GHOST_SharedOpenGLResource {
  struct { HANDLE device=nullptr; GLuint fbo=0; HANDLE render_target=nullptr; } shared_;
  bool is_locked_=false, use_gl_texture2d_=false;
  GLuint gl_render_target_=0;
  Com *render_target_tex_=nullptr, *render_target_=nullptr;
  void endGLOnly() { calls.push_back(2); is_locked_=false; }
  ~GHOST_SharedOpenGLResource()
"""
    checks = r"""
};
int main() {
  for (bool texture : {false, true}) {
    for (bool locked : {false, true}) {
      calls.clear(); com_alive=registered=device_open=true;
      Com tex{8}, view{9};
      {
        GHOST_SharedOpenGLResource resource;
        resource.shared_={(HANDLE)1, 2, (HANDLE)3}; resource.gl_render_target_=4;
        resource.is_locked_=locked; resource.use_gl_texture2d_=texture;
        resource.render_target_tex_=&tex; resource.render_target_=&view;
      }
      std::vector<int> expected{1};
      if (locked) expected.push_back(2);
      for (int call : {3,4,5,texture?6:7,8,9}) expected.push_back(call);
      assert(calls==expected);
    }
  }
  calls.clear(); { GHOST_SharedOpenGLResource empty; } assert(calls.empty());
  calls.clear(); com_alive=device_open=true; registered=false;
  { GHOST_SharedOpenGLResource partial; partial.shared_.device=(HANDLE)1; }
  assert((calls==std::vector<int>{1,4}));
  calls.clear(); com_alive=device_open=registered=true;
  fail_unregister=fail_close=true;
  { GHOST_SharedOpenGLResource failure; failure.shared_={(HANDLE)1,2,(HANDLE)3}; }
  assert((calls==std::vector<int>{1,3,4,5}));
  puts("XR_D3D_LIFETIME_PASS actual_destructor=1 gl_before_com=1 partial_init=1 session_order=1");
}
"""
    code = fixture + body(source, "~GHOST_SharedOpenGLResource()") + checks
    with tempfile.TemporaryDirectory(prefix="xr-d3d-lifetime-") as directory:
        build = Path(directory)
        test = build / "test.cc"
        test.write_text(code)
        subprocess.run(
            [
                "cl",
                "/nologo",
                "/std:c++17",
                "/EHsc",
                "/W4",
                str(test),
                "/Fe:" + str(build / "test.exe"),
            ],
            cwd=build,
            check=True,
        )
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
