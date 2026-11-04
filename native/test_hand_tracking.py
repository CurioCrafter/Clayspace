"""Compile the actual GHOST hand methods against a deterministic OpenXR fixture.

No runtime/camera is started. Covers lifecycle, provenance, freshness, failure,
invalid poses and sample generations; does not prove physical optical tracking.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
PREAMBLE = r'''
#include <openxr/openxr.h>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <limits>
#include <vector>
'''
FIXTURE = r'''
struct OpenXRSessionData {
  bool hand_tracking_supported=true,hand_data_source_supported=true;
  XrSession session=(XrSession)1;
  XrSpace reference_space=(XrSpace)2;
  XrSessionState session_state=XR_SESSION_STATE_FOCUSED;
  XrHandTrackerEXT hand_trackers[2]={XR_NULL_HANDLE,XR_NULL_HANDLE};
  PFN_xrCreateHandTrackerEXT create_hand_tracker=nullptr;
  PFN_xrDestroyHandTrackerEXT destroy_hand_tracker=nullptr;
  PFN_xrLocateHandJointsEXT locate_hand_joints=nullptr;
  GHOST_XrHandSample hand_samples[2];
  std::chrono::steady_clock::time_point hand_sample_time;
  int hand_sources[2]={}; XrTime hand_reference_change_time=0;
  bool views_pose_valid=false;
};
struct Context { XrInstance getInstance() { return (XrInstance)1; } };
struct GHOST_XrSession {
  std::unique_ptr<OpenXRSessionData> oxr_=std::make_unique<OpenXRSessionData>();
  Context context; Context *context_=&context;
  void invalidateHandSamples(); void destroyHandTrackers();
  bool setHandTrackingEnabled(bool enabled);
  bool isFocused() const;
  bool viewsValid() const;
  void handleReferenceSpaceChange(XrTime change_time);
  void updateHandSamples(XrTime time);
  void getHandSample(int hand,GHOST_XrHandSample &sample) const;
};
static int creates=0,destroys=0,fail_create=0,source_kind=1,missing_function=0;
static bool fail_locate=false,invalid_pose=false,position_only=false,bad_orientation=false;
static std::vector<XrHandTrackerEXT> live;
static XrResult XRAPI_CALL create_hand(XrSession,const XrHandTrackerCreateInfoEXT *info,XrHandTrackerEXT *out) {
  creates++;
  assert(info->handJointSet==XR_HAND_JOINT_SET_DEFAULT_EXT);
  if(info->next) {
    auto *source=(const XrHandTrackingDataSourceInfoEXT*)info->next;
    assert(source->requestedDataSourceCount==1);
    assert(source->requestedDataSources[0]==XR_HAND_TRACKING_DATA_SOURCE_UNOBSTRUCTED_EXT);
  }
  if(fail_create==creates) return XR_ERROR_RUNTIME_FAILURE;
  *out=(XrHandTrackerEXT)(uintptr_t)(creates+10); live.push_back(*out); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroy_hand(XrHandTrackerEXT tracker) {
  for(auto i=live.begin();i!=live.end();++i) if(*i==tracker) { live.erase(i); destroys++; return XR_SUCCESS; }
  assert(false); return XR_ERROR_HANDLE_INVALID;
}
static XrResult XRAPI_CALL locate_hand(XrHandTrackerEXT,const XrHandJointsLocateInfoEXT *info,XrHandJointLocationsEXT *out) {
  assert(info->baseSpace==(XrSpace)2 && info->time>0);
  if(fail_locate) return XR_ERROR_RUNTIME_FAILURE;
  out->isActive=XR_TRUE;
  if(out->next) {
    auto *source=(XrHandTrackingDataSourceStateEXT*)out->next;
    source->isActive=source_kind?XR_TRUE:XR_FALSE;
    source->dataSource=(XrHandTrackingDataSourceEXT)source_kind;
  }
  for(uint32_t i=0;i<out->jointCount;i++) {
    auto &joint=out->jointLocations[i]; joint.locationFlags=15;
    joint.pose.position={.01f*i,.1f,.2f}; joint.pose.orientation={0,0,0,1}; joint.radius=.005f;
  }
  if(invalid_pose) out->jointLocations[5].pose.position.x=std::numeric_limits<float>::quiet_NaN();
  if(position_only) { out->jointLocations[5].locationFlags=10; out->jointLocations[5].pose.orientation={}; }
  if(bad_orientation) { out->jointLocations[10].pose.orientation={}; }
  return XR_SUCCESS;
}
XrResult XRAPI_CALL xrGetInstanceProcAddr(XrInstance,const char *name,PFN_xrVoidFunction *out) {
  if(missing_function) { *out=nullptr; return XR_ERROR_FUNCTION_UNSUPPORTED; }
  if(!strcmp(name,"xrCreateHandTrackerEXT")) *out=(PFN_xrVoidFunction)create_hand;
  else if(!strcmp(name,"xrDestroyHandTrackerEXT")) *out=(PFN_xrVoidFunction)destroy_hand;
  else if(!strcmp(name,"xrLocateHandJointsEXT")) *out=(PFN_xrVoidFunction)locate_hand;
  else return XR_ERROR_FUNCTION_UNSUPPORTED;
  return XR_SUCCESS;
}
'''
TESTS = r'''
int main() {
  GHOST_XrSession session; GHOST_XrHandSample sample;
  session.getHandSample(0,sample); assert(sample.available && !sample.active && !sample.joints[5].flags);
  session.oxr_->hand_tracking_supported=false; assert(!session.setHandTrackingEnabled(true));
  assert(creates==0); session.oxr_->hand_tracking_supported=true;
  missing_function=1; assert(!session.setHandTrackingEnabled(true) && live.empty()); missing_function=0;
  fail_create=2; assert(!session.setHandTrackingEnabled(true) && live.empty() && destroys==1);
  fail_create=0; assert(session.setHandTrackingEnabled(true) && live.size()==2);
  const int created=creates; assert(session.setHandTrackingEnabled(true) && creates==created);
  session.updateHandSamples(1234567890123LL); session.getHandSample(0,sample);
  assert(sample.active && sample.data_source==1 && sample.joints[5].flags==15);
  assert(sample.timestamp_ns==1234567890123LL && sample.joints[5].orientation[0]==1);
  const auto stable_epoch=sample.generation;
  session.updateHandSamples(1234567890124LL); session.getHandSample(0,sample);
  assert(sample.generation==stable_epoch && sample.timestamp_ns==1234567890124LL);
  position_only=true; bad_orientation=true;
  session.updateHandSamples(1234567890124LL); session.getHandSample(0,sample);
  assert(sample.joints[5].flags==10 && std::abs(sample.joints[5].position[0]-.05f)<1e-6f);
  assert(sample.joints[5].orientation[0]==0 && sample.joints[10].flags==0);
  position_only=bad_orientation=false;
  session.handleReferenceSpaceChange(1234567890126LL);
  session.getHandSample(0,sample); assert(!sample.active && sample.generation>stable_epoch);
  const auto pending_epoch=sample.generation;
  session.updateHandSamples(1234567890125LL); session.getHandSample(0,sample);
  assert(sample.generation==pending_epoch);
  session.updateHandSamples(1234567890126LL); session.getHandSample(0,sample);
  assert(sample.generation>pending_epoch);
  auto generation=sample.generation;
  source_kind=2; session.updateHandSamples(1234567891123LL); session.getHandSample(0,sample);
  assert(!sample.active && sample.data_source==2 && sample.joints[5].flags==0 && sample.generation>generation);
  source_kind=0; session.updateHandSamples(1234567892123LL); session.getHandSample(0,sample);
  assert(!sample.active && sample.data_source==0 && sample.joints[5].flags==0);
  source_kind=1; fail_locate=true; session.updateHandSamples(1234567893123LL); session.getHandSample(0,sample);
  assert(!sample.active && !sample.joints[5].flags); fail_locate=false;
  invalid_pose=true; session.updateHandSamples(1234567894123LL); session.getHandSample(0,sample);
  assert(sample.active && sample.joints[5].flags==0 && sample.joints[10].flags==15); invalid_pose=false;
  session.updateHandSamples(1234567895123LL);
  session.oxr_->hand_sample_time-=std::chrono::seconds(1); session.getHandSample(0,sample);
  assert(!sample.active && sample.joints[5].flags==0);
  session.updateHandSamples(1234567896123LL);
  session.oxr_->session_state=XR_SESSION_STATE_VISIBLE; session.getHandSample(0,sample);
  assert(!sample.active && sample.joints[5].flags==0);
  session.oxr_->session_state=XR_SESSION_STATE_FOCUSED;
  session.updateHandSamples(1234567897123LL); session.getHandSample(0,sample); generation=sample.generation;
  assert(session.setHandTrackingEnabled(false) && live.empty()); session.getHandSample(0,sample);
  assert(!sample.active && sample.joints[5].flags==0 && sample.generation>generation);
  const int destroyed=destroys; session.destroyHandTrackers(); assert(destroys==destroyed);
  generation=sample.generation;
  GHOST_XrSession restarted; assert(restarted.setHandTrackingEnabled(true));
  restarted.updateHandSamples(1234567898123LL); restarted.getHandSample(1,sample);
  assert(sample.active && sample.generation>generation); restarted.destroyHandTrackers();
  GHOST_XrSession no_provenance; no_provenance.oxr_->hand_data_source_supported=false;
  assert(no_provenance.setHandTrackingEnabled(true)); no_provenance.updateHandSamples(1234567899123LL);
  no_provenance.getHandSample(0,sample); assert(sample.available && !sample.active && !sample.joints[5].flags);
  no_provenance.destroyHandTrackers(); assert(live.empty());
  puts("XR_HAND_MOCK_PASS lifecycle=1 provenance=1 freshness=1 failures=1 invalid_pose=1 restart_generation=1");
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--openxr-include", type=Path, required=True)
    args = parser.parse_args()
    header = (ROOT / "blender-5.1.2/intern/ghost/GHOST_Xr-api.hh").read_text()
    types = header[header.index("struct GHOST_XrHandJoint {"):header.index("bool GHOST_XrHandTrackingEnable(")]
    source = (ROOT / "blender-5.1.2/intern/ghost/intern/GHOST_XrSession.cc").read_text()
    methods = source[source.index("static uint32_t hand_sample_generation ="):source.index("void GHOST_XrSession::enablePassthrough()")]
    with tempfile.TemporaryDirectory(prefix="xr-hands-") as directory:
        folder = Path(directory)
        code = folder / "hands.cc"
        code.write_text(PREAMBLE + types + FIXTURE + methods + TESTS)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", f"/I{args.openxr_include}",
                        str(code), f"/Fe:{folder / 'hands.exe'}", f"/Fo:{folder / 'hands.obj'}"], check=True)
        subprocess.run([str(folder / "hands.exe")], check=True)


if __name__ == "__main__":
    main()
