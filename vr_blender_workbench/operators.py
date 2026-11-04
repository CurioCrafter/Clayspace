"""Desktop and XR-dispatched operators for VR Workbench."""

from __future__ import annotations

import time

import bpy
from bpy.app.handlers import persistent

from . import runtime
from .action_map import create_action_set
from .context_bridge import run_operator, view3d_override
from .model import ACTION_MAP_NAME, LEFT_HAND, xr_route_matches
from .tools import (
    begin_continuous_tool,
    execute_press_tool,
    pose_from_event,
    tool_is_continuous,
    tool_needs_outer_undo,
)
from .transactions import cancel_all_transactions, has_active_transactions


_last_action_setup_error = ""


def _set_result_status(context, ok: bool, message: str):
    settings = context.scene.vrw_settings
    if ok:
        settings.last_status = message
        settings.last_error = ""
    else:
        settings.last_error = message
    runtime.push_notice(context, message, is_error=not ok)


def stop_session_for_unregister(context=None) -> bool:
    """Tear down XR before Python operator types used by its actions disappear."""

    context = context or bpy.context
    cancel_all_transactions()
    try:
        runtime.toggle_palette(context, False)
    except (AttributeError, ReferenceError):
        pass
    try:
        state = context.window_manager.xr_session_state
    except (AttributeError, ReferenceError):
        return True
    if state is None:
        return True
    runtime.end_xr_session(context)
    try:
        with view3d_override(context):
            result = bpy.ops.wm.xr_session_toggle("EXEC_DEFAULT")
        ok = "CANCELLED" not in result
        return ok
    except Exception as exc:
        print(f"VR Workbench: XR teardown before unregister failed: {exc}")
        return False


