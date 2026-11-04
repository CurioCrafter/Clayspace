"""Static tripwire for the no-image compositor crash path; no headset required."""

from pathlib import Path


source = (
    Path(__file__).resolve().parents[2]
    / "providers/mercury-research/monado/src/xrt/compositor/main/comp_renderer.c"
).read_text(encoding="utf-8")


def between(start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


ensure = between("renderer_ensure_images_and_renderings(", "renderer_init(")
assert ensure.index("comp_target_create_images(") < ensure.index("if (!comp_target_has_images(target))")
assert ensure.index("if (!comp_target_has_images(target))") < ensure.index("renderer_create_layer_renderer(r)")

acquire = between("renderer_acquire_swapchain_image(", "renderer_resize(")
assert "if (ret != VK_SUCCESS && ret != VK_SUBOPTIMAL_KHR) {\n\t\tCOMP_ERROR" in acquire
assert acquire.index("\t\treturn;\n\t}", acquire.index("if (ret != VK_SUCCESS")) < acquire.index(
    "r->acquired_buffer = buffer_index"
)

draw = between("comp_renderer_draw(", "comp_renderer_allocate_layers(")
acquire_call = draw.index("renderer_acquire_swapchain_image(r);")
assert draw.index("if (r->acquired_buffer < 0) {", acquire_call) < draw.index("dispatch_graphics(r, &rr)")
assert draw.index("comp_target_mark_submit(ct, c->frame.rendering.id") < draw.index("dispatch_graphics(r, &rr)")
assert "c->frame.rendering.id = -1;\n\t\treturn;" in draw

print("MONADO_SURFACE_LOST_GUARDS_PASS")
