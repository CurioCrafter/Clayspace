"""Compile actual native failure-path bodies; no graphics driver or XR runtime."""
from pathlib import Path
import subprocess
import tempfile

from test_d3d_lifetime import body

ROOT = Path(__file__).resolve().parent / "blender-5.1.2"


def main():
    d3d = (ROOT / "intern/ghost/intern/GHOST_ContextD3D.cc").read_text()
    session = (ROOT / "source/blender/windowmanager/xr/intern/wm_xr_session.cc").read_text()
    editor = (ROOT / "source/blender/windowmanager/xr/intern/wm_xr_editor.cc").read_text()
    prepare = body(editor, "void wm_xr_editor_surface_prepare(")
    guard = prepare[prepare.index("  if (!editor_window_exists"):prepare.index("  wmWindow *win")]
    code = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <limits>
using GLint=int; using GLenum=unsigned; using HANDLE=void*;
enum GHOST_TSuccess { GHOST_kFailure, GHOST_kSuccess };
enum { GL_DRAW_FRAMEBUFFER_BINDING, GL_FRAMEBUFFER, GL_DRAW_FRAMEBUFFER,
 GL_READ_FRAMEBUFFER, GL_FRAMEBUFFER_COMPLETE, GL_COLOR_BUFFER_BIT, GL_NEAREST };
static bool unlock_ok=true, lock_ok=true, framebuffer_ok=true;
static int blits=0, locks=0, unlocks=0;
static bool wglDXUnlockObjectsNV(HANDLE,int,HANDLE*) { ++unlocks; return unlock_ok; }
static bool wglDXLockObjectsNV(HANDLE,int,HANDLE*) { ++locks; return lock_ok; }
static unsigned long GetLastError() { return 1; }
static void glGetIntegerv(int,int *v) { *v=42; }
static void glBindFramebuffer(int,int) {}
static GLenum glCheckFramebufferStatus(int) { return framebuffer_ok?GL_FRAMEBUFFER_COMPLETE:999; }
static void glBlitFramebuffer(int sx0,int sy0,int sx1,int sy1,int dx0,int dy0,int dx1,int dy1,int,int) {
 assert(sx0==dx0 && sy0==dy0 && sx1==dx1 && sy1==dy1); // Interop copy performs no second Y flip.
 assert(sx0==0 && sy0==0 && sx1==100 && sy1==50); ++blits;
}
struct Context { void OMSetRenderTargets(int,void**,void*) {} };
struct Resource {
 struct { HANDLE device=nullptr,render_target=nullptr; int fbo=0; } shared_;
 bool is_locked_=false,is_initialized_=false;
 unsigned cur_width_=100,cur_height_=50;
 int initializations=0;
 void *render_target_=(void*)1,*render_target_tex_=(void*)2;
 Context ctx; Context *device_ctx_=&ctx;
 GHOST_TSuccess initialize() { ++initializations; is_initialized_=true; return GHOST_kSuccess; }
'''
    for signature in ("bool ensureUpdated(unsigned int width, unsigned int height)",
                      "bool beginGLOnly()", "bool endGLOnly()",
                      "GHOST_TSuccess blit(unsigned int width, unsigned int height)"):
        code += signature + body(d3d, signature) + "\n"
    code += "};\nstatic bool wm_xr_hand_timestamp_split(int64_t timestamp_ns, int &seconds, int &nanoseconds)"
    code += body(session, "static bool wm_xr_hand_timestamp_split(")
    code += r'''
struct Window { bool alive; };
struct Surface { Window *source_window,*active_window; int captured_button; };
struct Manager { bool cancelled=false; };
static bool editor_window_exists(Manager*,Window *w) { return w && w->alive; }
static void wm_xr_editor_surface_cancel(Manager *wm) { wm->cancelled=true; }
static void check_capture(Manager *wm,Surface &surface) {
'''
    code += guard + "}\n"
    code += r'''
int main() {
 Resource a;
 assert(!a.ensureUpdated(101,50) && !a.ensureUpdated(100,51) && !a.ensureUpdated(0,50));
 assert(a.initializations==0 && a.cur_width_==100 && a.cur_height_==50);
 assert(a.blit(100,50)==GHOST_kSuccess && !a.is_locked_ && blits==1);
 unlock_ok=false;
 assert(a.blit(100,50)==GHOST_kFailure && a.is_locked_ && blits==2);
 const int oldlocks=locks;
 assert(a.blit(100,50)==GHOST_kFailure && locks==oldlocks && a.is_locked_);
 unlock_ok=true; assert(a.endGLOnly() && !a.is_locked_);
 lock_ok=false; assert(a.blit(100,50)==GHOST_kFailure && !a.is_locked_);
 lock_ok=true; framebuffer_ok=false;
 assert(a.blit(100,50)==GHOST_kFailure && !a.is_locked_ && blits==2);
 int sec=1,nano=1;
 assert(!wm_xr_hand_timestamp_split(INT64_MAX,sec,nano) && sec==0 && nano==0);
 assert(!wm_xr_hand_timestamp_split(INT64_MIN,sec,nano) && sec==0 && nano==0);
 assert(wm_xr_hand_timestamp_split(int64_t(INT32_MAX)*1000000000+999999999,sec,nano));
 assert(sec==INT32_MAX && nano==999999999);
 assert(wm_xr_hand_timestamp_split(int64_t(INT32_MIN)*1000000000-999999999,sec,nano));
 assert(sec==INT32_MIN && nano==-999999999);
 assert(wm_xr_hand_timestamp_split(0,sec,nano) && sec==0 && nano==0);
 Window parent{true}, child{false}; Manager wm;
 Surface surface{&parent,&child,1}; check_capture(&wm,surface); assert(wm.cancelled);
 wm.cancelled=false; surface.captured_button=0; check_capture(&wm,surface); assert(!wm.cancelled);
 surface.captured_button=1; child.alive=true; check_capture(&wm,surface); assert(!wm.cancelled);
 parent.alive=false; check_capture(&wm,surface); assert(wm.cancelled);
 puts("XR_NATIVE_FAILURE_PATHS_PASS unlock=1 size=1 timestamp=1 closed_capture_target=1");
}
'''
    binding = (ROOT / "intern/ghost/intern/GHOST_XrGraphicsBindingD3D.cc").read_text()
    assert binding.index("blit_result != GHOST_kSuccess") < binding.index("CopyResource(")
    with tempfile.TemporaryDirectory(prefix="xr-failure-paths-") as directory:
        build = Path(directory)
        test = build / "test.cc"
        test.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(test),
                        "/Fe:" + str(build / "test.exe")], cwd=build, check=True)
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
