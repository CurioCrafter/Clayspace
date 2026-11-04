"""Compile Blender's actual region callback and Python context helpers with GIL-aware stubs.

No Blender process or hardware is started. Run in a Visual Studio developer shell:
python native/test_xr_python_draw_context.py
"""
from pathlib import Path
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parent / "blender-5.1.2/source/blender/python/intern"


def function(text, signature):
    start = text.index(signature)
    cursor = text.index("{", start)
    depth, end = 1, cursor + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


PREAMBLE = r'''
#include <cassert>
#include <cstdio>
#include <functional>
#include <vector>
struct bContext { int value; };
struct ARegion {};
struct PyObject {};
using PyGILState_STATE = int;
static int py_call_level=0, gil_depth=0, decrefs=0, errors=0, module_updates=0;
static bool main_thread=true;
static bContext *python_context=nullptr;
static bContext *expected_context_at_release=nullptr;
static bool check_release=false;
static std::function<PyObject*()> callback;
static PyObject tuple, callable, arguments, result;
static std::vector<int> levels_at_clear;
PyGILState_STATE PyGILState_Ensure() { return gil_depth++; }
void PyGILState_Release(PyGILState_STATE state) {
  assert(gil_depth==state+1);
  if(check_release) assert(python_context==expected_context_at_release);
  --gil_depth;
}
bool BLI_thread_is_main() { return main_thread; }
bContext *BPY_context_get() { assert(gil_depth>0); return python_context; }
void BPY_context_set(bContext *C) { assert(gil_depth>0); python_context=C; }
void BPY_modules_update() { assert(gil_depth>0); ++module_updates; }
void pyrna_context_init(bContext *C) { assert(C && gil_depth>0); }
void pyrna_context_clear(bContext *C) { assert(C); levels_at_clear.push_back(py_call_level); }
PyObject *PyTuple_GET_ITEM(PyObject *o,int index) {
  assert(gil_depth>0 && o==&tuple && (index==1 || index==2));
  return index==1 ? &callable : &arguments;
}
PyObject *PyObject_CallObject(PyObject *fn,PyObject *args) {
  assert(gil_depth>0 && fn==&callable && args==&arguments);
  return callback();
}
void Py_DECREF(PyObject *o) { assert(gil_depth>0 && o==&result); ++decrefs; }
void PyErr_Print() { assert(gil_depth>0); ++errors; }
'''

TESTS = r'''
int main() {
  bContext desktop{1}, normal{2}, outer{3};
  // Outermost XR callback sees no editor context, then restores timers' prior context.
  python_context=&desktop; expected_context_at_release=&desktop; check_release=true;
  callback=[&]() -> PyObject* {
    assert(python_context==nullptr && py_call_level==1 && gil_depth==1);
    return &result;
  };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==&desktop && py_call_level==0 && gil_depth==0 && decrefs==1);
  callback=[&]() -> PyObject* {
    assert(python_context==nullptr && py_call_level==1 && gil_depth==1);
    return nullptr;
  };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==&desktop && py_call_level==0 && gil_depth==0 && errors==1);

  // Existing Python scope normally suppresses bpy_context_set updates. XR still needs NULL,
  // but must restore its enclosing scope without clearing that scope's bookkeeping.
  py_call_level=1; gil_depth=1; python_context=&outer; expected_context_at_release=&outer;
  callback=[&]() -> PyObject* {
    assert(python_context==nullptr && py_call_level==2 && gil_depth==2);
    return &result;
  };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==&outer && py_call_level==1 && gil_depth==1);
  assert(levels_at_clear.empty());

  // Nested null-context draw calls restore NULL to the outer draw, then its original scope.
  callback=[&]() -> PyObject* {
    assert(python_context==nullptr && py_call_level==2);
    auto outer_callback=callback;
    callback=[&]() -> PyObject* {
      assert(python_context==nullptr && py_call_level==3 && gil_depth==3);
      return nullptr;
    };
    expected_context_at_release=nullptr;
    cb_region_draw(nullptr,nullptr,&tuple);
    expected_context_at_release=&outer;
    assert(python_context==nullptr && py_call_level==2 && gil_depth==2);
    callback=outer_callback;
    return &result;
  };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==&outer && py_call_level==1 && gil_depth==1 && errors==2);

  // Existing non-null callback behavior stays unchanged (context is intentionally retained).
  py_call_level=0; gil_depth=0; python_context=&desktop; expected_context_at_release=&normal;
  callback=[&]() -> PyObject* {
    assert(python_context==&normal && py_call_level==1 && gil_depth==1);
    return &result;
  };
  cb_region_draw(&normal,nullptr,&tuple);
  assert(python_context==&normal && py_call_level==0 && gil_depth==0);
  assert(levels_at_clear==std::vector<int>{0});

  // Existing non-main-thread policy remains untouched: never change the global context there.
  main_thread=false; python_context=&desktop; expected_context_at_release=&desktop;
  int updates=module_updates;
  callback=[&]() -> PyObject* {
    assert(python_context==&desktop && py_call_level==1 && gil_depth==1);
    return &result;
  };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==&desktop && py_call_level==0 && gil_depth==0 && module_updates==updates);
  main_thread=true;

  // A genuinely missing prior context is not replaced with an invented desktop context.
  python_context=nullptr; expected_context_at_release=nullptr;
  callback=[&]() -> PyObject* { assert(python_context==nullptr); return &result; };
  cb_region_draw(nullptr,nullptr,&tuple);
  assert(python_context==nullptr && py_call_level==0 && gil_depth==0);
  puts("XR_PYTHON_DRAW_CONTEXT_PASS actual_callback=1 actual_context_helpers=1 null_during_draw=1 restore_success=1 restore_error=1 nested_scope=1 gil_order=1 nonnull_unchanged=1 worker_policy=1 hardware=0");
}
'''


def main():
    interface = (SOURCE / "bpy_interface.cc").read_text()
    callback_source = (SOURCE / "bpy_rna_callback.cc").read_text()
    code = PREAMBLE
    for signature in ("void BPY_context_update(", "void bpy_context_set(", "void bpy_context_clear("):
        code += function(interface, signature)
    code += function(callback_source, "static void cb_region_draw(") + TESTS
    with tempfile.TemporaryDirectory(prefix="xr-python-draw-context-") as temp:
        folder = Path(temp)
        source = folder / "context.cc"
        source.write_text(code)
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", str(source),
                        f"/Fe:{folder / 'context.exe'}", f"/Fo:{folder / 'context.obj'}"], check=True)
        subprocess.run([str(folder / "context.exe")], check=True)


if __name__ == "__main__":
    main()
