"""Compile actual popup-composition and selective-teardown functions against a tiny fixture.

Checks ownership separation and mutation-safe cancellation, not GPU output or headset input.
Run in a Visual Studio developer shell: python native/test_hand_menu_hooks.py
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
#include <cstdio>
#include <vector>
#define WITH_XR_OPENXR
struct Runtime { bool visible = true; };
struct ARegion { Runtime *runtime; int id; bool owned; };
struct wmTooltipState { ARegion *region; ARegion *region_from; };
struct bScreen { std::vector<ARegion> regionbase; wmTooltipState *tool_tip=nullptr; };
struct wmWindow { bScreen *screen; };
struct wmWindowManager {};
struct bContext { wmWindowManager *wm; wmWindow *win; };
wmWindowManager *CTX_wm_manager(bContext *c) { return c->wm; }
wmWindow *CTX_wm_window(bContext *c) { return c->win; }
bScreen *WM_window_get_active_screen(wmWindow *w) { return w->screen; }
bool WM_xr_hand_menu_region_owned(wmWindowManager *,const ARegion *r) {
  return r && r->owned;
}
std::vector<int> draws;
void wm_draw_region_blend(ARegion *r,int view,bool blend) {
  assert(view==0 && blend); draws.push_back(r->id);
}
enum { WM_HANDLER_TYPE_UI=1, WM_HANDLER_TYPE_OP=2 };
struct wmEventHandler { int type=WM_HANDLER_TYPE_UI; };
struct PopupBlockHandle { ARegion *region; bool cancelled=false; };
using Callback = void(*)();
void ui_popup_handler() {}
void unrelated_handler() {}
template<typename T> struct ListBaseT {
  std::vector<T*> values;
  struct iterator {
    typename std::vector<T*>::iterator value;
    bool operator!=(const iterator &other) const { return value!=other.value; }
    iterator &operator++() { ++value; return *this; }
    T &operator*() const { return **value; }
  };
  iterator begin() { return {values.begin()}; }
  iterator end() { return {values.end()}; }
};
struct wmEventHandler_UI {
  wmEventHandler head;
  Callback handle_fn=ui_popup_handler;
  void (*remove_fn)(bContext*,void*);
  void *user_data;
};
ListBaseT<wmEventHandler> *active_handlers;
PopupBlockHandle *child_to_close=nullptr;
int tooltip_clears=0, cancel_calls=0;
void WM_tooltip_clear(bContext*,wmWindow *w) {
  ++tooltip_clears; w->screen->tool_tip=nullptr;
}
void popup_handlers_remove(ListBaseT<wmEventHandler> *handlers,PopupBlockHandle *popup) {
  auto &v=handlers->values;
  v.erase(std::remove_if(v.begin(),v.end(),[popup](auto *base) {
    return base->type==WM_HANDLER_TYPE_UI &&
      reinterpret_cast<wmEventHandler_UI*>(base)->user_data==popup;
  }),v.end());
}
void ui_popup_handler_remove(bContext*,void *arg) {
  auto *p=static_cast<PopupBlockHandle*>(arg);
  // The implementation must remove the handler before cancellation.
  for(auto *h:active_handlers->values) {
    if(h->type==WM_HANDLER_TYPE_UI)
      assert(reinterpret_cast<wmEventHandler_UI*>(h)->user_data!=p);
  }
  p->cancelled=true; p->region->owned=false; ++cancel_calls;
  if(child_to_close) {
    popup_handlers_remove(active_handlers,child_to_close);
    child_to_close->cancelled=true; child_to_close->region->owned=false;
    child_to_close=nullptr;
  }
}
'''

TESTS = r'''
int main() {
  host_fixture::run();
  assert(!wm_event_is_xr_editor() && !wm_event_is_xr_hand_menu());
  {
    XrEditorEventScope outer;
    xr_editor_event_processing=true;
    xr_hand_menu_event_processing=true;
    {
      XrEditorEventScope inner;
      xr_editor_event_processing=false;
      xr_hand_menu_event_processing=false;
    }
    assert(wm_event_is_xr_editor() && wm_event_is_xr_hand_menu());
  }
  assert(!wm_event_is_xr_editor() && !wm_event_is_xr_hand_menu());
  Runtime shown, hidden; hidden.visible=false;
  bScreen screen{{{&shown,1,false},{&shown,2,true},{&shown,3,true},{&hidden,4,true}}};
  wmWindow win{&screen}; wmWindowManager wm; bContext context{&wm,&win};
  wm_draw_window_menu_regions(&wm,&win,false);
  assert((draws==std::vector<int>{1}));
  draws.clear(); wm_draw_window_menu_regions(&wm,&win,true);
  assert((draws==std::vector<int>{2,3})); // native stacking order, no desktop pixels
  screen.regionbase[1].owned=false;
  draws.clear(); wm_draw_window_menu_regions(&wm,&win,true);
  assert((draws==std::vector<int>{3})); // freed/reused ownership cannot linger
  screen.regionbase[1].owned=true;

  PopupBlockHandle desktop{&screen.regionbase[0]}, root{&screen.regionbase[1]}, child{&screen.regionbase[2]};
  wmEventHandler_UI desktop_h{{},ui_popup_handler,ui_popup_handler_remove,&desktop};
  wmEventHandler_UI root_h{{},ui_popup_handler,ui_popup_handler_remove,&root};
  wmEventHandler_UI child_h{{},ui_popup_handler,ui_popup_handler_remove,&child};
  wmEventHandler op{WM_HANDLER_TYPE_OP};
  ListBaseT<wmEventHandler> handlers{{&op,&root_h.head,&child_h.head,&desktop_h.head}};
  active_handlers=&handlers; child_to_close=&child;
  wmTooltipState hand_tip{nullptr,root.region}; screen.tool_tip=&hand_tip;
  popup_handlers_remove_xr(&context,&handlers);
  assert(root.cancelled && child.cancelled && !desktop.cancelled);
  assert(cancel_calls==1 && tooltip_clears==1);
  assert(handlers.values.size()==2 && handlers.values[0]==&op && handlers.values[1]==&desktop_h.head);
  wmTooltipState desktop_tip{desktop.region,desktop.region}; screen.tool_tip=&desktop_tip;
  popup_handlers_remove_xr(&context,&handlers);
  assert(screen.tool_tip==&desktop_tip && tooltip_clears==1 && !desktop.cancelled);
  puts("XR_HAND_MENU_HOOKS_PASS native_composition=1 desktop_exclusion=1 stacking=1 selective_cancel=1 callback_mutation=1 desktop_tooltip_preserved=1 event_scope=1 hardware=0");
}
'''

HOST_PREAMBLE = r'''
namespace host_fixture {
struct ARegion {};
struct wmWindow {};
template<typename T> struct Vector {
  std::vector<T> values;
  bool contains(T value) const { return std::find(values.begin(),values.end(),value)!=values.end(); }
  bool is_empty() const { return values.empty(); }
  void append(T value) { values.push_back(value); }
  void clear() { values.clear(); }
  void remove_first_occurrence_and_reorder(T value) {
    auto it=std::find(values.begin(),values.end(),value);
    assert(it!=values.end()); *it=values.back(); values.pop_back();
  }
};
struct Surface {
  wmWindow *active_window=nullptr;
  bool visible=false,pointer_visible=false;
  int captured_button=0,drawn_frames=0;
};
struct wmXrHandMenu {
  Surface surface;
  Vector<ARegion*> regions;
  bool opening=false,closing=false,anchor_ready=false,freeing=false;
};
struct Runtime { wmXrHandMenu hand_menu; };
struct wmWindowManager { struct { Runtime *runtime; } xr; };
struct bContext { wmWindowManager *wm; wmWindow *win; ARegion *region=nullptr,*popup=nullptr; };
wmWindowManager *CTX_wm_manager(bContext*c) { return c->wm; }
wmWindow *CTX_wm_window(bContext*c) { return c->win; }
ARegion *CTX_wm_region(bContext*c) { return c->region; }
ARegion *CTX_wm_region_popup(bContext*c) { return c->popup; }
enum { TIMER=1,WINDEACTIVATE=2,EVT_BUT_CANCEL=3,MOUSEMOVE=4,LEFTMOUSE=5,
       WM_EVENT_XR_MENU=1<<5,WM_EVENT_XR_EDITOR=1<<4 };
struct wmEvent { int type,flag; };
template<typename... T> bool elem(int v,T... xs) { return ((v==xs)||...); }
#define ELEM(v,...) elem(v,__VA_ARGS__)
#define ISTIMER(v) ((v)==TIMER)
'''

HOST_TESTS = r'''
void run() {
  Runtime runtime; wmWindowManager wm{{&runtime}}; wmWindow win,other;
  bContext context{&wm,&win}; auto &menu=runtime.hand_menu;
  menu.surface.active_window=&win;
  ARegion root,child,replacement,desktop,tooltip;
  menu.opening=true; WM_xr_hand_menu_region_add(&context,&root); menu.opening=false;
  assert(WM_xr_hand_menu_region_owned(&wm,&root) && menu.surface.visible);
  menu.anchor_ready=true; menu.surface.drawn_frames=12;
  menu.surface.captured_button=LEFTMOUSE; menu.surface.pointer_visible=true;
  {
    XrEditorEventScope scope;
    xr_editor_event_processing=true; xr_hand_menu_event_processing=true;
    WM_xr_hand_menu_region_remove(&context,&root);
    assert(menu.regions.is_empty() && !menu.surface.visible);
    // Actual native root is freed before delayed operator callbacks open its replacement.
    WM_xr_hand_menu_region_add(&context,&replacement);
    assert(WM_xr_hand_menu_region_owned(&wm,&replacement) && menu.surface.visible);
    assert(!menu.anchor_ready && !menu.surface.drawn_frames && !menu.surface.captured_button);
    assert(!menu.surface.pointer_visible);
    WM_xr_hand_menu_region_add(&context,&child);
    assert(WM_xr_hand_menu_region_owned(&wm,&child));
    context.win=&other; WM_xr_hand_menu_region_add(&context,&desktop);
    assert(!WM_xr_hand_menu_region_owned(&wm,&desktop)); context.win=&win;
  }
  {
    XrEditorEventScope scope;
    xr_editor_event_processing=true; xr_hand_menu_event_processing=false;
    WM_xr_hand_menu_region_add(&context,&desktop);
    assert(!WM_xr_hand_menu_region_owned(&wm,&desktop)); // Mirrored editor input is not menu input.
  }
  context.region=&replacement;
  WM_xr_hand_menu_region_add(&context,&tooltip); // Native tooltip timer inherits owner context.
  assert(WM_xr_hand_menu_region_owned(&wm,&tooltip)); context.region=nullptr;
  for (int type:{MOUSEMOVE,LEFTMOUSE}) {
    wmEvent desktop_event{type,0}, editor_event{type,WM_EVENT_XR_EDITOR};
    wmEvent menu_event{type,WM_EVENT_XR_EDITOR|WM_EVENT_XR_MENU};
    assert(!WM_xr_hand_menu_event_allowed(&wm,&replacement,&desktop_event));
    assert(!WM_xr_hand_menu_event_allowed(&wm,&replacement,&editor_event));
    assert(WM_xr_hand_menu_event_allowed(&wm,&replacement,&menu_event));
    assert(!WM_xr_hand_menu_event_allowed(&wm,&desktop,&menu_event));
    assert(WM_xr_hand_menu_event_allowed(&wm,&desktop,&desktop_event));
  }
  for(int type:{TIMER,WINDEACTIVATE,EVT_BUT_CANCEL}) {
    wmEvent event{type,0};
    assert(WM_xr_hand_menu_event_allowed(&wm,&replacement,&event));
  }
  WM_xr_hand_menu_region_remove(&context,&replacement);
  WM_xr_hand_menu_region_remove(&context,&child);
  WM_xr_hand_menu_region_remove(&context,&tooltip);
  assert(menu.regions.is_empty() && !menu.surface.visible);
  // Reusing a freed region address must not retain ownership outside a menu dispatch.
  menu.regions.append(&desktop); WM_xr_hand_menu_region_add(&context,&desktop);
  assert(!WM_xr_hand_menu_region_owned(&wm,&desktop));
  wm.xr.runtime=nullptr; WM_xr_hand_menu_region_add(&context,&root);
  WM_xr_hand_menu_region_remove(&context,&root);
  assert(!WM_xr_hand_menu_region_owned(&wm,&root));
  puts("XR_HAND_MENU_OWNERSHIP_PASS native_bodies=1 deferred_replacement=1 desktop_isolation=1 inherited_tooltip=1 lifecycle=1 address_reuse=1");
}
} // namespace host_fixture
#undef ELEM
#undef ISTIMER
'''


def main():
    draw = (SOURCE / "windowmanager/intern/wm_draw.cc").read_text()
    handlers = (SOURCE / "editors/interface/interface_handlers.cc").read_text()
    events = (SOURCE / "windowmanager/intern/wm_event_system.cc").read_text()
    host = (SOURCE / "windowmanager/xr/intern/wm_xr_editor.cc").read_text()
    regions = (SOURCE / "editors/interface/regions/interface_regions.cc").read_text()
    interface_build = (SOURCE / "editors/interface/CMakeLists.txt").read_text()
    assert "if(WITH_XR_OPENXR)\n  add_definitions(-DWITH_XR_OPENXR)\nendif()" in interface_build
    remove = function(regions, "void region_temp_remove(")
    assert remove.index("WM_xr_hand_menu_region_remove") < remove.index("BKE_area_region_free")
    assert "wm_draw_window_menu_regions(wm, win, false)" in function(draw, "static void wm_draw_window_onscreen(")
    assert "wm_draw_window_menu_regions(wm, win, true)" in function(draw, "void wm_draw_xr_menu_to_offscreen(")
    assert "tevent.flag &= (WM_EVENT_XR_EDITOR | WM_EVENT_XR_MENU)" in events
    assert "event_last->flag &= (WM_EVENT_XR_EDITOR | WM_EVENT_XR_MENU)" in events
    assert "xr_hand_menu_event_processing = (event->flag & WM_EVENT_XR_MENU) != 0" in events
    dispatch = function(events, "static eHandlerActionFlag wm_handlers_do_intern(")
    menu_guard = dispatch.index("(event->flag & WM_EVENT_XR_MENU) && handler_base->type != WM_HANDLER_TYPE_UI")
    assert dispatch.index("handler_base->flag & WM_HANDLER_DO_FREE") < menu_guard
    assert menu_guard < dispatch.index("handler_base->poll == nullptr")
    for signature in ("static int ui_popup_handler(", "static int ui_handler_region_menu("):
        # ui_handler_region_menu also has a forward declaration near the top.
        implementation = handlers[handlers.rfind(signature):]
        assert "WM_xr_hand_menu_event_allowed" in function(implementation, signature)
    code = PREAMBLE + function(draw, "static void wm_draw_window_menu_regions(")
    code += events[events.index("static bool xr_editor_event_processing"):
                   events.index("void wm_event_add_xr_editor(")]
    code += function(events, "struct XrEditorEventScope {") + ";\n"
    code += function(handlers, "void popup_handlers_remove_xr(")
    code += HOST_PREAMBLE
    code += host[host.index("bool WM_xr_hand_menu_region_owned("):
                 host.index("static wmXrEditorPanel editor_full_panel(")]
    code += HOST_TESTS + TESTS
    with tempfile.TemporaryDirectory(prefix="xr-hand-menu-hooks-") as temp:
        folder = Path(temp)
        source = folder / "hooks.cc"
        source.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", str(source),
                        f"/Fe:{folder / 'hooks.exe'}", f"/Fo:{folder / 'hooks.obj'}"], check=True)
        subprocess.run([str(folder / "hooks.exe")], check=True)


if __name__ == "__main__":
    main()
