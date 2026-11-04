from __future__ import annotations

import importlib.util
from pathlib import Path
import math
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "xr_workbench" / "interaction.py"
SPEC = importlib.util.spec_from_file_location(
    "xrwb_interaction_under_test", MODULE_PATH
)
interaction = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = interaction
SPEC.loader.exec_module(interaction)


class InteractionMathTests(unittest.TestCase):
    def test_constraint_and_snap_are_axis_bounded(self):
        self.assertEqual(
            interaction.snapped_translation(
                (1.24, -0.76, 9.0), interaction.Constraint.XY, 0.5
            ),
            (1.0, -1.0, 0.0),
        )

    def test_uniform_scale_snaps_and_clamps(self):
        options = interaction.TransformOptions(
            scale_snap=0.25,
            min_scale=0.5,
            max_scale=2.0,
        )
        self.assertEqual(interaction.uniform_scale(2.0, 2.6, options), 1.25)
        self.assertEqual(interaction.uniform_scale(2.0, 100.0, options), 2.0)
        self.assertEqual(interaction.uniform_scale(0.0, 10.0, options), 1.0)

    def test_nonfinite_scale_bounds_and_pivot_are_rejected(self):
        with self.assertRaises(ValueError):
            interaction.TransformOptions(max_scale=math.inf)
        with self.assertRaises(ValueError):
            interaction.TransformOptions(pivot_world=(0.0, math.nan, 0.0))

    def test_explicit_uniform_scale_validates_and_clamps(self):
        with self.assertRaises(ValueError):
            interaction.InputSample(
                interaction.CaptureOwner(
                    interaction.InputProvenance.OPTICAL,
                    "two-hand",
                    1,
                ),
                0,
                interaction.Pose((0.0, 0.0, 0.0)),
                1.0,
                uniform_scale=math.nan,
            )
        options = interaction.TransformOptions(min_scale=0.05, max_scale=20.0)
        self.assertEqual(interaction.bounded_uniform_scale(0.001, options), 0.05)
        self.assertEqual(interaction.bounded_uniform_scale(100.0, options), 20.0)


class CaptureStateTests(unittest.TestCase):
    def setUp(self):
        self.owner = interaction.CaptureOwner(
            interaction.InputProvenance.SIMULATOR,
            "deterministic-test-hand",
            4,
        )
        self.request = interaction.EditRequest(
            owner=self.owner,
            tool=interaction.TransformTool.MOVE,
            target_generation=9,
            pose=interaction.Pose((0.0, 0.0, 0.0)),
        )

    def sample(self, **changes):
        values = {
            "owner": self.owner,
            "target_generation": 9,
            "pose": interaction.Pose((1.0, 0.0, 0.0)),
            "timestamp_seconds": 10.0,
        }
        values.update(changes)
        return interaction.InputSample(**values)

    def test_provider_generation_change_requires_cancel(self):
        state = interaction.CaptureState(self.request)
        changed = interaction.CaptureOwner(
            interaction.InputProvenance.SIMULATOR,
            self.owner.identifier,
            5,
        )
        result = state.evaluate(self.sample(owner=changed))
        self.assertIs(result.decision, interaction.Decision.CANCEL_REQUIRED)
        self.assertIs(
            state.can_finish(changed).decision,
            interaction.Decision.CANCEL_REQUIRED,
        )

    def test_different_owner_is_rejected_without_stealing_capture(self):
        state = interaction.CaptureState(self.request)
        different = interaction.CaptureOwner(
            interaction.InputProvenance.CONTROLLER,
            "other-hand",
            4,
        )
        result = state.evaluate(self.sample(owner=different))
        self.assertIs(result.decision, interaction.Decision.REJECTED)
        self.assertIs(state.phase, interaction.CapturePhase.CAPTURED)

    def test_target_generation_change_requires_cancel(self):
        state = interaction.CaptureState(self.request)
        result = state.evaluate(self.sample(target_generation=10))
        self.assertIs(result.decision, interaction.Decision.CANCEL_REQUIRED)

    def test_tracking_loss_never_commits_and_requires_neutral_rearm(self):
        state = interaction.CaptureState(self.request)
        paused = state.evaluate(self.sample(tracking_active=False))
        self.assertIs(paused.decision, interaction.Decision.PAUSED)
        self.assertIs(
            state.can_finish(self.owner).decision,
            interaction.Decision.REJECTED,
        )
        not_neutral = state.evaluate(
            self.sample(timestamp_seconds=10.1, tracking_active=True, neutral=False)
        )
        self.assertIs(not_neutral.decision, interaction.Decision.NEUTRAL_REQUIRED)
        rearmed = state.evaluate(
            self.sample(timestamp_seconds=10.2, tracking_active=True, neutral=True)
        )
        self.assertIs(rearmed.decision, interaction.Decision.REBASE)

    def test_tracking_loss_grace_expiry_requires_cancel(self):
        options = interaction.TransformOptions(tracking_loss_grace_seconds=0.25)
        request = interaction.EditRequest(
            self.owner,
            interaction.TransformTool.MOVE,
            9,
            interaction.Pose((0.0, 0.0, 0.0)),
            options,
        )
        state = interaction.CaptureState(request)
        state.evaluate(self.sample(tracking_active=False, timestamp_seconds=3.0))
        result = state.tick(3.25)
        self.assertIs(result.decision, interaction.Decision.CANCEL_REQUIRED)

    def test_stale_sample_and_tick_cannot_extend_grace(self):
        state = interaction.CaptureState(self.request)
        state.evaluate(self.sample(tracking_active=False, timestamp_seconds=4.0))
        stale_sample = state.evaluate(
            self.sample(tracking_active=True, neutral=True, timestamp_seconds=3.9)
        )
        stale_tick = state.tick(3.8)
        self.assertIs(stale_sample.decision, interaction.Decision.REJECTED)
        self.assertIs(stale_tick.decision, interaction.Decision.REJECTED)
        self.assertIs(state.tick(4.25).decision, interaction.Decision.CANCEL_REQUIRED)

    def test_nonfinite_tick_is_rejected(self):
        state = interaction.CaptureState(self.request)
        self.assertIs(state.tick(math.nan).decision, interaction.Decision.REJECTED)

    def test_simulator_provenance_is_explicit(self):
        self.assertEqual(self.request.owner.provenance.value, "simulator")