class VRW_OT_start_session(bpy.types.Operator):
    bl_idname = "vrw.start_session"
    bl_label = "Start VR Workbench"
    bl_description = (
        "Start Blender's OpenXR session with VR Workbench controller actions"
    )

    @classmethod
    def poll(cls, _context):
        return bpy.app.build_options.xr_openxr

    def execute(self, context):
        global _last_action_setup_error
        if runtime.session_exists(context):
            self.report({"INFO"}, "VR Workbench is already starting or running")
            return {"FINISHED"}
        settings = context.window_manager.xr_session_settings
        settings.show_custom_overlays = True
        settings.show_controllers = True
        settings.controller_draw_style = "DARK_RAY"
        settings.use_positional_tracking = True
        _last_action_setup_error = ""
        try:
            with view3d_override(context):
                result = bpy.ops.wm.xr_session_toggle("EXEC_DEFAULT")
        except Exception as exc:
            runtime.end_xr_session(context)
            message = f"Could not start OpenXR: {exc}"
            _set_result_status(context, False, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if "CANCELLED" in result:
            runtime.end_xr_session(context)
            message = "OpenXR session did not start. Check that the headset and controllers are powered on in SteamVR."
            _set_result_status(context, False, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        try:
            runtime.prepare_xr_session(context)
        except Exception as exc:
            _last_action_setup_error = f"XR overlay setup failed: {exc}"
        if _last_action_setup_error:
            message = (
                "OpenXR inspection started, but VR Workbench controls failed: "
                f"{_last_action_setup_error}. Stop VR before retrying."
            )
            _set_result_status(context, False, message)
            self.report({"ERROR"}, message)
            # The XR toggle already changed state, so report a finished operator
            # while preserving the actionable failure in the panel/headset.
            return {"FINISHED"}
        _set_result_status(
            context, True, "Starting VR Workbench through SteamVR/OpenXR"
        )
        return {"FINISHED"}


class VRW_OT_stop_session(bpy.types.Operator):
    bl_idname = "vrw.stop_session"
    bl_label = "Stop VR Workbench"
    bl_description = "Stop the current OpenXR session"

    @classmethod
    def poll(cls, context):
        return runtime.session_exists(context)

    def execute(self, context):
        cancel_all_transactions()
        runtime.end_xr_session(context)
        try:
            with view3d_override(context):
                result = bpy.ops.wm.xr_session_toggle("EXEC_DEFAULT")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not stop OpenXR: {exc}")
            return {"CANCELLED"}
        if "CANCELLED" in result:
            return {"CANCELLED"}
        return {"FINISHED"}


class _VRW_ContinuousMixin:
    _transaction = None
    _tool = ""
    _timer = None
    _xr_action_set = ""
    _xr_action = ""
    _xr_user_path = ""
    _last_route_event_time = 0.0
    _input_watchdog_seconds = 2.0
    _blocked_wait = False

    def _capture_xr_route(self, event):
        xr = getattr(event, "xr", None)
        self._xr_action_set = getattr(xr, "action_set", "") if xr else ""
        self._xr_action = getattr(xr, "action", "") if xr else ""
        self._xr_user_path = getattr(xr, "user_path", "") if xr else ""
        self._last_route_event_time = time.monotonic()

    def _matches_xr_route(self, event) -> bool:
        xr = getattr(event, "xr", None)
        if xr is None:
            return False
        return xr_route_matches(
            self._xr_action_set,
            self._xr_action,
            self._xr_user_path,
            getattr(xr, "action_set", ""),
            getattr(xr, "action", ""),
            getattr(xr, "user_path", ""),
        )

    def _input_stream_stalled(self, event) -> bool:
        return (
            event.type == "TIMER"
            and self._last_route_event_time > 0.0
            and time.monotonic() - self._last_route_event_time
            > self._input_watchdog_seconds
        )

    def _remove_timer(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError, ValueError):
                pass
            self._timer = None

    def _hold_blocked_until_release(self, context, event, message: str):
        """Consume a rejected held action once, without repeated invocations."""

        self._capture_xr_route(event)
        self._blocked_wait = True
        _set_result_status(context, False, message)
        self.report({"WARNING"}, message)
        if context.window is not None:
            self._timer = context.window_manager.event_timer_add(
                0.1, window=context.window
            )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _modal_blocked(self, context, event):
        if event.type == "ESC" or not runtime.session_running(context):
            self._remove_timer(context)
            return {"CANCELLED"}
        if self._input_stream_stalled(event):
            self._remove_timer(context)
            return {"CANCELLED"}
        if event.type != "XR_ACTION":
            return {"RUNNING_MODAL"}
        if not self._matches_xr_route(event):
            return {"PASS_THROUGH"}
        self._last_route_event_time = time.monotonic()
        if event.value == "RELEASE":
            self._remove_timer(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _begin(self, context, event, tool: str):
        self._blocked_wait = False
        if has_active_transactions():
            message = "Finish or cancel the active VR gesture first"
            return self._hold_blocked_until_release(context, event, message)
        self._capture_xr_route(event)
        pose = pose_from_event(event, context)
        if pose is None:
            message = "Controller pose is unavailable"
            return self._hold_blocked_until_release(context, event, message)
        self._tool = tool
        self._transaction = begin_continuous_tool(context, tool, pose)
        if self._transaction is None:
            message = f"{tool} has no continuous VR adapter"
            return self._hold_blocked_until_release(context, event, message)
        if not self._transaction.valid:
            message = self._transaction.error
            return self._hold_blocked_until_release(context, event, message)
        if tool.startswith("SCULPT_"):
            try:
                self._transaction.apply(
                    pose.location,
                    pose.direction,
                    context.scene.vrw_settings,
                    pose.pressure,
                )
            except Exception as exc:
                self._transaction.cancel()
                return self._hold_blocked_until_release(
                    context, event, f"{tool.title()} failed: {exc}"
                )
        if context.window is not None:
            self._timer = context.window_manager.event_timer_add(
                0.1, window=context.window
            )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self._blocked_wait:
            return self._modal_blocked(context, event)
        if not self._transaction.valid:
            self._remove_timer(context)
            return {"CANCELLED"}
        if event.type == "ESC" or not runtime.session_running(context):
            self._transaction.cancel()
            self._remove_timer(context)
            _set_result_status(context, False, f"Cancelled {self._tool.title()}")
            return {"CANCELLED"}
        if self._input_stream_stalled(event):
            self._transaction.cancel()
            self._remove_timer(context)
            _set_result_status(
                context, False, "XR input stream stalled; gesture cancelled"
            )
            return {"CANCELLED"}
        if event.type != "XR_ACTION":
            return {"RUNNING_MODAL"}
        xr = getattr(event, "xr", None)
        if (
            xr is not None
            and getattr(xr, "action_set", "") == ACTION_MAP_NAME
            and getattr(xr, "action", "") == "undo"
            and getattr(xr, "user_path", "") == LEFT_HAND
            and event.value == "PRESS"
        ):
            self._transaction.cancel()
            self._remove_timer(context)
            _set_result_status(context, True, f"Cancelled {self._tool.title()}")
            return {"CANCELLED"}
        if not self._matches_xr_route(event):
            return {"PASS_THROUGH"}
        self._last_route_event_time = time.monotonic()
        if event.value == "RELEASE":
            self._transaction.commit()
            self._remove_timer(context)
            _set_result_status(context, True, f"Committed {self._tool.title()}")
            return {"FINISHED"}
        pose = pose_from_event(event, context)
        if pose is None:
            return {"RUNNING_MODAL"}
        try:
            if self._tool.startswith("SCULPT_"):
                self._transaction.apply(
                    pose.location,
                    pose.direction,
                    context.scene.vrw_settings,
                    pose.pressure,
                )
            else:
                self._transaction.apply(
                    pose.location, pose.rotation, context.scene.vrw_settings
                )
        except Exception as exc:
            self._transaction.cancel()
            self._remove_timer(context)
            message = f"{self._tool.title()} failed: {exc}"
            _set_result_status(context, False, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._transaction is not None:
            self._transaction.cancel()
        self._remove_timer(context)
        _set_result_status(context, False, f"Cancelled {self._tool.title()}")


class VRW_OT_primary_action(_VRW_ContinuousMixin, bpy.types.Operator):
    bl_idname = "vrw.primary_action"
    bl_label = "VR Primary Action"
    bl_description = "Execute the active VR tool from the dominant controller"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    _wait_release = False
    _pending_palette = False
    _pending_tool = ""
    _terminal_status_set = False

    def _hold_until_release(self, context, event):
        """Keep a held XR trigger from invoking a one-shot tool repeatedly."""

        self._capture_xr_route(event)
        self._wait_release = True
        if context.window is not None:
            self._timer = context.window_manager.event_timer_add(
                0.1, window=context.window
            )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        self._wait_release = False
        self._pending_palette = False
        self._pending_tool = ""
        self._terminal_status_set = False
        settings = context.scene.vrw_settings
        if settings.palette_visible:
            self._pending_palette = True
            runtime.update_palette_hover_from_event(context, event)
            settings.last_status = "Release the trigger over a palette button"
            return self._hold_until_release(context, event)

        tool = settings.active_tool
        if tool_is_continuous(tool):
            return self._begin(context, event, tool)
        self._pending_tool = tool
        settings.last_status = (
            f"Release the trigger to use {tool.replace('_', ' ').title()}"
        )
        return self._hold_until_release(context, event)

    def modal(self, context, event):
        if not self._wait_release:
            return super().modal(context, event)
        if event.type == "ESC" or not runtime.session_running(context):
            self._remove_timer(context)
            runtime.clear_palette_hover()
            _set_result_status(context, False, "Cancelled pending VR action")
            self._terminal_status_set = True
            return {"CANCELLED"}
        if self._input_stream_stalled(event):
            self._remove_timer(context)
            runtime.clear_palette_hover()
            _set_result_status(
                context, False, "XR input stream stalled; action cancelled"
            )
            self._terminal_status_set = True
            return {"CANCELLED"}
        if event.type == "XR_ACTION":
            xr = getattr(event, "xr", None)
            if (
                xr is not None
                and getattr(xr, "action_set", "") == ACTION_MAP_NAME
                and getattr(xr, "action", "") == "undo"
                and getattr(xr, "user_path", "") == LEFT_HAND
                and event.value == "PRESS"
            ):
                self._remove_timer(context)
                runtime.clear_palette_hover()
                _set_result_status(context, True, "Cancelled pending VR action")
                self._terminal_status_set = True
                return {"CANCELLED"}
            if not self._matches_xr_route(event):
                return {"PASS_THROUGH"}
            self._last_route_event_time = time.monotonic()
            if self._pending_palette:
                runtime.update_palette_hover_from_event(context, event)
        if event.type == "XR_ACTION" and event.value == "RELEASE":
            self._remove_timer(context)
            suppress_outer_undo = self._pending_palette or not tool_needs_outer_undo(
                context, self._pending_tool
            )
            if self._pending_palette:
                entry = runtime.palette_hit_from_event(context, event)
                ok, message = runtime.choose_palette_entry(context, entry)
                runtime.clear_palette_hover()
            elif has_active_transactions():
                ok, message = False, "Finish or cancel the active VR gesture first"
            else:
                pose = pose_from_event(event, context)
                if pose is None:
                    ok, message = False, "Controller pose is unavailable"
                else:
                    ok, message = execute_press_tool(context, self._pending_tool, pose)
            _set_result_status(context, ok, message)
            self._terminal_status_set = True
            if not ok:
                self.report({"WARNING"}, message)
            if suppress_outer_undo:
                # This operator is UNDO-enabled for transforms and data tools.
                # Non-data and history tools end as cancelled so they do not
                # add an empty state or clear the Redo branch.
                return {"CANCELLED"}
            return {"FINISHED"} if ok else {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._wait_release:
            self._remove_timer(context)
            runtime.clear_palette_hover()
            if not self._terminal_status_set:
                _set_result_status(context, False, "Cancelled pending VR action")
            return
        super().cancel(context)


class VRW_OT_direct_grab(_VRW_ContinuousMixin, bpy.types.Operator):
    bl_idname = "vrw.direct_grab"
    bl_label = "VR Direct Grab"
    bl_description = "Move the current selection with the dominant grip"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    def invoke(self, context, event):
        return self._begin(context, event, "MOVE")


class VRW_OT_palette_toggle(bpy.types.Operator):
    bl_idname = "vrw.palette_toggle"
    bl_label = "Toggle VR Palette"
    bl_description = "Open or close the world-space VR tool palette"

    def execute(self, context):
        visible = runtime.toggle_palette(context)
        message = (
            "VR palette opened"
            if visible
            else (context.scene.vrw_settings.last_error or "VR palette closed")
        )
        self.report({"INFO" if visible else "WARNING"}, message)
        return (
            {"FINISHED"}
            if visible or not context.scene.vrw_settings.last_error
            else {"CANCELLED"}
        )


class VRW_OT_cycle_palette(bpy.types.Operator):
    bl_idname = "vrw.cycle_palette"
    bl_label = "Cycle VR Palette"

    direction: bpy.props.IntProperty(default=1, min=-1, max=1)

    def execute(self, context):
        category = runtime.cycle_palette(context, self.direction)
        self.report({"INFO"}, f"Palette: {category.title()}")
        return {"FINISHED"}


class VRW_OT_set_tool(bpy.types.Operator):
    bl_idname = "vrw.set_tool"
    bl_label = "Set VR Tool"
    bl_options = {"INTERNAL"}

    tool: bpy.props.StringProperty()

    def execute(self, context):
        try:
            context.scene.vrw_settings.active_tool = self.tool
        except (TypeError, ValueError):
            self.report({"ERROR"}, f"Unknown VR tool: {self.tool}")
            return {"CANCELLED"}
        context.scene.vrw_settings.last_status = (
            f"Active VR tool: {self.tool.replace('_', ' ').title()}"
        )
        return {"FINISHED"}


class VRW_OT_undo(bpy.types.Operator):
    bl_idname = "vrw.undo"
    bl_label = "VR Undo"

    def execute(self, context):
        if has_active_transactions():
            cancel_all_transactions()
            _set_result_status(context, True, "Cancelled active VR gesture")
            return {"FINISHED"}
        ok, message = run_operator("ed.undo", context=context)
        _set_result_status(context, ok, message)
        return {"FINISHED"} if ok else {"CANCELLED"}


class VRW_OT_redo(bpy.types.Operator):
    bl_idname = "vrw.redo"
    bl_label = "VR Redo"

    def execute(self, context):
        if has_active_transactions():
            message = "Finish or cancel the active VR gesture before Redo"
            _set_result_status(context, False, message)
            return {"CANCELLED"}
        ok, message = run_operator("ed.redo", context=context)
        _set_result_status(context, ok, message)
        return {"FINISHED"} if ok else {"CANCELLED"}


CLASSES = (
    VRW_OT_start_session,
    VRW_OT_stop_session,
    VRW_OT_primary_action,
    VRW_OT_direct_grab,
    VRW_OT_palette_toggle,
    VRW_OT_cycle_palette,
    VRW_OT_set_tool,
    VRW_OT_undo,
    VRW_OT_redo,
)


@persistent
def xr_session_start_pre(_dummy=None):
    global _last_action_setup_error
    context = bpy.context
    try:
        ok, message = create_action_set(context)
        _last_action_setup_error = "" if ok else message
        print(message)
    except Exception as exc:
        message = f"VRW_ACTIONMAP_FAIL {exc}"
        _last_action_setup_error = message
        print(message)
