"""Factory-startup software check for optical aim rotation preview and rollback."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xr_workbench import editing, hands, interaction


def observation(direction):
    return hands.HandObservation(
        hand="RIGHT", generation=1, timestamp=1.0,
        thumb_tip=direction, index_proximal=(0.0, 0.0, 0.0), index_tip=direction,
        viewer_scale=1.0, navigation_signature=(),
    )


cube = bpy.context.scene.objects["Cube"]
original = cube.matrix_world.copy()
start = hands._pointing_rotation(observation((0.0, 0.0, -1.0)))
turned = hands._pointing_rotation(observation((1.0, 0.0, -1.0)))
owner = interaction.CaptureOwner(interaction.InputProvenance.OPTICAL, "right", 1)
transaction = editing.begin(
    bpy.context,
    interaction.EditRequest(
        owner, interaction.TransformTool.ROTATE, 0,
        interaction.Pose((0.0, 0.0, 0.0), start),
    ),
)
result = transaction.update(
    bpy.context,
    interaction.InputSample(
        owner, 0, interaction.Pose((0.0, 0.0, 0.0), turned), 1.0,
    ),
)
assert result.applied and cube.matrix_world != original
assert transaction.cancel(bpy.context, owner).decision is interaction.Decision.CANCEL_REQUIRED
assert cube.matrix_world == original and editing.active_transaction() is None
print("XR_TASK_ARRANGE_PASS optical_aim_math=1 rotate_preview=1 rollback=1 physical_verified=0")
