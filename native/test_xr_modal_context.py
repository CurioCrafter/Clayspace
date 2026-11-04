"""Compile actual XR dispatch, modal context validation and report forwarding.

This catches the native invoke-to-modal boundary skipped by direct bpy.ops and
Python event fixtures. It does not simulate OpenXR hardware or Python operators.
Run in a Visual Studio developer shell: python native/test_xr_modal_context.py
"""
from pathlib import Path
import subprocess
import tempfile

SOURCE = (Path(__file__).resolve().parent / "blender-5.1.2/source/blender/"
          "windowmanager/intern/wm_event_system.cc")


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
#include <cstdio>
#include <string>
#include <vector>
#define WITH_XR_OPENXR
#define BLI_assert assert
#define CLOG_ERROR(...) assert(false)
enum { SPACE_VIEW3D=1, RGN_TYPE_WINDOW=2, EVT_DATA_XR=3, EVT_XR_ACTION=4,
       TIMER=5, KM_PRESS=6, KM_RELEASE=7, WM_HANDLER_TYPE_OP=8, WM_HANDLER_DO_FREE=1,
       OP_IS_MODAL_CURSOR_REGION=2, RPT_OP_HOLD=1, RPT_PRINT=2,
       RPT_PRINT_HANDLED_BY_OWNER=4, RPT_INFO=10, RPT_WARNING=20, RPT_ERROR=30,
       OPERATOR_HANDLED=1, OPERATOR_CANCELLED=2, OPERATOR_RUNNING_MODAL=4 };
using eHandlerActionFlag=int;
constexpr int WM_HANDLER_CONTINUE=0, WM_HANDLER_BREAK=1;
struct ARegion { int id; };
struct ScrArea { int spacetype=SPACE_VIEW3D; struct { void *first=(void*)1; } spacedata;
  std::vector<ARegion*> regionbase; };
struct bScreen { std::vector<ScrArea*> areas; };
struct wmEvent { int type=EVT_XR_ACTION, val=KM_PRESS, custom=EVT_DATA_XR;
  void *customdata=nullptr; int xy[2]={0,0}; };
struct IDProperty { int value=0; };
bool IDP_EqualsProperties(IDProperty *a,IDProperty *b) { return a==b || (a&&b&&a->value==b->value); }
struct wmOperatorType { bool invoke=true, modal=true; void *srna=nullptr; const char *idname="XR_TEST"; };
struct Report { int type; std::string text; };
struct ReportList { int flag=0; std::vector<Report> list; };
struct wmOperator { wmOperatorType *type; IDProperty *properties; wmOperator *opm=nullptr;
  int flag=0; ReportList *reports=nullptr; };
struct bContext;
struct wmWindow;
struct wmEventHandler { int type=WM_HANDLER_TYPE_OP,flag=0;
  bool (*poll)(wmWindow*,ScrArea*,ARegion*,wmEvent*)=nullptr; };
struct wmEventHandler_Op { wmEventHandler head; wmOperator *op;
  struct { wmWindow *win=nullptr; ScrArea *area=nullptr; ARegion *region=nullptr;
           int region_type=RGN_TYPE_WINDOW; } context; };
