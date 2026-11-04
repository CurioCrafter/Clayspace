"""Compile the actual reference-space selector against synthetic OpenXR results.

Run in a Visual Studio developer shell. No runtime or headset starts.
"""
from pathlib import Path
import subprocess
import tempfile

from test_d3d_lifetime import body

SOURCE = Path(__file__).resolve().parent / "blender-5.1.2/intern/ghost/intern/GHOST_XrSession.cc"


def main():
    code = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <vector>
using XrResult=int;
using XrReferenceSpaceType=int;
enum { XR_SUCCESS=0, XR_SPACE_BOUNDS_UNAVAILABLE=7, XR_NULL_HANDLE=0,
       XR_REFERENCE_SPACE_TYPE_STAGE=1, XR_REFERENCE_SPACE_TYPE_LOCAL_FLOOR_EXT=2,
       XR_REFERENCE_SPACE_TYPE_LOCAL=3, XR_TYPE_REFERENCE_SPACE_CREATE_INFO=4 };
namespace blender {
template<typename T> struct Span : std::vector<T> {
  using std::vector<T>::vector;
  bool contains(T value) const { return std::find(this->begin(),this->end(),value)!=this->end(); }
};
}
struct OpenXRSessionData { int session=1,reference_space=0; };
struct XrReferenceSpaceCreateInfo {
  int type; XrReferenceSpaceType referenceSpaceType=0;
  struct { struct { float w=0; } orientation; } poseInReferenceSpace;
};
struct XrExtent2Df { float width,height; };
static XrResult runtime_bounds_result=XR_SUCCESS;
static XrExtent2Df bounds{2,2};
static bool write_bounds=true;
static int destroyed=0,creates=0;
int xrCreateReferenceSpace(int,const XrReferenceSpaceCreateInfo *info,int *space) {
  assert(info->poseInReferenceSpace.orientation.w==1);
  assert(*space==XR_NULL_HANDLE);
  *space=info->referenceSpaceType; ++creates; return XR_SUCCESS;
}
int xrGetReferenceSpaceBoundsRect(int,int,XrExtent2Df *extent) {
  assert(extent->width==0 && extent->height==0); // Unavailable may leave output untouched.
  if (write_bounds) *extent=bounds;
  return runtime_bounds_result;
}
int xrDestroySpace(int space) { assert(space==XR_REFERENCE_SPACE_TYPE_STAGE); ++destroyed; return 0; }
#define CHECK_XR(call,...) do { if ((call)<0) throw std::runtime_error("OpenXR failure"); } while(0)
void create_main_reference_space(OpenXRSessionData &oxr,
                                const blender::Span<XrReferenceSpaceType> supported_spaces,
                                const bool isDebugMode)
'''
    code += body(SOURCE.read_text(), "static void create_main_reference_space(")
    code += r'''
void check(blender::Span<int> spaces,int expected,int expected_destroyed) {
  OpenXRSessionData oxr; destroyed=creates=0;
  create_main_reference_space(oxr,spaces,false);
  assert(oxr.reference_space==expected && destroyed==expected_destroyed);
  assert(creates==1+expected_destroyed);
}
int main() {
  check({1,2,3},1,0);
  runtime_bounds_result=XR_SPACE_BOUNDS_UNAVAILABLE; write_bounds=false;
  check({1,2,3},2,1); check({1,3},3,1);
  write_bounds=true; // A positive status still cannot certify otherwise plausible extents.
  check({1,2,3},2,1);
  runtime_bounds_result=XR_SUCCESS;
  for (float bad : {0.0f,-1.0f,std::numeric_limits<float>::quiet_NaN(),std::numeric_limits<float>::infinity()}) {
    bounds={bad,2}; check({1,2,3},2,1);
    bounds={2,bad}; check({1,3},3,1);
  }
  bounds={2,2}; check({2,3},2,0); check({3},3,0);
  runtime_bounds_result=-1;
  bool failed=false;
  try { check({1,2,3},2,1); } catch (const std::runtime_error&) { failed=true; }
  assert(failed);
  puts("XR_REFERENCE_SPACE_PASS stage=1 unavailable=1 malformed=8 floor=1 local=1 hardware=0");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-reference-space-") as directory:
        folder = Path(directory)
        source = folder / "space.cc"
        source.write_text(code)
        executable = folder / "space.exe"
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(source),
                        f"/Fe:{executable}", f"/Fo:{folder / 'space.obj'}"], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
