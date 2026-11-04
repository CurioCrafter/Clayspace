"""Compile the guarded native injector, XR enqueue and unsafe-RNA-input rejection.

Run in a Visual Studio developer shell. This verifies the queue boundary and
guards, not an OpenXR runtime or physical controller.
"""
from pathlib import Path
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parent / "blender-5.1.2/source/blender"


def function(text, signature):
    start = text.index(signature)
    cursor = text.index("{", start)
    depth = 1
    end = cursor + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


PREAMBLE = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <vector>
using uint=unsigned int;
using wmOperatorStatus=int;
using wmEventType=int;
using eWM_EventFlag=int;
using wmEventModifierFlag=int;
enum { G_FLAG_EVENT_SIMULATE=1, V3D_OFSDRAW_XR_SHOW_PASSTHROUGH=2,
       OPERATOR_CANCELLED=0, OPERATOR_FINISHED=1, RPT_ERROR=1,
       XR_BOOLEAN_INPUT=1, XR_FLOAT_INPUT=2, XR_POSE_INPUT=3, XR_AXIS0_NEG=2,
       KM_PRESS=1, KM_RELEASE=2, KM_NOTHING=3, EVT_XR_ACTION=10, EVT_DATA_XR=11,
       KM_SHIFT=1, KM_CTRL=2, KM_ALT=4, KM_OSKEY=8, KM_HYPER=16 };