template<typename T> struct ListBaseT {
  std::vector<T*> values;
  struct iterator { typename std::vector<T*>::iterator value;
    bool operator!=(const iterator &o) const { return value!=o.value; }
    iterator &operator++() { ++value; return *this; }
    T &operator*() const { return **value; }
  };
  iterator begin() { return {values.begin()}; }
  iterator end() { return {values.end()}; }
};
struct WindowRuntime { ListBaseT<wmEventHandler> modalhandlers; };
struct wmWindow { bScreen *screen; WindowRuntime *runtime; };
struct wmXrData { ScrArea *area=nullptr; };
struct ManagerRuntime { ReportList reports; };
struct wmWindowManager { wmXrData xr; ManagerRuntime *runtime; };
struct bContext { wmWindowManager *wm; wmWindow *win; ScrArea *area; ARegion *region; };
struct wmXrActionData { wmOperatorType *ot; IDProperty *op_properties; };
struct PointerRNA { void *type=nullptr, *data=nullptr; };
struct Main { struct { void *first=nullptr; } wm; } main_data;
Main *G_MAIN=&main_data;
bool xr_action_event_processing=false;
bool xr_editor_event_processing=false;
wmWindowManager *CTX_wm_manager(bContext *c) { return c->wm; }
wmWindow *CTX_wm_window(bContext *c) { return c->win; }
bScreen *CTX_wm_screen(bContext *c) { return c->win->screen; }
bScreen *WM_window_get_active_screen(wmWindow *w) { return w->screen; }
void CTX_wm_area_set(bContext *c,ScrArea *a) { c->area=a; }
void CTX_wm_region_set(bContext *c,ARegion *r) { c->region=r; }
ScrArea *WM_xr_session_area_get(wmXrData *x) { return x->area; }
#define ED_screen_areas_iter(win,screen,area) for (ScrArea *area : (screen)->areas)
int BLI_findindex(std::vector<ARegion*> *list,ARegion *r) {
  auto i=std::find(list->begin(),list->end(),r); return i==list->end() ? -1 : int(i-list->begin());
}
ARegion *BKE_area_find_region_type(ScrArea *a,int) {
  return a->regionbase.empty() ? nullptr : a->regionbase.front();
}
ARegion *BKE_area_find_region_xy(ScrArea *a,int t,const int*) { return BKE_area_find_region_type(a,t); }
bool WM_region_use_viewport(ScrArea *a,ARegion *r) { return a&&r; }
bool BLI_listbase_is_empty(std::vector<Report> *list) { return list->empty(); }
bool BKE_reports_contain(ReportList *r,int level) {
  return std::any_of(r->list.begin(),r->list.end(),[=](auto &v) { return v.type>=level; });
}
std::vector<std::string> printed;
int banners=0;
void BKE_reports_print(ReportList *r,int level) {
  for (auto &v : r->list) if (v.type>=level) printed.push_back(v.text);
}
void BKE_reports_move_to_reports(ReportList *to,ReportList *from) {
  to->list.insert(to->list.end(),from->list.begin(),from->list.end()); from->list.clear();
}
void WM_report_banner_show(wmWindowManager*,wmWindow*) { ++banners; }
void WM_reports_from_reports_move(wmWindowManager*,ReportList*);
void wm_handler_op_context(bContext*,wmEventHandler_Op*,const wmEvent*);
void wm_event_handler_return_value_check(bContext*,wmEvent*,int) {}
int invoked=0, modal_called=0;
ScrArea *expected_area=nullptr;
ARegion *expected_region=nullptr;
ReportList action_reports;
wmOperator action_op{};
wmEventHandler_Op action_handler{};
void wm_operator_invoke(bContext *c,wmOperatorType *ot,wmEvent*,PointerRNA *p,
                        void*,bool,bool) {
  ++invoked;
  assert(c->area==expected_area && c->region==expected_region);
  assert(xr_action_event_processing);
  action_op={ot,p?static_cast<IDProperty*>(p->data):nullptr,nullptr,0,&action_reports};
  action_handler.op=&action_op;
  action_handler.context={c->win,c->area,c->region,RGN_TYPE_WINDOW};
  // Mirror the native RUNNING_MODAL report consumption contract checked below.
  WM_reports_from_reports_move(c->wm,&action_reports);
  c->win->runtime->modalhandlers.values.push_back(&action_handler.head);
}
int wm_handler_operator_call(bContext *c,ListBaseT<wmEventHandler>*,wmEventHandler *base,
                             wmEvent *event,void*,void*) {
  ++modal_called;
  auto *h=reinterpret_cast<wmEventHandler_Op*>(base);
  wm_handler_op_context(c,h,event);
  assert(c->area==expected_area && c->region==expected_region);
  assert(xr_action_event_processing);
  WM_reports_from_reports_move(c->wm,h->op->reports);
  return WM_HANDLER_BREAK;
}
wmOperator *wm_operator_create(wmWindowManager*,wmOperatorType*,PointerRNA*,void*) { return &action_op; }
int WM_operator_call(bContext *c,wmOperator*) {
  assert(c->area==expected_area && c->region==expected_region);
  WM_reports_from_reports_move(c->wm,&action_reports);
  return OPERATOR_HANDLED|OPERATOR_CANCELLED;
}
void WM_operator_free(wmOperator*) { assert(false); }
'''

TESTS = r'''
int main() {
  ARegion desktop_region{1}, xr_region{2}, stale_region{3};
  ScrArea desktop, xr, former_xr;
  desktop.regionbase={&desktop_region}; xr.regionbase={&xr_region};
  former_xr.regionbase={&stale_region};
  bScreen screen{{&desktop}}; WindowRuntime window_runtime;
  wmWindow window{&screen,&window_runtime}; ManagerRuntime manager_runtime;
  wmWindowManager manager{{&xr},&manager_runtime};
  bContext C{&manager,&window,&desktop,&desktop_region};
  main_data.wm.first=&manager;
  wmOperatorType type; IDProperty properties; wmXrActionData data{&type,&properties};
  wmEvent event; event.customdata=&data;
  expected_area=&xr; expected_region=&xr_region;

  // Real dispatch invokes off-screen, then uses the actual modal validator.
  action_reports.list={{RPT_WARNING,"invoke warning"},{RPT_INFO,"quiet info"}};
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(invoked==1 && modal_called==0 && printed==std::vector<std::string>{"invoke warning"});
  assert(!xr_action_event_processing && C.area==nullptr && C.region==nullptr);
  action_reports.list={{RPT_ERROR,"held trigger error"}};
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(invoked==1 && modal_called==1 && printed.size()==2);
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(modal_called==2 && printed.size()==2); // consumed, never repeats per frame
  event.val=KM_RELEASE;
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(modal_called==3 && !xr_action_event_processing);

  // Timers and ordinary modal events must retain the same live off-screen context.
  wmEvent timer; timer.type=TIMER;
  wm_handler_op_context(&C,&action_handler,&timer);
  assert(C.area==&xr && C.region==&xr_region);
  ScrArea *old_area=nullptr; ARegion *old_region=nullptr;
  wm_handler_op_context_get_if_valid_legacy(&C,&action_handler,&timer,&old_area,&old_region);
  assert(old_area==nullptr && old_region==nullptr); // the pre-fix path loses XR context
  action_handler.context.region=&stale_region;
  wm_handler_op_context(&C,&action_handler,&timer);
  assert(C.area==&xr && C.region==nullptr);
  action_handler.context.area=&former_xr;
  wm_handler_op_context(&C,&action_handler,&timer);
  assert(C.area==nullptr && C.region==nullptr);
  action_handler.context.area=&xr; action_handler.context.region=&xr_region;
  manager.xr.area=nullptr;
  wm_handler_op_context(&C,&action_handler,&timer);
  assert(C.area==nullptr && C.region==nullptr); // stopped session rejects old area
  manager.xr.area=&xr;

  // Desktop modal ownership is unchanged.
  action_handler.context.area=&desktop; action_handler.context.region=&desktop_region;
  wm_handler_op_context(&C,&action_handler,&timer);
  assert(C.area==&desktop && C.region==&desktop_region);

  // Actual non-modal CANCELLED exec reports traverse the same scoped forwarder.
  window_runtime.modalhandlers.values.clear(); type.invoke=false; type.modal=false;
  action_reports.list={{RPT_ERROR,"cancelled command error"}};
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(printed.size()==3 && printed.back()=="cancelled command error");

  // The scope restores an enclosing XR dispatch and does not leak to desktop calls.
  xr_action_event_processing=true;
  wm_event_handle_xrevent(&C,&manager,&window,&event);
  assert(xr_action_event_processing);
  xr_action_event_processing=false;
  action_reports.list={{RPT_WARNING,"desktop"}};
  WM_reports_from_reports_move(&manager,&action_reports);
  assert(printed.size()==3);

  // Owner-printed/native-printed reports are not duplicated; held reports stay owned.
  xr_action_event_processing=true;
  for (int flag : {RPT_PRINT,RPT_PRINT_HANDLED_BY_OWNER,RPT_OP_HOLD}) {
    action_reports.flag=flag; action_reports.list={{RPT_ERROR,"already owned"}};
    WM_reports_from_reports_move(&manager,&action_reports);
    assert(printed.size()==3);
    assert(action_reports.list.empty()==(flag!=RPT_OP_HOLD));
  }
  action_reports.flag=0; action_reports.list={{RPT_INFO,"info is quiet"}};
  WM_reports_from_reports_move(&manager,&action_reports);
  assert(printed.size()==3);
  xr_action_event_processing=false;
  xr_editor_event_processing=true;
  action_reports.list={{RPT_ERROR,"ray menu button failed"}};
  WM_reports_from_reports_move(&manager,&action_reports);
  assert(printed.size()==4 && printed.back()=="ray menu button failed");
  xr_editor_event_processing=false;
  puts("XR_MODAL_CONTEXT_PASS actual_dispatch=1 actual_validator=1 legacy_null_reproduced=1 invoke_modal_release=1 timer_context=1 stale_area_rejected=1 stale_region_rejected=1 desktop_unchanged=1 cancelled_reports=1 modal_reports=1 ray_button_reports=1 reports_once=1 hardware=0");
}
'''


def main():
    source = SOURCE.read_text()
    report_source = (SOURCE.parents[2] / "blenkernel/intern/report.cc").read_text()
    assert "fflush(stdout)" in function(report_source, "void BKE_reports_print(")
    invoke = function(source, "static wmOperatorStatus wm_operator_invoke(")
    branch = invoke[invoke.index("else if (retval & OPERATOR_RUNNING_MODAL)"):]
    assert "(xr_action_event_processing || xr_editor_event_processing) && reports == nullptr" in branch
    assert "WM_reports_from_reports_move(wm, op->reports);" in branch
    reports = function(source, "static void wm_operator_reports(")
    assert "!(xr_action_event_processing || xr_editor_event_processing)" in reports
    code = PREAMBLE
    for signature in (
        "void WM_reports_from_reports_move(",
        "static void wm_handler_op_context_get_if_valid(",
        "static void wm_handler_op_context(",
        "static bool wm_event_xr_handler_matches_actiondata(",
        "static void wm_event_handle_xrevent(",
    ):
        code += function(source, signature)
    legacy = function(source, "static void wm_handler_op_context_get_if_valid(")
    start = legacy.index("#ifdef WITH_XR_OPENXR")
    end = legacy.index("#endif", start) + len("#endif")
    legacy = legacy[:start] + legacy[end:]
    code += legacy.replace("wm_handler_op_context_get_if_valid(",
                           "wm_handler_op_context_get_if_valid_legacy(", 1)
    code += TESTS
    with tempfile.TemporaryDirectory(prefix="xr-modal-context-") as temp:
        folder = Path(temp)
        cpp = folder / "modal_context.cc"
        cpp.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", str(cpp),
                        f"/Fe:{folder / 'modal_context.exe'}", f"/Fo:{folder / 'modal_context.obj'}"],
                       check=True)
        subprocess.run([str(folder / "modal_context.exe")], check=True)


if __name__ == "__main__":
    main()
