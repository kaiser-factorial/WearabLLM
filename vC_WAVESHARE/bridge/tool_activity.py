"""Privacy-safe public tool activity and private model-context formatting."""

from __future__ import annotations

import json
from typing import Any, Callable

from bridge_contracts import ModelActivity, ModelToolContext, SourceReference, ToolActivity
from model_protocol import item_field


def clip(value: Any, limit: int = 96) -> str:
    text = " ".join(str(value or "").split()).strip()
    return f"{text[:limit]}…" if len(text) > limit else text


def public_tool_error(name: str, error: Any) -> str:
    text = " ".join(str(error or "").split()).strip()
    lowered = text.lower()
    if "supabase" in lowered:
        return "The private data backend rejected the request."
    if "source" in name and ("not found" in lowered or "path" in lowered):
        return "That path is not available in Sphere's published source manifest."
    if "permission" in lowered or "explicit" in lowered or "intent" in lowered:
        return "The current message did not authorize that operation."
    return f"{name} failed." if text else "Tool failed."


def _memory_search(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    del arguments
    memories = result.get("memories", [])
    count = len(memories) if isinstance(memories, list) else 0
    return f"Memory searched — {count} match{'es' if count != 1 else ''}"


def _memory_remember(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    if result.get("confirmation_required"):
        return "Memory needs confirmation"
    if result.get("created") is False:
        return "Memory already present"
    return "Memory updated"


def _memory_confirm(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    del arguments
    return "Memory updated" if result.get("saved") else "Memory not saved"


def _memory_correct(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    del arguments
    if result.get("confirmation_required"):
        return "Memory correction needs confirmation"
    return "Memory updated"


def _memory_forget(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    del result, arguments
    return "Memory forgotten"


def _sphere_status(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    bodies = result.get("bodies", [])
    count = len(bodies) if isinstance(bodies, list) else 0
    return f"Sphere state checked — {count} bod{'y' if count == 1 else 'ies'}"


def _send_to_body(_result: dict[str, Any], arguments: dict[str, Any]) -> str:
    targets = [str(value) for value in arguments.get("target_device_ids", [])]
    return f"Expression queued — {', '.join(targets) or 'no target'}"


def _sensor_list(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    return f"Sensors inspected — {len(result.get('devices', []))} registered device(s)"


def _sensor_read(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    reading = result.get("result") if isinstance(result.get("result"), dict) else None
    if reading:
        return f"Sensors measured — {len(reading.get('readings', []))} confirmed reading(s)"
    return f"Sensor reading requested — {result.get('status', 'queued')}"


def _sensor_loop(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    return (
        f"Sensor loop scheduled — {result.get('count', '?')} readings every "
        f"{result.get('interval_seconds', '?')}s · {clip(result.get('schedule_id'), 64)}"
    )


def _loop_cancel(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    cancelled = result.get("cancelled", 0)
    return f"Loop cancelled — {cancelled} pending action{'s' if cancelled != 1 else ''}"


def _source_list(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    entries = result.get("entries", [])
    count = len(entries) if isinstance(entries, list) else 0
    return f"Source listed — {clip(arguments.get('path') or '/', 72)} ({count} entries)"


def _source_read(result: dict[str, Any], arguments: dict[str, Any]) -> str:
    file = result.get("file") if isinstance(result.get("file"), dict) else {}
    return (
        f"Source read — {clip(file.get('path') or arguments.get('path'), 72)} "
        f"(lines {file.get('start_line', '?')}–{file.get('end_line', '?')})"
    )


def _web_search(result: dict[str, Any], _arguments: dict[str, Any]) -> str:
    count = int(result.get("source_count", 0))
    return f"Web searched — {count} source{'s' if count != 1 else ''}"


SUMMARY_FORMATTERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], str]] = {
    "memory_search": _memory_search,
    "memory_remember": _memory_remember,
    "memory_confirm": _memory_confirm,
    "memory_correct": _memory_correct,
    "memory_forget": _memory_forget,
    "sphere_status": _sphere_status,
    "send_to_body": _send_to_body,
    "sensor_list": _sensor_list,
    "sensor_read": _sensor_read,
    "sensor_loop": _sensor_loop,
    "loop_cancel": _loop_cancel,
    "source_list": _source_list,
    "source_read": _source_read,
    "web_search": _web_search,
}


def tool_activity_summary(
    name: str,
    result: dict[str, Any],
    arguments: dict[str, Any],
) -> str:
    if not result.get("ok"):
        return public_tool_error(name, result.get("error"))
    formatter = SUMMARY_FORMATTERS.get(name)
    return formatter(result, arguments) if formatter else f"Tool used — {name}"


def public_tool_activity(
    name: str,
    result: dict[str, Any],
    arguments: dict[str, Any] | None = None,
) -> ToolActivity:
    arguments = arguments or {}
    ok = bool(result.get("ok"))
    details: dict[str, Any] = {}
    if "created" in result:
        details["created"] = bool(result.get("created"))
    if "saved" in result:
        details["saved"] = bool(result.get("saved"))
    if result.get("confirmation_required"):
        details["confirmation_required"] = True
        details["sensitive_categories"] = list(result.get("sensitive_categories", []))
    memory = result.get("memory")
    if isinstance(memory, dict) and memory.get("id"):
        details["memory_id"] = str(memory["id"])
    memories = result.get("memories")
    if isinstance(memories, list):
        details["match_count"] = len(memories)
    actions = result.get("actions")
    if isinstance(actions, list):
        details["action_ids"] = [
            str(item.get("action", {}).get("id"))
            for item in actions
            if isinstance(item, dict)
            and isinstance(item.get("action"), dict)
            and item["action"].get("id")
        ]
    bodies = result.get("bodies")
    if name == "sphere_status" and isinstance(bodies, list):
        details["body_count"] = len(bodies)
    if not ok and result.get("error"):
        details["error"] = public_tool_error(name, result.get("error"))
    return ToolActivity(
        name=name,
        ok=ok,
        summary=tool_activity_summary(name, result, arguments),
        details=details,
    )


def model_tool_context(
    name: str,
    result: dict[str, Any],
    arguments: dict[str, Any],
) -> ModelToolContext:
    return ModelToolContext(
        name=name[:80],
        arguments=json.dumps(arguments, ensure_ascii=False, default=str)[:4_000],
        output=json.dumps(result, ensure_ascii=False, default=str)[:32_000],
    )


def collect_response_sources(response: Any, destination: list[SourceReference]) -> None:
    seen = {source.url for source in destination}
    for item in getattr(response, "output", []) or []:
        if item_field(item, "type") == "web_search_call":
            action = item_field(item, "action")
            for source in item_field(action, "sources") or []:
                url = str(item_field(source, "url") or "").strip()
                if url and url not in seen:
                    destination.append(
                        SourceReference(
                            url=url,
                            title=str(item_field(source, "title") or url),
                        )
                    )
                    seen.add(url)
        for content in item_field(item, "content") or []:
            for annotation in item_field(content, "annotations") or []:
                url = str(item_field(annotation, "url") or "").strip()
                if url and url not in seen:
                    destination.append(
                        SourceReference(
                            url=url,
                            title=str(item_field(annotation, "title") or url),
                        )
                    )
                    seen.add(url)


def record_web_search_activity(response: Any, activity: ModelActivity) -> None:
    calls = sum(
        1
        for item in getattr(response, "output", []) or []
        if item_field(item, "type") == "web_search_call"
    )
    for _ in range(calls):
        activity.tool_results.append(
            public_tool_activity(
                "web_search",
                {"ok": True, "source_count": len(activity.sources)},
                {},
            )
        )
        activity.model_tool_context.append(
            model_tool_context(
                "web_search",
                {
                    "ok": True,
                    "sources": [source.to_legacy_dict() for source in activity.sources],
                },
                {},
            )
        )
