"""Actual WM hand conversion bodies with deterministic matrix fixture operations.

Checks world scale, offsets, translation/rotation, position-only flags and RNA
timestamp guards; not a replacement for Blender's underlying math library tests.
"""
from pathlib import Path
import subprocess
import tempfile
from test_d3d_lifetime import body


def main():
    source = (Path(__file__).resolve().parent / "blender-5.1.2/source/blender/windowmanager/xr/intern/wm_xr_session.cc").read_text()
    code = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
enum { XR_SESSION_USE_POSITION_TRACKING=1, XR_SESSION_USE_ABSOLUTE_TRACKING=2 };
struct GHOST_XrPose { float position[3]={}; float orientation_quat[4]={1,0,0,0}; };
struct GHOST_XrHandJoint { uint64_t flags=0; float position[3]={}; float orientation[4]={}; float radius=0; };
struct GHOST_XrHandSample { bool available=true,active=true; int data_source=1; int64_t timestamp_ns=1; GHOST_XrHandJoint joints[26]; };
struct wmXrSessionState {
 bool is_view_data_set=true; float prev_base_scale=2,viewer_scale=3;
 GHOST_XrPose prev_local_pose,prev_base_pose,nav_pose; float prev_eye_position_ofs[3]={};
};
struct Runtime { wmXrSessionState session_state; void *ghost_context=nullptr; };
struct XrSessionSettings { int flag=3; };
struct wmXrData { Runtime *runtime; XrSessionSettings session_settings; };
static GHOST_XrHandSample mock_sample;
void GHOST_XrHandSampleGet(void*,int,GHOST_XrHandSample *out) { *out=mock_sample; }
void copy_v3_v3(float *a,const float *b) { memcpy(a,b,12); }
void copy_qt_qt(float *a,const float *b) { memcpy(a,b,16); }
void add_v3_v3(float *a,const float *b) { for(int i=0;i<3;i++) a[i]+=b[i]; }
void negate_v3_v3(float *a,const float *b) { for(int i=0;i<3;i++) a[i]=-b[i]; }
void unit_qt(float *a) { a[0]=1;a[1]=a[2]=a[3]=0; }
void unit_m4(float a[4][4]) { memset(a,0,64); for(int i=0;i<4;i++) a[i][i]=1; }
void mul_m4_m4m4(float out[4][4],const float a[4][4],const float b[4][4]) {
 float t[4][4]={}; for(int c=0;c<4;c++) for(int r=0;r<4;r++) for(int k=0;k<4;k++) t[c][r]+=a[k][r]*b[c][k]; memcpy(out,t,64);
}
void wm_xr_pose_to_mat(const GHOST_XrPose *p,float a[4][4]) {
 unit_m4(a); const float w=p->orientation_quat[0],z=p->orientation_quat[3];
 a[0][0]=a[1][1]=1-2*z*z; a[0][1]=2*w*z; a[1][0]=-2*w*z; copy_v3_v3(a[3],p->position);
}
void wm_xr_pose_scale_to_mat(const GHOST_XrPose *p,float scale,float a[4][4]) {
 wm_xr_pose_to_mat(p,a); for(int c=0;c<3;c++)for(int r=0;r<3;r++)a[c][r]*=scale;
}
void mat4_to_loc_quat(float *p,float *q,const float a[4][4]) { copy_v3_v3(p,a[3]); unit_qt(q); }
'''
    for signature in (
        "static bool wm_xr_hand_timestamp_split(int64_t timestamp_ns, int &seconds, int &nanoseconds)",
        "static bool wm_xr_hand_world_transform(const wmXrData *xr, float matrix[4][4], float &scale)",
        "void WM_xr_hand_world_transform(const wmXrData *xr, float r_values[17])",
        "void WM_xr_hand_joint_sample(const wmXrData *xr, int hand, int joint, float r_values[9])",
    ):
        code += signature + body(source, signature) + "\n"
    code += r'''
int main() {
 Runtime rt; wmXrData xr{&rt}; float mapping[17],joint[9];
 mock_sample.joints[5].flags=10; mock_sample.joints[5].position[0]=1; mock_sample.joints[5].radius=.01f;
 rt.session_state.prev_base_pose.position[0]=4;
 rt.session_state.nav_pose.position[1]=5;
 WM_xr_hand_world_transform(&xr,mapping);
 assert(mapping[0]==6 && mapping[13]==12 && mapping[14]==5);
 WM_xr_hand_joint_sample(&xr,0,5,joint);
 assert(joint[0]==10 && joint[1]==18 && joint[2]==5 && std::abs(joint[8]-.06f)<1e-6f);
 for(int i=4;i<8;i++) assert(joint[i]==0); // No invented orientation.
 xr.session_settings.flag=0; rt.session_state.prev_local_pose.position[0]=.25f;
 rt.session_state.prev_eye_position_ofs[0]=.25f;
 WM_xr_hand_joint_sample(&xr,0,5,joint); assert(joint[1]==15);
 xr.session_settings.flag=3;
 rt.session_state.prev_base_pose.orientation_quat[0]=std::sqrt(.5f);
 rt.session_state.prev_base_pose.orientation_quat[3]=std::sqrt(.5f);
 WM_xr_hand_joint_sample(&xr,0,5,joint);
 assert(std::abs(joint[1]-12)<1e-5f && std::abs(joint[2]-11)<1e-5f);
 mock_sample.timestamp_ns=INT64_MAX; WM_xr_hand_joint_sample(&xr,0,5,joint); assert(joint[0]==0);
 mock_sample.timestamp_ns=1; rt.session_state.prev_base_scale=0;
 WM_xr_hand_world_transform(&xr,mapping); for(float f:mapping) assert(f==0);
 WM_xr_hand_joint_sample(&xr,0,5,joint); assert(joint[0]==0);
 puts("XR_HAND_WORLD_MAPPING_PASS scale=6 base_offset=1 rotation=1 flags10=1 orientation_zero=1 timestamp=1");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-world-map-") as directory:
        build = Path(directory)
        test = build / "test.cc"
        test.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(test),
                        "/Fe:"+str(build / "test.exe")], cwd=build, check=True)
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
