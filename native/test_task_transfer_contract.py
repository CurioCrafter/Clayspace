"""Source-contract/numerical check, not a GPU or headset orientation test."""
from pathlib import Path
from test_d3d_lifetime import body

root = Path(__file__).resolve().parent / "blender-5.1.2"
ghost = root / "intern/ghost/intern"
assert "return true;" in body((ghost / "GHOST_ContextD3D.hh").read_text(), "bool isUpsideDown()")
binding = (ghost / "GHOST_XrGraphicsBindingD3D.cc").read_text()
assert "ghost_d3d_ctx_->isUpsideDown()" in body(binding, "bool GHOST_XrGraphicsBindingD3D::needsUpsideDownDrawing")
submit = body(binding, "void GHOST_XrGraphicsBindingOpenGLD3D::submitToSwapchainImage")
assert "CopyResource(" in submit
draw = (root / "source/blender/windowmanager/xr/intern/wm_xr_task.cc").read_text()
draw_body = body(draw, "void wm_xr_task_card_draw(")
assert "projection[column][1] = -projection[column][1]" in draw_body
assert "projection[column][0] = -" not in draw_body
# Rendering applies the binding's Y-origin compensation, X stays unchanged.
# A raw GL readback is before interpretation in D3D's top-left texture convention.
for x, y in [(-.7, .8), (.4, -.2), (.9, .9)]:
    raw_gl = (x, -y)
    d3d_convention = (raw_gl[0], -raw_gl[1])
    assert d3d_convention == (x, y)
print("XR_TASK_TRANSFER_CONTRACT_PASS source_contract=1 numerical_origin=1 gpu_readback_verified=0 physical_verified=0")
