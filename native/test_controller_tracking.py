"""Compile actual controller state/pose guards with synthetic OpenXR results.

Run in a Visual Studio developer shell. No runtime, camera, or headset starts.
This proves tracking-loss control flow, not physical Index tracking.
"""
from pathlib import Path
import subprocess
import tempfile

from test_d3d_lifetime import body

ROOT = Path(__file__).resolve().parent / "blender-5.1.2"

PREAMBLE = r'''
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
using XrSpaceLocationFlags = unsigned long long;
enum { XR_SPACE_LOCATION_ORIENTATION_VALID_BIT=1, XR_SPACE_LOCATION_POSITION_VALID_BIT=2,
       XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT=4, XR_SPACE_LOCATION_POSITION_TRACKED_BIT=8,
       XR_TYPE_ACTION_STATE_BOOLEAN, XR_TYPE_ACTION_STATE_FLOAT, XR_TYPE_ACTION_STATE_VECTOR2F,
       XR_TYPE_ACTION_STATE_POSE, XR_TYPE_SPACE_LOCATION,
       GHOST_kXrActionTypeBooleanInput, GHOST_kXrActionTypeFloatInput,
       GHOST_kXrActionTypeVector2fInput, GHOST_kXrActionTypePoseInput,
       GHOST_kXrActionTypeVibrationOutput, XR_NULL_HANDLE=0 };
struct XrQuaternionf { float x=0,y=0,z=0,w=1; };
struct XrPosef { XrQuaternionf orientation; struct { float x=0,y=0,z=0; } position; };
struct XrSpaceLocation { int type; XrSpaceLocationFlags locationFlags=15; XrPosef pose; };
struct XrActionStateBoolean { int type; bool isActive=false,currentState=false; };
struct XrActionStateFloat { int type; bool isActive=false; float currentState=0; };
struct XrActionStateVector2f { int type; bool isActive=false; float currentState[2]={}; };
struct XrActionStatePose { int type; bool isActive=false; };
struct GHOST_XrPose { float position[3]={},orientation_quat[4]={}; bool is_active=false; };
using XrSpace = int;
struct Space { int handle=1; XrSpace getSpace() { return handle; } } space;
struct Subaction { Space *space; } binding{&space};
static bool active=true;
static XrSpaceLocation located{XR_TYPE_SPACE_LOCATION};
int xrGetActionStateBoolean(int, int*, XrActionStateBoolean *s) { s->isActive=active; s->currentState=true; return 0; }
int xrGetActionStateFloat(int, int*, XrActionStateFloat *s) { s->isActive=active; s->currentState=1; return 0; }
int xrGetActionStateVector2f(int, int*, XrActionStateVector2f *s) { s->isActive=active; s->currentState[0]=1; s->currentState[1]=.5f; return 0; }
int xrGetActionStatePose(int, int*, XrActionStatePose *s) { s->isActive=active; return 0; }
int xrLocateSpace(int, int, long long, XrSpaceLocation *s) { *s=located; return 0; }
#define CHECK_XR(call,...) assert((call)==0)
void copy_openxr_pose_to_ghost_pose(const XrPosef &p, GHOST_XrPose &g) {
  g.position[0]=p.position.x; g.position[1]=p.position.y; g.position[2]=p.position.z;
  g.orientation_quat[0]=p.orientation.w; g.orientation_quat[1]=p.orientation.x;
  g.orientation_quat[2]=p.orientation.y; g.orientation_quat[3]=p.orientation.z;
}
struct wmXrActionData {
  float controller_loc[3]={},controller_rot[4]={},controller_loc_other[3]={},controller_rot_other[4]={};
  bool bimanual=false;
};
void copy_v3_v3(float *d,const float *s) { memcpy(d,s,3*sizeof(float)); }
void copy_qt_qt(float *d,const float *s) { memcpy(d,s,4*sizeof(float)); }
void zero_v3(float *v) { memset(v,0,3*sizeof(float)); }
void zero_v4(float *v) { memset(v,0,4*sizeof(float)); }
void unit_qt(float *v) { zero_v4(v); v[0]=1; }
using uint = unsigned;
struct wmXrController { GHOST_XrPose aim_pose,grip_pose; bool aim_active=false,grip_active=false; };
struct Controllers { wmXrController item,other; int count=1; };
struct wmXrHandMenu {
  struct { bool visible=false; GHOST_XrPose pose; } surface;
  int controller=0;
  bool simulated=false;
};
struct Runtime {
  struct { bool is_view_data_set=true; Controllers controllers; } session_state;
  wmXrHandMenu hand_menu;
  GHOST_XrPose viewer;
  float scale=1.0f;
  bool anchor_ready=true;
};
using wmXrRuntimeData=Runtime;
struct wmXrData { Runtime *runtime; bool ready=true; };
bool WM_xr_session_is_ready(const wmXrData *xr) { return xr->runtime && xr->ready; }
int BLI_listbase_count(const Controllers *c) { return c->count; }
const wmXrController *BLI_findlink(const Controllers *c,uint index) {
  return index>=uint(c->count)?nullptr:(index==0?&c->item:&c->other);
}
wmXrController *BLI_findlink(Controllers *c,uint index) {
  return const_cast<wmXrController *>(BLI_findlink(static_cast<const Controllers *>(c),index));
}
bool wm_xr_ui_anchor_get(Runtime *runtime,GHOST_XrPose &viewer,float &scale) {
  viewer=runtime->viewer; scale=runtime->scale; return runtime->anchor_ready;
}
void add_v3_v3(float *a,const float *b) { for(int i=0;i<3;i++) a[i]+=b[i]; }
void mul_qt_v3(const float *q,float *v) {
  const float cross[3]={q[2]*v[2]-q[3]*v[1],q[3]*v[0]-q[1]*v[2],q[1]*v[1]-q[2]*v[0]};
  const float cross2[3]={q[2]*cross[2]-q[3]*cross[1],q[3]*cross[0]-q[1]*cross[2],q[1]*cross[1]-q[2]*cross[0]};
  for(int i=0;i<3;i++) v[i]+=2*(q[0]*cross[i]+cross2[i]);
}
#define BLI_assert assert
struct wmWindowManager { wmXrData xr; };
struct bContext { wmWindowManager *wm; };
wmWindowManager *CTX_wm_manager(bContext *C) { return C->wm; }
struct wmOperator { void *customdata=nullptr; };
enum { KM_PRESS=1, KM_RELEASE=2 };
struct wmEvent { void *customdata; int val=KM_PRESS; bool matches=true; };
static int cleanup=0;
void wm_xr_grab_uninit(wmOperator *op) { ++cleanup; op->customdata=nullptr; }
void wm_xr_fly_uninit(wmOperator *op) { ++cleanup; op->customdata=nullptr; }
void wm_xr_navigation_teleport_uninit(wmOperator *op) { ++cleanup; op->customdata=nullptr; }
enum { OPERATOR_CANCELLED=0, OPERATOR_RUNNING_MODAL=1, OPERATOR_PASS_THROUGH=2, OPERATOR_FINISHED=3 };
using wmOperatorStatus=int;
bool wm_xr_operator_test_event(const wmOperator*,const wmEvent *event) { return event->matches; }
static int handlers=0;
void WM_event_add_modal_handler(bContext*,wmOperator*) { ++handlers; }
'''


