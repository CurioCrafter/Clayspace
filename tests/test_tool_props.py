import ast
import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("xr_tool_props_test", ROOT / "xr_workbench/tool_props.py")
tool_props = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool_props
spec.loader.exec_module(tool_props)

# 0.20 tools (spec section 12 table) by prop name and by their tool ids.
NEW_PROPS = {"snake_hook", "inflate", "crease", "layer", "mask_paint", "mask_extract", "live_cage",
             "subdiv_lab", "grow_seed", "retopo_loom"}
NEW_TOOL_IDS = {"sculpt_snake", "sculpt_inflate", "sculpt_crease", "sculpt_layer", "sculpt_mask", "lab_dial"}


def physical_tools_ids():
    """physical_tools.TOOLS read from the source (the module itself needs bpy)."""
    tree = ast.parse((ROOT / "xr_workbench/physical_tools.py").read_text(encoding="utf8"))
    names = {}

    def value(node):
        if isinstance(node, ast.BinOp):
            return value(node.left) | value(node.right)
        if isinstance(node, ast.Name):
            return names[node.id]
        return set(ast.literal_eval(node))

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"SCULPT_TOOLS", "TOOLS"}:
                names[node.targets[0].id] = value(node.value)
    return names["TOOLS"]


class ToolPropTests(unittest.TestCase):
    def test_every_tool_has_a_prop(self):
        for tool in physical_tools_ids() | NEW_PROPS | NEW_TOOL_IDS:
            with self.subTest(tool=tool):
                self.assertIsNotNone(tool_props.prop_for(tool))
        self.assertIsNone(tool_props.prop_for("select"))  # No prop: the ring cursor only.

    def test_triangle_budgets(self):
        counts = {prop: tool_props.triangle_count(prop) for prop in tool_props.prop_ids()}
        self.assertLessEqual(max(counts.values()), 2000, counts)
        self.assertLessEqual(sum(counts.values()), 40000)

    def test_tips_inside_bounds_and_parts_valid(self):
        for prop in tool_props.prop_ids():
            with self.subTest(prop=prop):
                low, high = (np.array(b) for b in tool_props.bounds(prop))
                tip = np.array(tool_props.tip_offset(prop))
                self.assertTrue(np.all(tip >= low - 1e-3) and np.all(tip <= high + 1e-3), (tip, low, high))
                self.assertLess(float(np.max(high - low)), 0.25)  # Hand-sized.
                data = tool_props._load()[prop]
                self.assertEqual(set(np.unique(data["part"])) - {0, 1, 2}, set())
                self.assertIn(2, data["part"])  # Every prop shows the theme accent.
                self.assertLess(int(data["idx"].max()), len(data["pos"]))

    def test_shading_follows_light_and_accent(self):
        prop = "inflate"
        _pos, lit = tool_props.shade(prop, (0, 1, 0), (0, -1, 0), (0.0, 0.0, 1.0))
        _pos, dark = tool_props.shade(prop, (0, -1, 0), (0, 1, 0), (0.0, 0.0, 1.0))
        top = tool_props._load()[prop]["nrm"][:, 1] > 0.8
        self.assertGreater(lit[top, :3].mean(), dark[top, :3].mean())
        accent = tool_props._load()[prop]["part"] == 2
        self.assertTrue(np.all(lit[accent, 0] == 0.0) and np.all(lit[accent, 1] == 0.0))
        squeezed, _ = tool_props.shade(prop, (0, 1, 0), (0, -1, 0), (0, 0, 1), squeeze=1.0)
        self.assertGreater(float(np.abs(squeezed - _pos).max()), 0.003)  # The bulb squashes.


if __name__ == "__main__":
    unittest.main()
