"""Compile production semantic hit/capture logic, with no runtime or camera."""
from pathlib import Path
import subprocess
import tempfile


def main():
    include = Path(__file__).resolve().parent / "blender-5.1.2/source/blender/windowmanager/xr/intern"
    code = r'''
#include "wm_xr_task_logic.hh"
#include <cassert>
#include <cstdio>
#include <limits>
using namespace blender::xr_task;
int main() {
 State s{}; s.visible=true; s.generation=10;
 int a=-1;
 assert(session_actions[0].id==1 && session_actions[1].id==2);
 assert(default_width==.8f && default_distance==.72f);
 assert((arrange_actions[1].ymax-arrange_actions[1].ymin)*default_width/1000 >= .09f);
 assert((arrange_actions[0].ymax-arrange_actions[0].ymin)*default_width/1000 >= .06f);
 assert(hit(s,200,200)==1 && hit(s,700,200)==2 && hit(s,500,200)==-1);
 assert(hit(s,800,360)==3);
 assert(hit(s,200,360)==-1); // Cancel is visible but inactive without an edit.
 assert(!hit(s,-1,200) && !hit(s,200,501));
 assert(!hit(s,std::numeric_limits<float>::quiet_NaN(),200));
 assert(!route(s,Phase::Move,0,10,1,a)); // No composed tree yet.
 s.drawn_generation=10;
 assert(!route(s,Phase::Press,0,10,1,a)); // Pinched on entry cannot engage.
 assert(!route(s,Phase::Move,0,10,0,a)); // Outside panel does not block a scene ray.
 assert(route(s,Phase::Move,0,10,-1,a) && !s.neutral_mask); // Backing blocks scene, never arms.
 assert(!route(s,Phase::Press,0,10,-1,a) && !s.captured_action);
 assert(route(s,Phase::Move,0,10,1,a));
 assert(!route(s,Phase::Press,0,10,-1,a));
 assert(!route(s,Phase::Press,0,10,1,a)); // A failed press still consumes neutral entry.
 assert(route(s,Phase::Move,0,10,1,a));
 assert(!route(s,Phase::Press,1,10,1,a)); // Neutral is per hand.
 assert(route(s,Phase::Press,0,10,1,a) && a==0);
 assert(!route(s,Phase::Move,1,10,2,a)); // Other hand cannot steal.
 assert(!route(s,Phase::Cancel,1,10,0,a) && s.captured_action==1);
 assert(route(s,Phase::Move,0,10,0,a)); // Capture owns off-card movement.
 assert(route(s,Phase::Release,0,10,2,a) && a==0 && !s.captured_action);
 assert(!route(s,Phase::Release,0,10,1,a)); // No repeat.
 assert(route(s,Phase::Move,2,10,1,a)); // Dedicated image-local card pointer.
 assert(route(s,Phase::Press,2,10,1,a));
 assert(!route(s,Phase::Move,0,10,2,a)); // Optical pointer cannot steal it.
 s.image_cursor_visible=true;
 assert(route(s,Phase::Cancel,2,10,0,a) && !s.image_cursor_visible);
 assert(route(s,Phase::Move,0,10,1,a));
 assert(route(s,Phase::Press,0,10,1,a));
 assert(route(s,Phase::Release,0,10,1,a) && a==1);
 assert(!route(s,Phase::Press,0,10,1,a)); // Must neutral re-arm.
 assert(route(s,Phase::Move,0,10,2,a));
 assert(route(s,Phase::Press,0,10,2,a));
 assert(route(s,Phase::Cancel,0,999,0,a) && a==0 && !s.captured_action);
 assert(route(s,Phase::Move,0,10,2,a));
 assert(route(s,Phase::Press,0,10,2,a));
 assert(!route(s,Phase::Release,0,9,2,a) && a==0 && !s.captured_action);
 assert(hit(s,200,120)==-1 && hit(s,700,120)==-1);
 s.undo_available=true; s.redo_available=true;
 s.cancel_available=true;
 assert(hit(s,200,360)==13);
 assert(route(s,Phase::Move,0,10,13,a));
 assert(route(s,Phase::Press,0,10,13,a));
 assert(route(s,Phase::Release,0,10,13,a) && a==13);
 assert(!route(s,Phase::Press,0,10,13,a)); // A held pinch cannot repeat Cancel.
 s.cancel_available=false; assert(hit(s,200,360)==-1);
 assert(hit(s,200,120)==11 && hit(s,700,120)==12);
 assert(route(s,Phase::Move,0,10,11,a));
 assert(route(s,Phase::Press,0,10,11,a));
 assert(route(s,Phase::Release,0,10,11,a) && a==11);
 s.undo_available=false;
 assert(hit(s,200,120)==-1 && !route(s,Phase::Press,0,10,11,a));
 assert(route(s,Phase::Move,0,10,12,a));
 assert(route(s,Phase::Press,0,10,12,a));
 assert(route(s,Phase::Release,0,10,12,a) && a==12);
 s.arrange=true; s.generation=11; s.drawn_generation=11;
 s.tool_actions_available=true; s.add_available=true;
 assert(hit(s,800,360)==4 && hit(s,200,250)==5 && hit(s,500,250)==6);
 assert(hit(s,200,120)==7 && hit(s,800,250)==8 && hit(s,500,120)==9);
 assert(hit(s,800,120)==-1);
 assert(route(s,Phase::Move,0,11,5,a));
 assert(route(s,Phase::Press,0,11,5,a));
 assert(route(s,Phase::Release,0,11,5,a) && a==5);
 assert(route(s,Phase::Move,0,11,8,a));
 assert(route(s,Phase::Press,0,11,8,a));
 assert(route(s,Phase::Release,0,11,8,a) && a==8);
 assert(route(s,Phase::Move,0,11,9,a));
 assert(route(s,Phase::Press,0,11,9,a));
 assert(route(s,Phase::Release,0,11,9,a) && a==9);
 assert(!route(s,Phase::Press,0,11,10,a)); // More remains disabled.
 assert(!route(s,Phase::Move,0,10,6,a)); // Previous page generation rejected.
 s.tool_actions_available=false;
 assert(hit(s,200,250)==-1 && hit(s,800,360)==4);
 assert(!route(s,Phase::Press,0,11,5,a));
 s.add_available=false; assert(hit(s,500,120)==-1);
 s.visible=false; assert(!route(s,Phase::Move,0,10,1,a));
 puts("XR_TASK_LOGIC_PASS stable_ids=1 shared_bounds=1 generation=1 neutral=1 capture=1 cancel_action=1 arrange=1 rotate=1 add=1 history=1 disabled=1");
}
'''
    with tempfile.TemporaryDirectory(prefix="xr-task-logic-") as directory:
        build = Path(directory)
        source = build / "test.cc"
        source.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", "/I"+str(include),
                        str(source), "/Fe:"+str(build / "test.exe")], cwd=build, check=True)
        subprocess.run([str(build / "test.exe")], check=True)


if __name__ == "__main__":
    main()