def main():
    ghost = (ROOT / "intern/ghost/intern/GHOST_XrAction.cc").read_text()
    wm = ROOT / "source/blender/windowmanager/xr/intern"
    session = (wm / "wm_xr_session.cc").read_text()
    operators = (wm / "wm_xr_operators.cc").read_text()
    editor = (wm / "wm_xr_editor.cc").read_text()
    opening = body(editor, "static wmOperatorStatus hand_menu_exec(")
    assert "menu.controller = 1 - menu.controller" not in opening
    assert opening.count("menu.controller =") == 1
    code = PREAMBLE
    code += "bool hand_menu_anchor(wmXrRuntimeData *runtime)" + body(
        editor, "static bool hand_menu_anchor(")
    for name, size, argument in (("location", 3, "r_location"), ("rotation", 4, "r_rotation")):
        code += f"bool WM_xr_session_state_controller_aim_{name}_get(const wmXrData *xr,uint subaction_idx,float {argument}[{size}])" + body(
            session, f"bool WM_xr_session_state_controller_aim_{name}_get(")
    code += "bool ghost_xr_controller_pose_valid(const XrSpaceLocation &location)" + body(
        ghost, "static bool ghost_xr_controller_pose_valid(")
    update = body(ghost, "void GHOST_XrAction::updateState(")
    code += r'''
void update(int type_, void *states_, long long predicted_display_time=1,
            const Subaction *subaction=&binding) {
  int session=0,state_info=0,reference_space=0,subaction_idx=0;
  const char *action_name="test";
  switch (type_)
''' + body(update, "switch (type_)") + "}\n"
    code += "bool wm_xr_operator_pose_valid(const wmXrActionData *data)" + body(
        operators, "static bool wm_xr_operator_pose_valid(")
    code += "bool wm_xr_navigation_input_blocked(bContext *C)" + body(
        operators, "static bool wm_xr_navigation_input_blocked(")
    code += "wmOperatorStatus wm_xr_navigation_drain_invoke(bContext *C,wmOperator *op,const wmEvent *event)" + body(
        operators, "static wmOperatorStatus wm_xr_navigation_drain_invoke(")
    creation = body(session, "static wmXrActionData *wm_xr_session_event_create(")
    code += r'''
void event_poses(wmXrActionData *data, const GHOST_XrPose *controller_aim_pose,
                 const GHOST_XrPose *controller_aim_pose_other, bool bimanual) {
''' + creation[creation.index("if (controller_aim_pose)"):creation.index("data->ot =")] + "}\n"
    lookup = body(session, "static const GHOST_XrPose *wm_xr_session_controller_aim_pose_find(")
    assert "controller && controller->aim_active" in lookup
    pose_calc = body(session, "static void wm_xr_session_controller_pose_calc(")
    assert pose_calc.index("!raw_pose->is_active") < pose_calc.index("wm_xr_pose_to_mat")
    names = ("grab_invoke", "grab_modal", "fly_invoke", "fly_modal", "teleport_modal")
    for name in names:
        function = body(operators, "static wmOperatorStatus wm_xr_navigation_" + name + "(")
        start = function.index("if (!wm_xr_operator_pose_valid(")
        guard_body = body(function[start:], "if (")
        guard = function[start: function.index(guard_body, start) + len(guard_body)]
        # These exact guards must precede native navigation state mutation/commit.
        mutation = {"grab_invoke": "wm_xr_grab_init", "grab_modal": "wm_xr_navigation_grab_apply",
                    "fly_invoke": "wm_xr_fly_init", "fly_modal": "WM_xr_session_state_nav_location_set",
                    "teleport_modal": "wm_xr_navigation_teleport_data_update"}[name]
        assert start < function.index(mutation)
        code += f"int guard_{name}(wmOperator *op,const wmEvent *event) {{\n"
        code += "const auto *actiondata=static_cast<const wmXrActionData *>(event->customdata);\n"
        code += guard + " return OPERATOR_RUNNING_MODAL; }\n"
    for name in ("grab_invoke", "grab_modal", "fly_invoke", "fly_modal",
                 "teleport_invoke", "teleport_modal"):
        function = body(operators, "static wmOperatorStatus wm_xr_navigation_" + name + "(")
        marker = ("if (wm_xr_navigation_input_blocked(C))" if name.endswith("invoke")
                  else "if (!op->customdata || wm_xr_navigation_input_blocked(C))")
        guard_start = function.index(marker)
        guard_body = body(function[guard_start:], "if (")
        guard_end = function.index(guard_body, guard_start) + len(guard_body)
        assert function.index("wm_xr_operator_test_event(op, event)") < guard_start
        # Actual matching + menu guard, before any allocation, pose read, or move.
        code += f"int menu_{name}(bContext *C,wmOperator *op,const wmEvent *event)"
        code += function[:guard_end] + " return OPERATOR_FINISHED; }\n"
    code += r'''
int main() {
  Runtime runtime; wmXrData xr{&runtime}; float location[3],rotation[4];
  // Actual native anchor function: either action can open on its exact hand,
  // neither tracking loss nor a different active hand can silently transfer it.
  unit_qt(runtime.viewer.orientation_quat);
  auto &menu=runtime.hand_menu;
  auto &controllers=runtime.session_state.controllers;
  controllers.count=2;
  controllers.item.grip_active=controllers.other.grip_active=true;
  controllers.item.grip_pose.position[0]=-0.4f;
  controllers.other.grip_pose.position[0]=0.6f;
  for(int index:{0,1}) {
    menu.controller=index;
    assert(hand_menu_anchor(&runtime));
    assert(std::abs(menu.surface.pose.position[0]-(index==0?-0.4f:0.6f))<1e-6f);
    assert(std::abs(menu.surface.pose.position[1]-0.17f)<1e-6f);
    auto *opening_hand=BLI_findlink(&controllers,index);
    opening_hand->grip_pose.position[1]=0.5f;
    assert(hand_menu_anchor(&runtime) && std::abs(menu.surface.pose.position[1]-0.67f)<1e-6f);
    opening_hand->grip_active=false;
    assert(!hand_menu_anchor(&runtime) && menu.controller==index);
    opening_hand->grip_active=true;
    opening_hand->grip_pose.position[1]=0;
  }
  runtime.scale=3;
  assert(hand_menu_anchor(&runtime) && std::abs(menu.surface.pose.position[1]-0.51f)<1e-6f);
  runtime.anchor_ready=false; assert(!hand_menu_anchor(&runtime)); runtime.anchor_ready=true;
  controllers.count=1; menu.controller=1; assert(!hand_menu_anchor(&runtime));
  menu.controller=0; controllers.item.grip_active=false;
  menu.simulated=true; assert(hand_menu_anchor(&runtime)); menu.simulated=false;
  runtime.scale=1;
  puts("XR_HAND_MENU_ANCHOR_PASS exact_opening_hand=2 tracking_loss_no_transfer=1 follows_grip=1 scaled_offset=1 hardware=0");
  assert(!WM_xr_session_state_controller_aim_rotation_get(&xr,0,rotation));
  assert(rotation[0]==0);  // Existing controller but tracking inactive.
  assert(!WM_xr_session_state_controller_aim_location_get(&xr,0,location));
  runtime.session_state.controllers.item.aim_active=true;
  runtime.session_state.controllers.item.aim_pose.orientation_quat[0]=1;
  assert(WM_xr_session_state_controller_aim_rotation_get(&xr,0,rotation) && rotation[0]==1);
  assert(!WM_xr_session_state_controller_aim_rotation_get(&xr,255,rotation));
  runtime.session_state.is_view_data_set=false;
  assert(!WM_xr_session_state_controller_aim_rotation_get(&xr,0,rotation));
  xr.runtime=nullptr;
  assert(!WM_xr_session_state_controller_aim_rotation_get(&xr,0,rotation));
  bool button=true; float axis=1,stick[2]={1,1}; GHOST_XrPose pose;
  update(GHOST_kXrActionTypePoseInput,&pose); assert(pose.is_active && pose.orientation_quat[0]==1);
  active=false;
  update(GHOST_kXrActionTypeBooleanInput,&button); assert(!button);
  update(GHOST_kXrActionTypeFloatInput,&axis); assert(axis==0);
  update(GHOST_kXrActionTypeVector2fInput,stick); assert(stick[0]==0 && stick[1]==0);
  update(GHOST_kXrActionTypePoseInput,&pose); assert(!pose.is_active && pose.orientation_quat[0]==0);
  active=true;
  for (unsigned bit=1;bit<=8;bit<<=1) {
    located.locationFlags=15 & ~bit;
    update(GHOST_kXrActionTypePoseInput,&pose); assert(!pose.is_active);
  }
  located.locationFlags=15;
  update(GHOST_kXrActionTypePoseInput,&pose); assert(pose.is_active);
  update(GHOST_kXrActionTypePoseInput,&pose,0); assert(!pose.is_active);
  update(GHOST_kXrActionTypePoseInput,&pose,1,nullptr); assert(!pose.is_active);
  for (float bad : {0.0f,std::numeric_limits<float>::quiet_NaN(),std::numeric_limits<float>::infinity(),1.0e30f}) {
    located.pose.orientation.w=bad;
    update(GHOST_kXrActionTypePoseInput,&pose); assert(!pose.is_active);
  }
  located.pose.orientation.w=1;
  located.pose.position.x=std::numeric_limits<float>::infinity();
  update(GHOST_kXrActionTypePoseInput,&pose); assert(!pose.is_active);
  located.pose.position.x=0;
  update(GHOST_kXrActionTypePoseInput,&pose); assert(pose.is_active);
  wmXrActionData data; event_poses(&data,nullptr,nullptr,false);
  assert(!wm_xr_operator_pose_valid(&data));
  wmOperator op; wmEvent event{&data};
  assert(guard_grab_invoke(&op,&event)==OPERATOR_CANCELLED && cleanup==0);
  assert(guard_grab_modal(&op,&event)==OPERATOR_CANCELLED && cleanup==1);
  assert(guard_fly_invoke(&op,&event)==OPERATOR_CANCELLED && cleanup==1);
  assert(guard_fly_modal(&op,&event)==OPERATOR_CANCELLED && cleanup==2);
  assert(guard_teleport_modal(&op,&event)==OPERATOR_CANCELLED && cleanup==3);
  event_poses(&data,&pose,nullptr,false); assert(wm_xr_operator_pose_valid(&data));
  assert(guard_grab_modal(&op,&event)==OPERATOR_RUNNING_MODAL && cleanup==3);
  data.bimanual=true; assert(!wm_xr_operator_pose_valid(&data));
  event_poses(&data,&pose,&pose,true); assert(wm_xr_operator_pose_valid(&data));
  data.controller_loc_other[0]=std::numeric_limits<float>::quiet_NaN();
  assert(!wm_xr_operator_pose_valid(&data));
  wmWindowManager wm{{&runtime}}; bContext context{&wm};
  assert(!wm_xr_navigation_input_blocked(&context));
  context.wm=nullptr; assert(wm_xr_navigation_input_blocked(&context)); context.wm=&wm;
  wm.xr.runtime=nullptr; assert(wm_xr_navigation_input_blocked(&context)); wm.xr.runtime=&runtime;
  wm.xr.ready=false; assert(wm_xr_navigation_input_blocked(&context)); wm.xr.ready=true;
  using Guard=int (*)(bContext*,wmOperator*,const wmEvent*);
  Guard invokes[]={menu_grab_invoke,menu_fly_invoke,menu_teleport_invoke};
  Guard modals[]={menu_grab_modal,menu_fly_modal,menu_teleport_modal};
  for (int i=0;i<3;i++) {
    op.customdata=nullptr; event.val=KM_PRESS; event.matches=true;
    runtime.hand_menu.surface.visible=false;
    assert(invokes[i](&context,&op,&event)==OPERATOR_FINISHED);
    runtime.hand_menu.surface.visible=true;
    const int before_handlers=handlers;
    event.matches=false;
    assert(invokes[i](&context,&op,&event)==OPERATOR_PASS_THROUGH);
    assert(modals[i](&context,&op,&event)==OPERATOR_PASS_THROUGH && handlers==before_handlers);
    event.matches=true;
    assert(invokes[i](&context,&op,&event)==OPERATOR_RUNNING_MODAL && handlers==before_handlers+1);
    assert(!op.customdata);  // No navigation allocation while the hand UI owns input.
    runtime.hand_menu.surface.visible=false;
    assert(modals[i](&context,&op,&event)==OPERATOR_RUNNING_MODAL);  // Held after menu closed.
    event.val=KM_RELEASE;
    assert(modals[i](&context,&op,&event)==OPERATOR_CANCELLED && !op.customdata);
    op.customdata=&data; event.val=KM_PRESS;
    assert(modals[i](&context,&op,&event)==OPERATOR_FINISHED);  // New live gesture is permitted.
    runtime.hand_menu.surface.visible=true;
    const int before_cleanup=cleanup;
    assert(modals[i](&context,&op,&event)==OPERATOR_RUNNING_MODAL);
    assert(!op.customdata && cleanup==before_cleanup+1);  // Cancels allocated nav and draw state.
    runtime.hand_menu.surface.visible=false;
    assert(modals[i](&context,&op,&event)==OPERATOR_RUNNING_MODAL);  // Cannot resume this gesture.
    event.val=KM_RELEASE;
    assert(modals[i](&context,&op,&event)==OPERATOR_CANCELLED);
    assert(invokes[i](&context,&op,&event)==OPERATOR_FINISHED);  // Unblocked prefix only.
    runtime.hand_menu.surface.visible=true;
    assert(invokes[i](&context,&op,&event)==OPERATOR_CANCELLED);  // No drain handler on release.
    op.customdata=&data; wm.xr.runtime=nullptr;
    assert(modals[i](&context,&op,&event)==OPERATOR_CANCELLED && !op.customdata);
    wm.xr.runtime=&runtime;
  }
  puts("XR_CONTROLLER_TRACKING_PASS inactive_neutral=3 pose_flags=4 zero_sentinel=1 navigation_cancel=3 hand_menu_nav_latch=3 session_null_safe=1 hardware=0");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-controller-tracking-") as directory:
        folder = Path(directory)
        source = folder / "tracking.cc"
        source.write_text(code)
        executable = folder / "tracking.exe"
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(source),
                        f"/Fe:{executable}", f"/Fo:{folder / 'tracking.obj'}"], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
