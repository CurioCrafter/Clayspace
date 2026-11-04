"""Compile actual XR image attribute setup and Blender's resetting format accessor.

Run in a Visual Studio developer shell. No GPU, cameras, Blender or headset starts.
This checks attribute allocation, not texture visibility in the compositor.
"""
from pathlib import Path
import re
import subprocess
import tempfile

from test_d3d_lifetime import body

ROOT = Path(__file__).resolve().parent / "blender-5.1.2/source/blender"


def main():
    code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>
using uint = unsigned;
namespace gpu { enum class VertAttrType { SFLOAT_32_32, SFLOAT_32_32_32 }; }
struct Attribute { const char *name; gpu::VertAttrType type; };
struct GPUVertFormat { std::vector<Attribute> attributes; };
struct Immediate { GPUVertFormat vertex_format; } storage, *imm = &storage;
static int clear_calls = 0;
void GPU_vertformat_clear(GPUVertFormat *format) {
  format->attributes.clear(); ++clear_calls;
}
uint GPU_vertformat_attr_add(GPUVertFormat *format, const char *name, gpu::VertAttrType type) {
  uint id = unsigned(format->attributes.size());
  format->attributes.push_back({name, type});
  return id;
}
'''
    immediate = (ROOT / "gpu/intern/gpu_immediate.cc").read_text()
    code += "GPUVertFormat *immVertexFormat()" + body(immediate, "GPUVertFormat *immVertexFormat()")
    code += "\nint main() {\n"
    for filename, function in (
        ("wm_xr_editor.cc", "void wm_xr_editor_surface_draw("),
        ("wm_xr_camera_overlay.cc", "void wm_xr_camera_overlay_draw("),
    ):
        source = (ROOT / "windowmanager/xr/intern" / filename).read_text()
        draw = body(source, function)
        setup = re.search(
            r"(?:GPUVertFormat \*\w+\s*=\s*immVertexFormat\(\);\s*)?"
            r"const uint pos\s*=\s*GPU_vertformat_attr_add\([^;]+;\s*"
            r"const uint uv\s*=\s*GPU_vertformat_attr_add\([^;]+;",
            draw,
        )
        assert setup, f"Image attribute setup not found: {filename}"
        code += "{ const int before = clear_calls;\n" + setup.group() + r'''
  assert(clear_calls == before + 1);
  assert(pos == 0 && uv == 1 && pos != uv);
  const auto &attributes = imm->vertex_format.attributes;
  assert(attributes.size() == 2);
  assert(!std::strcmp(attributes[pos].name, "pos"));
  assert(attributes[pos].type == gpu::VertAttrType::SFLOAT_32_32_32);
  assert(!std::strcmp(attributes[uv].name, "texCoord"));
  assert(attributes[uv].type == gpu::VertAttrType::SFLOAT_32_32);
}
'''
    code += 'puts("XR_IMAGE_VERTEX_FORMAT_PASS editor=1 camera=1 distinct_pos_uv=1 actual_reset_accessor=1"); }\n'
    with tempfile.TemporaryDirectory(prefix="xr-image-format-") as directory:
        folder = Path(directory)
        source = folder / "format.cc"
        source.write_text(code)
        executable = folder / "format.exe"
        subprocess.run(["cl", "/nologo", "/std:c++17", "/EHsc", "/W4", str(source),
                        f"/Fe:{executable}", f"/Fo:{folder / 'format.obj'}"], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
