"""Compile actual stereo anchor code with orthonormal matrix fixtures."""
from pathlib import Path
import subprocess
import tempfile
from test_d3d_lifetime import body


def main():
    source = (Path(__file__).resolve().parent / "blender-5.1.2/source/blender/windowmanager/xr/intern/wm_xr_task.cc").read_text()
    code = r'''
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
struct GHOST_XrPose { float position[3]={}; float orientation_quat[4]={1,0,0,0}; };
struct wmXrRuntimeData { GHOST_XrPose task_eye_pose[2],task_view_pose; bool task_view_ready=false,task_first_eye_ready=false; };
bool invert_m4_m4(float out[4][4],const float in[4][4]) {
 if(in[3][3]==0) return false;
 memset(out,0,64); out[3][3]=1;
 for(int c=0;c<3;c++)for(int r=0;r<3;r++)out[c][r]=in[r][c];
 for(int r=0;r<3;r++)for(int k=0;k<3;k++)out[3][r]-=out[k][r]*in[3][k];
 return true;
}
void mat4_to_loc_quat(float *p,float *q,const float m[4][4]) {
 memcpy(p,m[3],12); q[0]=1; q[1]=q[2]=q[3]=0; // Fixtures have identity eye rotation.
}
void mid_v3_v3v3(float *out,const float *a,const float *b) { for(int i=0;i<3;i++)out[i]=(a[i]+b[i])*.5f; }
void interp_qt_qtqt(float *out,const float *a,const float *b,float t) { for(int i=0;i<4;i++)out[i]=a[i]*(1-t)+b[i]*t; }
'''
    signature = "void wm_xr_task_view_update(wmXrRuntimeData *runtime, const float viewmat[4][4], int view_index)"
    code += signature + body(source, "void wm_xr_task_view_update(")
    code += r'''
int main() {
 wmXrRuntimeData rt;
 float left[4][4]={{1,0,0,0},{0,1,0,0},{0,0,1,0},{.03f,0,0,1}};
 float right[4][4]; memcpy(right,left,64); right[3][0]=-.03f;
 wm_xr_task_view_update(&rt,right,1); assert(!rt.task_view_ready);
 wm_xr_task_view_update(&rt,left,0); assert(!rt.task_view_ready);
 wm_xr_task_view_update(&rt,right,1); assert(rt.task_view_ready);
 assert(rt.task_view_pose.position[0]==0 && rt.task_view_pose.orientation_quat[0]==1);
 // Head VIEW-space pose intentionally isn't an input: an inconsistent 180deg
 // head roll cannot reverse the rendered eye basis or button labels.
 left[0][0]=std::numeric_limits<float>::quiet_NaN();
 wm_xr_task_view_update(&rt,left,0); wm_xr_task_view_update(&rt,right,1);
 assert(!rt.task_view_ready); // Never reuse a stale first eye after invalid data.
 puts("XR_TASK_VIEW_PASS stereo_center=1 rendered_basis=1 no_stale_eye=1 finite_guard=1");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-task-view-") as directory:
        build = Path(directory)
        test = build / "test.cc"
        test.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(test),
                        "/Fe:"+str(build / "test.exe")], cwd=build, check=True)
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