struct { int f=0; } G;
bool main_thread=true;
bool BLI_thread_is_main() { return main_thread; }
#define STREQ(a,b) (std::strcmp(a,b)==0)
#define STRNCPY(a,b) std::snprintf(a,sizeof(a),"%s",b)
#define BLI_assert assert
template<typename T,typename... Args> bool ELEM(T v,Args... args) { return ((v==args)||...); }
bool ISKEYBOARD(int t) { return t==20; }
bool ISMOUSE_BUTTON(int t) { return t==21; }
bool ISMOUSE_MOTION(int t) { return t==22; }
int BLI_str_utf8_size_or_error(const char *s) { return std::strlen(s)==1 ? 1 : -1; }
struct wmOperatorType {};
struct IDProperty {};
struct ReportList { std::vector<std::string> messages; };
void BKE_report(ReportList *r,int,const char *message) { r->messages.emplace_back(message); }
struct Params {
  std::string action="pointer", user_path="/user/hand/right";
  float location[3]={1,2,3}, rotation[4]={2,0,0,0}; int value=KM_PRESS;
};
struct wmOperator { Params *ptr; ReportList *reports; };
void RNA_string_get(Params *p,const char *key,char *out) {
  std::strcpy(out,STREQ(key,"action")?p->action.c_str():p->user_path.c_str());
}
void RNA_float_get_array(Params *p,const char *key,float *out) {
  bool loc=STREQ(key,"controller_location");
  std::copy_n(loc?p->location:p->rotation,loc?3:4,out);
}
int RNA_enum_get(Params *p,const char*) { return p->value; }
void copy_v3_v3(float *to,const float *from) { std::copy_n(from,3,to); }
void copy_qt_qt(float *to,const float *from) { std::copy_n(from,4,to); }
struct wmXrActionData {
  char action_set[64]{},action[64]{},user_path[64]{},user_path_other[64]{};
  int type=0; float state[2]{},state_other[2]{},float_threshold=0;
  float controller_loc[3]{},controller_rot[4]{},controller_loc_other[3]{},controller_rot_other[4]{};
  wmOperatorType *ot=nullptr; IDProperty *op_properties=nullptr; bool bimanual=false;
};
template<typename T> T *MEM_new_zeroed(const char*) { return new T{}; }
struct wmXrAction {
  const char *name="pointer"; int type=XR_FLOAT_INPUT; uint count_subaction_paths=2;
  const char **subaction_paths; wmOperatorType *ot; IDProperty *op_properties;
  float *float_thresholds; int *axis_flags;
};
struct wmXrActionSet { const char *name="xr_workbench_controllers"; };
struct wmXrRuntimeData {
  struct { wmXrActionSet *active_action_set; } session_state;
  void *area=(void*)1; void *ghost_context=(void*)2;
};
struct wmXrData {
  wmXrRuntimeData *runtime;
  struct { int draw_flags=0; } session_settings;
};
struct wmWindowManager { wmXrData xr; };
struct wmEvent {
  int type=0,val=0,flag=0,custom=0; void *customdata=nullptr; bool customdata_free=false;
  int xy[2]{},modifier=0,tablet=0; char utf8_buf[8]{};
};
struct WindowRuntime { wmEvent *eventstate; };
struct wmWindow { WindowRuntime *runtime; };
struct bContext { wmWindowManager *wm; wmWindow *win; };
wmWindow *root_window;
wmWindowManager *CTX_wm_manager(bContext *c) { return c->wm; }
wmWindow *CTX_wm_window(bContext *c) { return c->win; }
bool session_ready=true;
bool WM_xr_session_is_ready(wmXrData *x) { return session_ready&&x->runtime; }
wmWindow *wm_xr_session_root_window_or_fallback_get(wmWindowManager*,wmXrRuntimeData*) {
  return root_window;
}
wmXrAction *registered_action;
void *GHOST_XrGetActionCustomdata(void*,const char *set,const char *name) {
  assert(STREQ(set,"xr_workbench_controllers"));
  return registered_action && STREQ(name,registered_action->name) ? registered_action : nullptr;
}
std::vector<wmEvent> queued;
wmEvent *WM_event_add(wmWindow *win,const wmEvent *e) {
  assert(win==root_window); queued.push_back(*e); return &queued.back();
}
wmEvent *WM_event_add_simulate(wmWindow *win,const wmEvent *e) { return WM_event_add(win,e); }
void WM_event_tablet_data_default_set(int *t) { *t=0; }
void wm_event_add_xrevent(wmWindow*,wmXrActionData*,short);
'''

TESTS = r'''
int main() {
  const char *envs[]={"XRWB_TASK_TEST_ALLOW","XRWB_HAND_MENU_SIMULATOR","XRWB_EDITOR_TEST_ALLOW",
    "SIMULATED_ENABLE","XRWB_STEAMVR_SMOKE","XRWB_STEAMVR","XRWB_INDEX_CAMERA_CONSENT",
    "XRWB_INDEX_CAMERA_OVERLAY","XRWB_INDEX_CAMERA_GESTURE","XRWB_PHYSICAL_GESTURE_OPT_IN"};
  for (const char *key : envs) _putenv_s(key,"");
  wmEvent event_state; WindowRuntime win_runtime{&event_state}; wmWindow win{&win_runtime};
  root_window=&win;
  wmXrActionSet action_set; wmXrRuntimeData runtime{{&action_set}};
  wmWindowManager manager{{&runtime}}; bContext C{&manager,&win};
  Params props; ReportList reports; wmOperator op{&props,&reports};
  wmOperatorType type; IDProperty properties;
  const char *paths[]={"/user/hand/left","/user/hand/right"};
  float thresholds[]={0.3f,0.4f}; int axes[]={0,0};
  wmXrAction action{"pointer",XR_FLOAT_INPUT,2,paths,&type,&properties,thresholds,axes};
  registered_action=&action;
  assert(!wm_xr_test_action_poll(&C));
  assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_CANCELLED && queued.empty());
  G.f=G_FLAG_EVENT_SIMULATE;
  _putenv_s("XRWB_TASK_TEST_ALLOW","1");
  assert(!wm_xr_test_action_poll(&C));
  _putenv_s("XRWB_HAND_MENU_SIMULATOR","1");
  _putenv_s("XRWB_EDITOR_TEST_ALLOW","1");
  assert(!wm_xr_test_action_poll(&C));
  _putenv_s("SIMULATED_ENABLE","1");
  assert(wm_xr_test_action_poll(&C));
  for (const char *key : envs) {
    if (std::strstr(key,"CAMERA") || STREQ(key,"XRWB_PHYSICAL_GESTURE_OPT_IN")) {
      _putenv_s(key,"1"); assert(!wm_xr_test_action_poll(&C)); _putenv_s(key,"");
    }
  }
  manager.xr.session_settings.draw_flags=V3D_OFSDRAW_XR_SHOW_PASSTHROUGH;
  assert(!wm_xr_test_action_poll(&C)); manager.xr.session_settings.draw_flags=0;
  main_thread=false; assert(!wm_xr_test_action_poll(&C)); main_thread=true;
  session_ready=false; assert(!wm_xr_test_action_poll(&C)); session_ready=true;
  G.f=0; assert(!wm_xr_test_action_poll(&C)); G.f=G_FLAG_EVENT_SIMULATE;
  _putenv_s("XRWB_TASK_TEST_ALLOW","true"); assert(!wm_xr_test_action_poll(&C));
  _putenv_s("XRWB_TASK_TEST_ALLOW","1");

  assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_FINISHED && queued.size()==1);
  auto *data=static_cast<wmXrActionData*>(queued.back().customdata);
  assert(queued.back().type==EVT_XR_ACTION && queued.back().custom==EVT_DATA_XR);
  assert(queued.back().val==KM_PRESS && queued.back().customdata_free);
  assert(STREQ(data->action_set,action_set.name) && STREQ(data->action,action.name));
  assert(STREQ(data->user_path,paths[1]) && data->ot==&type && data->op_properties==&properties);
  assert(data->controller_loc[0]==1 && data->controller_loc[2]==3);
  assert(data->controller_rot[0]==1 && data->controller_rot[1]==0);
  assert(data->state[0]==1 && data->float_threshold==0.4f && !data->bimanual);
  assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_FINISHED && queued.size()==2); // held
  props.value=KM_RELEASE;
  assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_FINISHED && queued.size()==3);
  data=static_cast<wmXrActionData*>(queued.back().customdata);
  assert(queued.back().val==KM_RELEASE && data->state[0]==0);
  const auto count=queued.size();
  auto rejected=[&]() { assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_CANCELLED); assert(queued.size()==count); };
  props.action="arbitrary_operator"; rejected(); props.action="pointer";
  props.user_path="/user/other"; rejected(); props.user_path=paths[1];
  action.count_subaction_paths=1; rejected(); action.count_subaction_paths=2;
  action.ot=nullptr; rejected(); action.ot=&type;
  action.type=XR_POSE_INPUT; rejected(); action.type=XR_FLOAT_INPUT;
  runtime.area=nullptr; rejected(); runtime.area=(void*)1;
  action_set.name="unrelated"; rejected(); action_set.name="xr_workbench_controllers";
  runtime.session_state.active_action_set=nullptr; rejected(); runtime.session_state.active_action_set=&action_set;
  wmWindow other{&win_runtime}; C.win=&other; rejected(); C.win=&win;
  props.value=KM_NOTHING; rejected(); props.value=KM_PRESS;
  props.location[0]=std::numeric_limits<float>::infinity(); rejected(); props.location[0]=1;
  props.rotation[0]=std::numeric_limits<float>::quiet_NaN(); rejected(); props.rotation[0]=0;
  rejected(); props.rotation[0]=2;
  axes[1]=XR_AXIS0_NEG;
  assert(wm_xr_test_action_exec(&C,&op)==OPERATOR_FINISHED);
  assert(static_cast<wmXrActionData*>(queued.back().customdata)->state[0]==-1);
  axes[1]=0;

  // The second sanctioned route remains explicitly opt-in and camera-off.
  _putenv_s("SIMULATED_ENABLE",""); assert(!wm_xr_test_action_poll(&C));
  _putenv_s("XRWB_STEAMVR_SMOKE","1"); assert(!wm_xr_test_action_poll(&C));
  _putenv_s("XRWB_STEAMVR","1"); assert(wm_xr_test_action_poll(&C));
  _putenv_s("XRWB_TASK_TEST_ALLOW",""); assert(!wm_xr_test_action_poll(&C));
  for (auto &event : queued) delete static_cast<wmXrActionData*>(event.customdata);
  queued.clear();

  // Unsupported generic XR simulation must return before dereferencing the window.
  assert(rna_Window_event_add_simulate(nullptr,&reports,EVT_XR_ACTION,KM_PRESS,nullptr,
                                     0,0,false,false,false,false,false)==nullptr);
  assert(queued.empty() && reports.messages.back().find("XR_ACTION")!=std::string::npos);
  assert(rna_Window_event_add_simulate(&win,&reports,21,KM_PRESS,nullptr,
                                     3,4,false,false,false,false,false)!=nullptr);
  assert(queued.size()==1 && queued.back().type==21 && queued.back().xy[0]==3);
  puts("XR_ACTION_INJECTOR_PASS actual_exec=1 actual_enqueue=1 guarded_opt_in=1 registered_action_only=1 normalized_pose=1 hand_binding=1 press_hold_release=1 no_live_state_mutation=1 unsafe_rna_rejected=1 hardware=0");
}
'''


def main():
    operators = (SOURCE / "windowmanager/xr/intern/wm_xr_operators.cc").read_text()
    events = (SOURCE / "windowmanager/intern/wm_event_system.cc").read_text()
    rna = (SOURCE / "makesrna/intern/rna_wm_api.cc").read_text()
    assert "WM_operatortype_append(WM_OT_xr_test_action)" in operators
    code = PREAMBLE + function(events, "void wm_event_add_xrevent(")
    for signature in ("static bool wm_xr_test_env_enabled(", "static bool wm_xr_test_action_poll(",
                      "static wmOperatorStatus wm_xr_test_action_exec("):
        code += function(operators, signature)
    code += function(rna, "static wmEvent *rna_Window_event_add_simulate(") + TESTS
    with tempfile.TemporaryDirectory(prefix="xr-action-injector-") as temp:
        folder = Path(temp)
        cpp = folder / "injector.cc"
        cpp.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/D_CRT_SECURE_NO_WARNINGS", str(cpp),
                        f"/Fe:{folder / 'injector.exe'}", f"/Fo:{folder / 'injector.obj'}"], check=True)
        subprocess.run([str(folder / "injector.exe")], check=True)


if __name__ == "__main__":
    main()
