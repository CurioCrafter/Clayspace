from __future__ import annotations

import math
from pathlib import Path
import importlib.util
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "vrw_model_test", ROOT / "vr_blender_workbench" / "model.py"
)
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)

CATEGORY_ORDER = model.CATEGORY_ORDER
DEFAULT_PALETTE_LAYOUT = model.DEFAULT_PALETTE_LAYOUT
DEFAULT_FAVORITES = model.DEFAULT_FAVORITES
TOOLS = model.TOOLS
cycle_category = model.cycle_category
favorite_needs_outer_undo = model.favorite_needs_outer_undo
operator_id_is_valid = model.operator_id_is_valid
palette_cell_from_local_point = model.palette_cell_from_local_point
palette_entries = model.palette_entries
quantize = model.quantize
ray_plane_intersection = model.ray_plane_intersection
snapped_angle_radians = model.snapped_angle_radians
tool_by_id = model.tool_by_id
tools_for_category = model.tools_for_category
uniform_scale_from_distances = model.uniform_scale_from_distances
xr_route_matches = model.xr_route_matches


class ToolRegistryTests(unittest.TestCase):
    def test_identifiers_are_unique_and_categories_known(self):
        identifiers = [tool.identifier for tool in TOOLS]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(identifiers)
        self.assertTrue(all(tool.category in CATEGORY_ORDER for tool in TOOLS))

    def test_every_category_fits_one_palette_page(self):
        for category in CATEGORY_ORDER:
            tools = tools_for_category(category)
            self.assertGreater(len(tools), 0)
            self.assertLessEqual(len(tools), 9)
            entries = palette_entries(category)
            self.assertEqual(len(entries), 12)
            self.assertEqual(entries[-3:], ("__PREVIOUS__", "__CLOSE__", "__NEXT__"))

    def test_category_cycle_wraps(self):
        self.assertEqual(cycle_category(CATEGORY_ORDER[-1], 1), CATEGORY_ORDER[0])
        self.assertEqual(cycle_category(CATEGORY_ORDER[0], -1), CATEGORY_ORDER[-1])

    def test_tool_lookup(self):
        self.assertEqual(tool_by_id("MOVE").label, "Move")
        with self.assertRaises(KeyError):
            tool_by_id("NOT_A_TOOL")

    def test_favorite_undo_metadata_and_history_override(self):
        self.assertTrue(DEFAULT_FAVORITES[0].creates_undoable_data)
        self.assertFalse(DEFAULT_FAVORITES[4].creates_undoable_data)
        self.assertFalse(DEFAULT_FAVORITES[5].creates_undoable_data)
        self.assertFalse(
            favorite_needs_outer_undo(
                DEFAULT_FAVORITES[4].operator_id,
                DEFAULT_FAVORITES[4].creates_undoable_data,
            )
        )
        self.assertFalse(
            favorite_needs_outer_undo(
                DEFAULT_FAVORITES[5].operator_id,
                DEFAULT_FAVORITES[5].creates_undoable_data,
            )
        )
        self.assertFalse(favorite_needs_outer_undo("ed.undo", True))
        self.assertFalse(favorite_needs_outer_undo("ed.redo", True))
        self.assertTrue(favorite_needs_outer_undo("mesh.subdivide", True))


class PaletteGeometryTests(unittest.TestCase):
    def test_ray_plane_hit_and_rejections(self):
        self.assertEqual(
            ray_plane_intersection((0, 1, 0), (0, -1, 0), (0, 0, 0), (0, 1, 0)),
            (0.0, 0.0, 0.0),
        )
        self.assertIsNone(
            ray_plane_intersection((0, 1, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0))
        )
        self.assertIsNone(
            ray_plane_intersection((0, -1, 0), (0, -1, 0), (0, 0, 0), (0, 1, 0))
        )

    def test_palette_cell_mapping(self):
        layout = DEFAULT_PALETTE_LAYOUT
        self.assertEqual(palette_cell_from_local_point((-0.35, 0.0, 0.23)), 0)
        self.assertEqual(palette_cell_from_local_point((0.35, 0.0, 0.23)), 2)
        self.assertEqual(palette_cell_from_local_point((-0.35, 0.0, -0.23)), 9)
        self.assertEqual(palette_cell_from_local_point((0.35, 0.0, -0.23)), 11)
        self.assertIsNone(palette_cell_from_local_point((layout.width, 0.0, 0.0)))


class NumericTests(unittest.TestCase):
    def test_quantization(self):
        self.assertAlmostEqual(quantize(0.26, 0.1), 0.3)
        self.assertAlmostEqual(quantize(-0.26, 0.1), -0.3)
        self.assertAlmostEqual(
            snapped_angle_radians(math.radians(22), 15), math.radians(15)
        )

    def test_scale_guard_and_clamp(self):
        self.assertEqual(uniform_scale_from_distances(0.0, 10.0), 1.0)
        self.assertEqual(uniform_scale_from_distances(1.0, 2.0), 2.0)
        self.assertEqual(uniform_scale_from_distances(1.0, 1000.0), 100.0)

    def test_operator_id_validation(self):
        self.assertTrue(operator_id_is_valid("object.shade_flat"))
        self.assertFalse(operator_id_is_valid("bpy.ops.object.delete"))
        self.assertFalse(operator_id_is_valid("wm.save-mainfile"))

    def test_xr_modal_route_matching(self):
        self.assertTrue(
            xr_route_matches(
                "vr_workbench",
                "primary",
                "/user/hand/right",
                "vr_workbench",
                "primary",
                "/user/hand/right",
            )
        )
        self.assertFalse(
            xr_route_matches(
                "vr_workbench",
                "primary",
                "/user/hand/right",
                "other_set",
                "primary",
                "/user/hand/right",
            )
        )
        self.assertFalse(
            xr_route_matches(
                "vr_workbench",
                "direct_grab",
                "/user/hand/right",
                "vr_workbench",
                "primary",
                "/user/hand/right",
            )
        )
        self.assertFalse(
            xr_route_matches(
                "vr_workbench",
                "primary",
                "/user/hand/right",
                "vr_workbench",
                "primary",
                "/user/hand/left",
            )
        )


class ManifestTests(unittest.TestCase):
    def test_manifest_targets_blender_45(self):
        manifest_path = ROOT / "vr_blender_workbench" / "blender_manifest.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "vr_blender_workbench")
        self.assertEqual(manifest["version"], "0.1.1")
        self.assertEqual(manifest["blender_version_min"], "4.5.0")
        self.assertEqual(manifest["blender_version_max"], "4.6.0")


class LifecycleSourceTests(unittest.TestCase):
    def test_runtime_has_no_context_polling_app_timer(self):
        runtime_source = (ROOT / "vr_blender_workbench" / "runtime.py").read_text(
            encoding="utf-8"
        )
        init_source = (ROOT / "vr_blender_workbench" / "__init__.py").read_text(
            encoding="utf-8"
        )
        bootstrap_source = (ROOT / "scripts" / "launch_bootstrap.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("bpy.app.timers", runtime_source)
        self.assertNotIn("bpy.app.timers", init_source)
        self.assertNotIn("bpy.app.timers", bootstrap_source)
        self.assertNotIn("_runtime_tick", runtime_source)
        draw_source = runtime_source.split("def _draw_callback():", 1)[1].split(
            "def _add_draw_handler():", 1
        )[0]
        self.assertNotIn("bpy.context", draw_source)


if __name__ == "__main__":
    unittest.main()
