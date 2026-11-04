"""Compile the actual native input-validation function against a small RNA/event fixture.

No OpenXR runtime, camera, OS input, or Blender process is started. This proves
validation/capture/ray-plane control flow, not Blender UI dispatch or GPU drawing.
Run in a Visual Studio developer shell: python native/test_editor_input.py
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "blender-5.1.2/source/blender/windowmanager/xr/intern/wm_xr_editor.cc"

PREAMBLE = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <vector>
using wmOperatorStatus = int;
using wmEventType = int;
using eWM_EventFlag = int;
using wmEventModifierFlag = int;
enum { OPERATOR_CANCELLED=0, OPERATOR_FINISHED=1, MOUSEMOVE=2, WHEELUPMOUSE=3,
       WHEELDOWNMOUSE=4, LEFTMOUSE=10, RIGHTMOUSE=11, EVT_AKEY=100, EVT_ESCKEY=101,
       KM_NOTHING=0, KM_PRESS=1, KM_RELEASE=2, KM_SHIFT=1, KM_CTRL=2, KM_ALT=4,
       NC_WINDOW=0 };
template<typename T, typename... V> bool elem(T t, V... v) { return ((t==v) || ...); }
#define ELEM(t,...) elem(t,__VA_ARGS__)
#define ISKEYBOARD(t) ((t)>=100 && (t)<200)
#define ISMOUSE_BUTTON(t) ((t)==LEFTMOUSE || (t)==RIGHTMOUSE)
#define STRNCPY(dst,src) std::snprintf(dst,sizeof(dst),"%s",src)
struct wmEvent { int type=0,val=0,flag=0,xy[2]={0,0},modifier=0; char utf8_buf[6]={}; };
struct wmWindow {};
struct Pose {
  bool yaw_90=false;
  float position[3]={0,0,0}, orientation_quat[4]={1,0,0,0};
};
using GHOST_XrPose = Pose;
// SPACE_ENUM_FROM_BLENDER
struct ScrArea { struct { int xmin,ymin,xmax,ymax; } totrct; int spacetype; };
struct ARegion { bool owned=false; };
struct bScreen { std::vector<ScrArea> areabase; std::vector<ARegion> regionbase; };
template<typename T> bool BLI_listbase_is_empty(const std::vector<T> *v) { return v->empty(); }
template<typename T> int BLI_findindex(const std::vector<T> *v, const T *item) {
  for (int i=0;i<int(v->size());i++) if (&(*v)[i]==item) return i;
  return -1;
}
struct wmXrEditorPanel {
  int xmin=0,ymin=0,xmax=0,ymax=0;
  float x=0,y=0,width=0,height=0;
  const ScrArea *area=nullptr;
};
struct wmXrEditorSurface {
  bool visible=true,pointer_visible=false,separated=false,hand_menu=false;
  wmWindow *source_window=nullptr;
  wmWindow *active_window=nullptr;
  int pixel_width=1000,pixel_height=500,pointer_id=0,captured_button=0;
  float width=2,height=1;
  Pose pose;
  wmEvent event_state;
  int panel_count=0,pointer_panel=0,drawn_frames=0;
  wmXrEditorPanel panels[3]{};
};
struct wmXrHandMenu {
  wmXrEditorSurface surface;
  bool closing=false,simulated=false,anchor_ready=false,anchor_valid=true;
  wmXrHandMenu() { surface.visible=false; surface.hand_menu=true; }
};
struct Runtime {
  wmXrEditorSurface editor_surface;
  wmXrHandMenu hand_menu;
  struct { int captured_action=0; } task_card;
  bool ready=true;
};
using wmXrRuntimeData = Runtime;
struct wmXrData { Runtime *runtime; };
struct wmWindowManager { wmXrData xr; };
struct bContext { wmWindowManager *wm; };
struct Props {
  int type=MOUSEMOVE,value=KM_NOTHING,pointer=0,target=0;
  bool use_ray=false,shift=false,ctrl=false,alt=false;
  float u=.5f,v=.5f,origin[3]={0,0,1},direction[3]={0,0,-1};
  float position[3]={0,0,0},rotation[4]={1,0,0,0},width=0;
  std::string text;
};
struct wmOperator { Props *ptr; };
static std::vector<wmEvent> events;
wmWindowManager *CTX_wm_manager(bContext *c) { return c->wm; }
bool editor_window_exists(wmWindowManager *, wmWindow *w) { return w!=nullptr; }
bool wm_xr_ui_input_ready(Runtime *runtime) { return runtime->ready; }
bool WM_xr_hand_menu_region_owned(const wmWindowManager*,const ARegion*r) { return r && r->owned; }
bool hand_menu_anchor(Runtime *runtime) { return runtime->hand_menu.anchor_valid; }
void wm_xr_hand_menu_cancel(wmWindowManager *wm) {
  wm->xr.runtime->hand_menu.surface.captured_button=0;
  wm->xr.runtime->hand_menu.surface.visible=false;
}
static bool is_main_thread=true;
bool BLI_thread_is_main() { return is_main_thread; }
void copy_v3_v3(float *to,const float *from) { std::copy_n(from,3,to); }
void copy_qt_qt(float *to,const float *from) { std::copy_n(from,4,to); }
void wm_xr_editor_surface_cancel(wmWindowManager *wm) {
  wm->xr.runtime->editor_surface.captured_button=0;
  wm->xr.runtime->editor_surface.visible=false;
}
int RNA_int_get(Props*p,const char*) { return p->pointer; }
int RNA_enum_get(Props*p,const char*n) {
  if (!strcmp(n,"target")) return p->target;
  return !strcmp(n,"type")?p->type:p->value;
}
bool RNA_boolean_get(Props*p,const char*n) {
  if (!strcmp(n,"use_ray")) return p->use_ray;
  if (!strcmp(n,"shift")) return p->shift;
  if (!strcmp(n,"ctrl")) return p->ctrl;
  return p->alt;
}
float RNA_float_get(Props*p,const char*n) {
  if (!strcmp(n,"width")) return p->width;
  return !strcmp(n,"u")?p->u:p->v;
}
void RNA_float_get_array(Props*p,const char*n,float*v) {
  if (!strcmp(n,"position")) { std::copy_n(p->position,3,v); return; }
  if (!strcmp(n,"rotation")) { std::copy_n(p->rotation,4,v); return; }
  std::copy_n(!strcmp(n,"ray_origin")?p->origin:p->direction,3,v);
}
void RNA_string_get(Props*p,const char*,char*v) { std::strcpy(v,p->text.c_str()); }
void wm_xr_pose_to_imat(const Pose*p,float m[4][4]) {
  for(int i=0;i<4;i++) for(int j=0;j<4;j++) m[i][j]=i==j;
  if(p->yaw_90) { // Inverse of a +90 degree world Y rotation.
    m[0][0]=0; m[0][2]=1; m[2][0]=-1; m[2][2]=0;
  }
  for (int row=0;row<3;row++) {
    m[3][row]=-(m[0][row]*p->position[0]+m[1][row]*p->position[1]+m[2][row]*p->position[2]);
  }
}
void mul_mat3_m4_v3(float m[4][4],float v[3]) {
  float r[3];
  for(int row=0;row<3;row++) r[row]=m[0][row]*v[0]+m[1][row]*v[1]+m[2][row]*v[2];
  std::copy_n(r,3,v);
}
void mul_m4_v3(float m[4][4],float v[3]) {
  mul_mat3_m4_v3(m,v);
  for (int i=0;i<3;i++) v[i]+=m[3][i];
}
int BLI_str_utf8_size_or_error(const char*t) { return (unsigned char)t[0]<128?1:-1; }
void editor_queue(wmWindow*,wmXrEditorSurface&s,wmEvent&e) { events.push_back(e); s.event_state=e; }
void WM_event_add_notifier(bContext*,int,void*) {}
'''

TESTS = r'''
int main() {
  wmWindow win; Runtime runtime; runtime.editor_surface.active_window=&win;
  wmWindowManager wm{{&runtime}}; bContext ctx{&wm}; Props props; wmOperator op{&props};
  auto &s=runtime.editor_surface;
  auto run=[&]() { return editor_event_exec(&ctx,&op); };
  assert(run()==OPERATOR_FINISHED && events.back().xy[0]==499);
  s.visible=false; assert(run()==OPERATOR_CANCELLED); s.visible=true;
  props.u=std::numeric_limits<float>::quiet_NaN(); assert(run()==OPERATOR_CANCELLED);
  props.u=2; assert(run()==OPERATOR_CANCELLED);
  props.u=.5f; props.type=LEFTMOUSE; props.value=KM_PRESS; props.text="ab";
  assert(run()==OPERATOR_CANCELLED && s.captured_button==0);
  props.text=""; assert(run()==OPERATOR_FINISHED && s.captured_button==LEFTMOUSE);
  props.pointer=1; assert(run()==OPERATOR_CANCELLED);
  props.pointer=0; assert(run()==OPERATOR_CANCELLED); // Repeated press cannot steal capture.
  props.type=RIGHTMOUSE; props.value=KM_RELEASE; assert(run()==OPERATOR_CANCELLED);
  props.type=MOUSEMOVE; props.value=KM_NOTHING; props.u=2; props.v=-1;
  assert(run()==OPERATOR_FINISHED && events.back().xy[0]==999 && events.back().xy[1]==0);
  props.type=LEFTMOUSE; props.value=KM_RELEASE;
  assert(run()==OPERATOR_FINISHED && s.captured_button==0);
  props=Props{}; props.use_ray=true;
  assert(run()==OPERATOR_FINISHED && events.back().xy[0]==499);
  props.direction[2]=1; assert(run()==OPERATOR_CANCELLED); // Ray points away.
  props.direction[2]=0; assert(run()==OPERATOR_CANCELLED); // Parallel.
  props.origin[2]=-1; props.direction[2]=1;
  assert(run()==OPERATOR_CANCELLED); // Back face cannot start input.
  props.origin[2]=1;
  props.direction[2]=std::numeric_limits<float>::infinity(); assert(run()==OPERATOR_CANCELLED);
  s.pose.yaw_90=true; props=Props{}; props.use_ray=true;
  props.origin[0]=1; props.origin[2]=0; props.direction[0]=-1; props.direction[2]=0;
  assert(run()==OPERATOR_FINISHED && events.back().xy[0]==499); // World -X faces the rotated card.
  props.origin[0]=-1; props.direction[0]=1;
  assert(run()==OPERATOR_CANCELLED); // Reverse ray hits its back face.
  s.pose.yaw_90=false;
  props=Props{}; props.type=LEFTMOUSE; props.value=KM_PRESS; assert(run()==OPERATOR_FINISHED);
  props.use_ray=true; props.origin[2]=-1; props.direction[2]=1; props.value=KM_RELEASE;
  assert(run()==OPERATOR_FINISHED && s.captured_button==0); // Lost ray still releases.
  props=Props{}; props.u=.8f; props.v=.2f; assert(run()==OPERATOR_FINISHED);
  const int focus_x=s.event_state.xy[0], focus_y=s.event_state.xy[1];
  props=Props{}; props.type=EVT_AKEY; props.value=KM_PRESS; props.text="x"; props.ctrl=true;
  props.use_ray=true; props.direction[2]=0; props.u=std::numeric_limits<float>::quiet_NaN();
  assert(run()==OPERATOR_FINISHED && events.back().utf8_buf[0]=='x' && events.back().modifier==KM_CTRL);
  assert(events.back().xy[0]==focus_x && events.back().xy[1]==focus_y);
  props.value=KM_RELEASE; assert(run()==OPERATOR_CANCELLED); // Text only on key press.
  props=Props{}; props.type=777; assert(run()==OPERATOR_CANCELLED);
  props.type=MOUSEMOVE; props.value=KM_PRESS; assert(run()==OPERATOR_CANCELLED);

  // View3D first, Properties right, Outliner left even when Timeline is larger.
  s.source_window=&win; s.separated=true;
  bScreen screen{{{{0,0,699,499},SPACE_VIEW3D}, {{700,0,999,299},SPACE_PROPERTIES},
                   {{700,300,999,499},SPACE_OUTLINER}, {{0,0,999,80},SPACE_ACTION}}, {}};
  editor_panels_prepare(s,&screen,&wm);
  assert(s.panel_count==3 && s.panels[0].area==&screen.areabase[0]);
  assert(s.panels[1].area==&screen.areabase[1] && s.panels[1].x>0);
  assert(s.panels[2].area==&screen.areabase[2] && s.panels[2].x<0);
  assert(s.panels[1].x-s.panels[1].width/2>s.panels[0].width/2);
  assert(editor_capture_layout_valid(s,&screen,1000,500));
  assert(!editor_capture_layout_valid(s,&screen,999,500));
  assert(!editor_capture_layout_valid(s,nullptr,1000,500));
  screen.areabase[1].totrct.xmin++;
  assert(!editor_capture_layout_valid(s,&screen,1000,500));
  screen.areabase[1].totrct.xmin--;
  bScreen other_screen=screen;
  assert(!editor_capture_layout_valid(s,&other_screen,1000,500));
  s.pointer_visible=true; editor_panels_prepare(s,&screen,&wm);
  assert(s.pointer_visible); // Stable layout does not flicker the pointer.
  screen.regionbase.push_back({false}); editor_panels_prepare(s,&screen,&wm);
  assert(s.panel_count==1 && !s.panels[0].area && !s.pointer_visible); // Popup fallback.
  screen.regionbase[0].owned=true; editor_panels_prepare(s,&screen,&wm);
  assert(s.panel_count==3); // Hand-only popup does not collapse separated desktop panels.
  screen.regionbase.clear(); wmWindow child; s.active_window=&child;
  editor_panels_prepare(s,&screen,&wm); assert(s.panel_count==1 && !s.panels[0].area);
  s.active_window=&win; editor_panels_prepare(s,&screen,&wm);
  props=Props{}; props.use_ray=true; props.origin[0]=s.panels[1].x;
  assert(run()==OPERATOR_FINISHED && std::abs(events.back().xy[0]-849)<=1);
  assert(s.pointer_panel==1 && std::abs(events.back().xy[1]-149)<=1);
  props.origin[0]=s.panels[0].width/2+.01f;
  assert(run()==OPERATOR_CANCELLED && !s.pointer_visible); // Gap is not a button.
  props.origin[0]=s.panels[1].x; props.type=LEFTMOUSE; props.value=KM_PRESS;
  assert(run()==OPERATOR_FINISHED && s.pointer_panel==1);
  props.origin[0]=s.panels[2].x; props.type=MOUSEMOVE; props.value=KM_NOTHING;
  assert(run()==OPERATOR_FINISHED && s.pointer_panel==1 && !s.pointer_visible);
  assert(std::abs(events.back().xy[0]-700)<=1); // Drag cannot jump to another editor.
  props.type=LEFTMOUSE; props.value=KM_RELEASE;
  assert(run()==OPERATOR_FINISHED && !s.captured_button);
  float hit[5], pose_values[9];
  props=Props{}; props.origin[0]=s.panels[1].x;
  props.direction[2]=-3; // Query reports true distance, not a direction-dependent parameter.
  auto hit_query=[&]() { WM_xr_editor_surface_hit_get(&wm.xr,props.origin,props.direction,hit); };
  hit_query(); assert(hit[0]==1 && hit[1]==1 && std::abs(hit[4]-1)<1e-5);
  props.origin[0]=s.panels[0].width/2+.01f; hit_query();
  assert(hit[0]==0 && hit[1]==-1); // Same gap as native event input.
  props.origin[0]=0; props.origin[2]=-1; props.direction[2]=1;
  hit_query(); assert(hit[0]==0); // Back face.
  props=Props{}; props.direction[2]=0; hit_query(); assert(hit[0]==0);
  props.direction[2]=std::numeric_limits<float>::infinity(); hit_query(); assert(hit[0]==0);
  props=Props{}; s.captured_button=LEFTMOUSE; hit_query(); assert(hit[0]==0);
  auto move=[&]() { return editor_transform_exec(&ctx,&op); };
  props.position[0]=3; assert(move()==OPERATOR_CANCELLED && s.pose.position[0]==0);
  s.captured_button=0; runtime.task_card.captured_action=1;
  assert(move()==OPERATOR_CANCELLED); runtime.task_card.captured_action=0;
  props.rotation[0]=0; assert(move()==OPERATOR_CANCELLED && s.pose.position[0]==0);
  props.rotation[0]=std::numeric_limits<float>::quiet_NaN(); assert(move()==OPERATOR_CANCELLED);
  props.rotation[0]=2; props.width=-1; assert(move()==OPERATOR_CANCELLED);
  props.width=std::numeric_limits<float>::infinity(); assert(move()==OPERATOR_CANCELLED);
  props.width=std::numeric_limits<float>::denorm_min(); assert(move()==OPERATOR_CANCELLED);
  props.width=0; props.position[1]=std::numeric_limits<float>::infinity();
  assert(move()==OPERATOR_CANCELLED); props.position[1]=0;
  is_main_thread=false; assert(move()==OPERATOR_CANCELLED); hit_query(); assert(hit[0]==0);
  is_main_thread=true; runtime.ready=false; assert(move()==OPERATOR_CANCELLED);
  runtime.ready=true; s.source_window=nullptr; assert(move()==OPERATOR_CANCELLED); s.source_window=&win;
  const float old_width=s.width, old_right_x=s.panels[1].x;
  props.rotation[0]=std::numeric_limits<float>::max();
  assert(move()==OPERATOR_FINISHED && s.width==old_width && s.separated && s.panel_count==3);
  assert(s.pose.position[0]==3 && s.pose.orientation_quat[0]==1 && !s.pointer_visible);
  WM_xr_editor_surface_pose_get(&wm.xr,pose_values);
  assert(pose_values[0]==3 && pose_values[3]==1 && pose_values[7]==old_width);
  props.origin[0]=3; hit_query(); assert(hit[0]==1 && hit[1]==0 && hit[4]==1);
  props.width=old_width*2; assert(move()==OPERATOR_FINISHED);
  assert(s.width==old_width*2 && s.panels[1].x==old_right_x*2 && s.panel_count==3);
  s.visible=false; assert(move()==OPERATOR_CANCELLED); hit_query(); assert(hit[0]==0);
  WM_xr_editor_surface_pose_get(&wm.xr,pose_values); assert(pose_values[7]==0);
  s.visible=true;
  s.separated=false; editor_panels_prepare(s,&screen,&wm);
  assert(s.panel_count==1 && !s.panels[0].area);
  auto &menu=runtime.hand_menu;
  menu.surface.active_window=&win; menu.surface.visible=true;
  props=Props{};
  const auto before_menu=events.size();
  assert(run()==OPERATOR_CANCELLED && events.size()==before_menu); // No stale geometry on reopen.
  menu.anchor_ready=true; assert(run()==OPERATOR_CANCELLED); // Wait for first composed frame.
  menu.surface.drawn_frames=1;
  assert(run()==OPERATOR_FINISHED && menu.surface.event_state.xy[0]==499); // AUTO picks hand.
  props.target=1; assert(run()==OPERATOR_CANCELLED); // Explicit desktop cannot bypass open menu.
  props.target=2; props.type=LEFTMOUSE; props.value=KM_PRESS;
  assert(run()==OPERATOR_FINISHED && menu.surface.captured_button==LEFTMOUSE && !s.captured_button);
  props.value=KM_RELEASE; assert(run()==OPERATOR_FINISHED && !menu.surface.captured_button);
  // Rays target rendered pixel centers, not UV test injection. Cover every
  // menu quadrant and corners before and after a cascading submenu grows its
  // crop around the unchanged root anchor, including a rotated world pose.
  auto &hand=menu.surface;
  const float meters_per_pixel=.002f,root_x=550,root_y=280;
  for(bool rotated:{false,true}) {
    hand.pose.yaw_90=rotated;
    hand.pose.position[0]=2; hand.pose.position[1]=3; hand.pose.position[2]=4;
    for(bool nested:{false,true}) {
      hand.panel_count=1;
      auto &crop=hand.panels[0];
      crop.xmin=nested?100:300; crop.ymin=nested?5:100;
      crop.xmax=nested?990:800; crop.ymax=nested?495:460;
      crop.width=(crop.xmax-crop.xmin)*meters_per_pixel;
      crop.height=(crop.ymax-crop.ymin)*meters_per_pixel;
      crop.x=((crop.xmin+crop.xmax)*.5f-root_x)*meters_per_pixel;
      crop.y=((crop.ymin+crop.ymax)*.5f-root_y)*meters_per_pixel;
      for(float u:{.01f,.25f,.5f,.75f,.99f}) for(float v:{.01f,.25f,.5f,.75f,.99f}) {
        const float x=crop.x+(u-.5f)*crop.width,y=crop.y+(v-.5f)*crop.height;
        props=Props{}; props.target=2; props.use_ray=true;
        props.origin[0]=2+(rotated?.3f:x); props.origin[1]=3+y;
        props.origin[2]=4+(rotated?-x:.3f);
        props.direction[0]=rotated?-1:0; props.direction[2]=rotated?0:-1;
        assert(run()==OPERATOR_FINISHED && hand.pointer_visible);
        const int px=int(crop.xmin+u*(crop.xmax-crop.xmin-1));
        const int py=int(crop.ymin+v*(crop.ymax-crop.ymin-1));
        assert(std::abs(events.back().xy[0]-px)<=1 && std::abs(events.back().xy[1]-py)<=1);
        props.type=LEFTMOUSE; props.value=KM_PRESS;
        assert(run()==OPERATOR_FINISHED && hand.captured_button==LEFTMOUSE);
        props.value=KM_RELEASE;
        assert(run()==OPERATOR_FINISHED && !hand.captured_button);
      }
    }
  }
  hand.pose=Pose{}; hand.panel_count=0;
  puts("XR_HAND_MENU_RAY_COVERAGE_PASS actual_native_input=1 root_and_submenu=1 rotated_and_translated=1 points=100 press_release=100 hardware=0");
  menu.anchor_valid=false; props=Props{}; props.target=2;
  assert(run()==OPERATOR_CANCELLED && !menu.surface.visible); // Tracking loss cancels host.
  menu.surface.visible=true; menu.simulated=true;
  assert(run()==OPERATOR_FINISHED); // Explicitly authorized fixture does not need physical grip.
  runtime.ready=false; assert(run()==OPERATOR_CANCELLED && !menu.surface.visible && !s.visible);
  puts("XR_EDITOR_INPUT_PASS validation=1 capture=1 off_card_release=1 rotated_ray=1 typed_input=1 separated_crops=1 popup_fallback=1 panel_capture=1 layout_invalidation=1 rig_transform=1 hit_query=1 hand_target=1 first_menu_frame_guard=1");
}
'''


def main():
    source = SOURCE.read_text()
    begin = source.index("static wmOperatorStatus editor_transform_exec(")
    end = source.index("static wmOperatorStatus editor_capture_exec(", begin)
    function = source[begin:end]
    helpers = source[source.index("static wmXrEditorPanel editor_full_panel("):
                     source.index("void WM_xr_editor_surface_info_get(")]
    queries = source[source.index("void WM_xr_editor_surface_pose_get("):
                     source.index("bool WM_xr_editor_surface_event_filter(")]
    # Compile the real enum names/values, not a mock that can mask Blender API changes.
    enums = (ROOT / "blender-5.1.2/source/blender/makesdna/DNA_space_enums.h").read_text()
    enum_begin = enums.index("enum eSpace_Type {")
    enum_end = enums.index("};", enum_begin) + 2
    preamble = PREAMBLE.replace("// SPACE_ENUM_FROM_BLENDER", enums[enum_begin:enum_end])
    with tempfile.TemporaryDirectory(prefix="xr-editor-input-") as directory:
        folder = Path(directory)
        code = folder / "input.cc"
        code.write_text(preamble + helpers + queries + function + TESTS)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", str(code),
                        f"/Fe:{folder / 'input.exe'}", f"/Fo:{folder / 'input.obj'}"], check=True)
        subprocess.run([str(folder / "input.exe")], check=True,
                       env={**os.environ, "XRWB_TASK_TEST_ALLOW": "1"})


if __name__ == "__main__":
    main()
