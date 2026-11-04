"""Action outcomes in the launcher's persistent console log; never pose streams."""
from datetime import datetime, timezone
import json
from time import monotonic
import traceback

_sequence = 0
_last_message = "Ready: choose a tool, then use either trigger."
_last_level = "INFO"
# Newest user-facing outcome for the in-headset toast: (message, level, hand, time).
_toast = None
_QUIET_STAGES = {"ui_event", "begin", "hover", "two_hand", "tracking"}


def last_message():
    return _last_message


def last_level():
    return _last_level


def last_toast():
    return _toast


def clear_toast():
    global _toast
    _toast = None


def emit(stage, message, context=None, *, action="", hand="", tool="", level="INFO", error=None):
    """One flushed record per action transition, not per tracking/render sample."""
    global _sequence, _last_message, _last_level, _toast
    _sequence += 1
    _last_message, _last_level = str(message), level
    if stage not in _QUIET_STAGES:
        _toast = (_last_message, level, str(hand or ""), monotonic())
    record = {"seq": _sequence, "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
              "stage": stage, "level": level, "message": _last_message,
              "action": action, "hand": hand, "tool": tool}
    if context is not None:
        try:
            obj = context.active_object
            record.update(mode=context.mode, area=getattr(context.area, "type", None),
                          region=getattr(context.region, "type", None),
                          active_type=getattr(obj, "type", None),
                          selected=len(context.selected_objects),
                          modifiers=[modifier.type for modifier in obj.modifiers] if obj else [])
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            record["context_available"] = False
    if error is not None:
        record["exception"] = "".join(traceback.format_exception(error))
    print("XR_INTERACTION " + json.dumps(record, ensure_ascii=True), flush=True)