class SelectionClickStateTests(unittest.TestCase):
    def setUp(self):
        self.owner = interaction.CaptureOwner(
            interaction.InputProvenance.SIMULATOR,
            "selection-hand",
            7,
        )
        self.request = interaction.SelectionRequest(
            owner=self.owner,
            target_generation=3,
            anchor_pose=interaction.Pose((0.0, 0.0, 0.0)),
            world_units_per_metre=2.0,
            click_slop_metres=0.010,
        )

    def sample(self, position=(0.0, 0.0, 0.0), **changes):
        values = {
            "owner": self.owner,
            "target_generation": 3,
            "pose": interaction.Pose(position),
            "timestamp_seconds": 1.0,
        }
        values.update(changes)
        return interaction.InputSample(**values)

    def test_physical_click_slop_uses_world_units_per_metre(self):
        state = interaction.SelectionClickState(self.request)
        within = state.evaluate(self.sample((0.019, 0.0, 0.0)))
        self.assertIs(within.decision, interaction.Decision.APPLY)
        beyond = state.evaluate(self.sample((0.021, 0.0, 0.0), timestamp_seconds=1.1))
        self.assertIs(beyond.decision, interaction.Decision.CANCEL_REQUIRED)

    def test_loss_and_provider_change_cancel_without_commit(self):
        lost = interaction.SelectionClickState(self.request).evaluate(
            self.sample(tracking_active=False)
        )
        self.assertIs(lost.decision, interaction.Decision.CANCEL_REQUIRED)
        changed_owner = interaction.CaptureOwner(
            interaction.InputProvenance.SIMULATOR,
            self.owner.identifier,
            8,
        )
        changed = interaction.SelectionClickState(self.request).evaluate(
            self.sample(owner=changed_owner)
        )
        self.assertIs(changed.decision, interaction.Decision.CANCEL_REQUIRED)

    def test_other_owner_cannot_commit(self):
        state = interaction.SelectionClickState(self.request)
        other = interaction.CaptureOwner(
            interaction.InputProvenance.OPTICAL,
            "other-hand",
            7,
        )
        self.assertIs(
            state.can_finish(other).decision,
            interaction.Decision.REJECTED,
        )

    def test_invalid_slop_is_rejected(self):
        with self.assertRaises(ValueError):
            interaction.SelectionRequest(
                owner=self.owner,
                target_generation=3,
                anchor_pose=interaction.Pose((0.0, 0.0, 0.0)),
                world_units_per_metre=1.0,
                click_slop_metres=math.nan,
            )


if __name__ == "__main__":
    unittest.main()
