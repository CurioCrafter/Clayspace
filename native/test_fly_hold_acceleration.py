"""Compile actual native XR hold-acceleration logic; no Blender or headset starts.

Run in a Visual Studio developer shell. This checks movement math and source
lifecycle boundaries, not physical comfort or SteamVR tracking.
"""

from pathlib import Path
import subprocess
import tempfile

from test_d3d_lifetime import body


def main():
    source = (
        Path(__file__).resolve().parent
        / "blender-5.1.2/source/blender/windowmanager/xr/intern/wm_xr_operators.cc"
    ).read_text()
    state = "struct XrFlyHoldState " + body(source, "struct XrFlyHoldState") + ";\n"
    helper = source[
        source.index("static float wm_xr_fly_hold_factor(") : source.index(
            "struct XrFlyData {"
        )
    ]
    modal = body(source, "static wmOperatorStatus wm_xr_navigation_fly_modal(")
    enabled = "if (hold_acceleration) " + body(modal, "if (hold_acceleration)")
    # Existing release/tracking paths destroy the complete per-action hold state.
    assert "wm_xr_fly_uninit(op);" in body(modal, "if (event->val == KM_RELEASE)")
    assert "wm_xr_fly_uninit(op);" in body(modal, "if (!wm_xr_operator_pose_valid(")
    assert modal.index("if (!wm_xr_operator_pose_valid(") < modal.index(
        "wm_xr_fly_hold_factor("
    )
    assert "MEM_new_zeroed<XrFlyData>" in body(source, "static void wm_xr_fly_init(")
    assert "MEM_SAFE_DELETE(data)" in body(source, "static void wm_xr_fly_uninit(")
    # The turn branch precedes and is separate from the hold-acceleration branch.
    assert modal.index("wm_xr_fly_compute_turn(") < modal.index(
        "const float hold_seconds"
    )
    definition = body(source, "static void WM_OT_xr_navigation_fly(")
    assert '"hold_acceleration_seconds",\n                0.0f,' in definition
    code = (
        r"""
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
"""
        + state
        + helper
        + r"""
enum {XR_BOOLEAN_INPUT,XR_FLOAT_INPUT,XR_VECTOR2F_INPUT};
struct Action {int type=XR_FLOAT_INPUT; float state[2]={1.0f,0.0f};};
struct Data {XrFlyHoldState hold{};};
struct Props {float duration=1.5f,start=0.2f,threshold=0.75f;};
struct Operator {Props *ptr;};
float RNA_float_get(Props *p, const char *name) {
  if (!std::strcmp(name,"hold_start_factor")) return p->start;
  assert(!std::strcmp(name,"hold_acceleration_threshold")); return p->threshold;
}
float step(Data *data, Action *actiondata, Props &props, double &delta_time,
           int mode=8, float speed=1.0f, float speed_max=1.0f, bool speed_frame_based=false) {
  Operator storage{&props}; Operator *op=&storage;
  const float hold_seconds=props.duration;
  const bool hold_acceleration=hold_seconds!=0.0f;
"""
        + enabled
        + r"""
  if (!speed_frame_based) speed *= float(delta_time);
  return speed;
}
int main() {
  Props props; Action action; Data data;
  double dt=1.0/90.0;
  float first=step(&data,&action,props,dt)/float(dt);
  assert(first>=0.2f && first<0.201f);
  float previous=first;
  for (int frame=1;frame<360;frame++) {
    dt=1.0/90.0;
    float factor=step(&data,&action,props,dt)/float(dt);
    assert(factor>=previous && factor<=1.0f);
    previous=factor;
  }
  assert(previous==1.0f && std::abs(data.hold.elapsed-1.5)<1e-9);
  // Duration, not frame count, governs the ramp; midpoint is exactly 0.6.
  for (int fps : {30,60,90,120,144}) {
    Data timed;
    for (int frame=0;frame<fps;frame++) {
      dt=0.75/double(fps); step(&timed,&action,props,dt);
    }
    dt=0.0;
    float factor=wm_xr_fly_hold_factor(timed.hold,dt,action.state,8,1.5f,0.2f,0.75f);
    assert(std::abs(factor-0.6f)<1e-5f);
  }
  // Fine stick input never accumulates boost, even after a firm long hold.
  action.state[0]=0.5f;
  for (int frame=0;frame<360;frame++) {
    dt=1.0/90.0;
    assert(std::abs(step(&data,&action,props,dt)/float(dt)-0.2f)<1e-6f);
    assert(data.hold.elapsed==0.0);
  }
  // Reversal (including vector direction) and effective mode changes reset.
  action.state[0]=1.0f; dt=0.01; data.hold.elapsed=1.5;
  step(&data,&action,props,dt);
  action.state[0]=-1.0f; dt=0.01;
  assert(step(&data,&action,props,dt)/float(dt)<0.201f);
  data.hold.elapsed=1.5; dt=0.01;
  assert(step(&data,&action,props,dt,9)/float(dt)<0.201f);
  action.type=XR_VECTOR2F_INPUT; action.state[0]=0.0f; action.state[1]=1.0f;
  dt=0.01; step(&data,&action,props,dt,9); data.hold.elapsed=1.5;
  action.state[1]=-1.0f; dt=0.01;
  assert(step(&data,&action,props,dt,9)/float(dt)<0.201f);
  action=Action{};
  // A stalled, reversed, or invalid clock never produces catch-up travel.
  for (double invalid_dt : {0.251,2.0,-0.1,std::numeric_limits<double>::infinity(),
                            std::numeric_limits<double>::quiet_NaN()}) {
    data.hold.elapsed=1.5; dt=invalid_dt;
    assert(step(&data,&action,props,dt)==0.0f && dt==0.0);
    assert(data.hold.elapsed==0.0);
    dt=0.01;
    assert(step(&data,&action,props,dt)/float(dt)<0.201f);
  }
  // Modest dropped frames have a capped delta and per-action velocity.
  data.hold.elapsed=1.5; dt=0.2;
  assert(step(&data,&action,props,dt,8,100.0f,2.0f)<=0.1f && dt==0.05);
  for (float bad : {std::numeric_limits<float>::infinity(),
                     std::numeric_limits<float>::quiet_NaN()}) {
    action.state[0]=bad; dt=0.01;
    assert(step(&data,&action,props,dt)==0.0f && dt==0.0);
    action=Action{}; dt=0.01;
    assert(step(&data,&action,props,dt,8,bad)==0.0f);
    props.duration=bad; dt=0.01;
    assert(step(&data,&action,props,dt)==0.0f && dt==0.0);
    props=Props{};
  }
  // Disabled preserves ordinary Blender behavior, including frame-based mode.
  props.duration=0.0f; dt=0.5;
  assert(step(&data,&action,props,dt,8,3.0f,4.0f)==1.5f && dt==0.5);
  assert(step(&data,&action,props,dt,8,3.0f,4.0f,true)==3.0f);
  props=Props{}; data={}; dt=0.01;
  assert(step(&data,&action,props,dt,8,1.0f,1.0f,true)<0.003f);
  puts("XR_FLY_HOLD_ACCELERATION_PASS ramp=1 fine=1 reverse=1 stall=1 finite=1 cap=1 defaults_unchanged=1 hardware=0");
}
"""
    )
    with tempfile.TemporaryDirectory(prefix="xr-fly-hold-") as directory:
        folder = Path(directory)
        fixture = folder / "hold.cc"
        fixture.write_text(code)
        executable = folder / "hold.exe"
        subprocess.run(
            [
                "cl",
                "/nologo",
                "/std:c++17",
                "/EHsc",
                "/W4",
                str(fixture),
                f"/Fe:{executable}",
                f"/Fo:{folder / 'hold.obj'}",
            ],
            check=True,
        )
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
