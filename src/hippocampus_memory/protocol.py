"""Host-neutral lifecycle protocol for the local memory engine."""

from __future__ import annotations

import json
from typing import Any

from .cognition import CognitiveCoordinator
from .models import contains_secret


PROTOCOL_VERSION = 1
SUPPORTED_EVENTS = frozenset({
    "task.context_requested",
    "task.event",
    "task.finished",
    "memory.feedback",
    "memory.consolidate",
    "memory.maintain",
    "memory.metrics",
})
SUMMARY_FIELDS = (
    "tasks_completed",
    "key_decisions",
    "discoveries",
    "user_feedback",
    "outcomes",
    "lessons_learned",
)


def dispatch_event(engine: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one bounded lifecycle event without persisting host transcripts."""
    _validate_envelope(envelope)
    event_name = envelope["event"]
    payload = envelope.get("payload", {})
    session_id = _identifier(envelope.get("session_id"), "session")
    project_id = _identifier(envelope.get("project_id"), "global")
    cwd = str(envelope.get("cwd") or "")
    cognition = CognitiveCoordinator(engine)

    if event_name == "task.context_requested":
        task = _bounded_text(payload.get("task"), "payload.task", 2_000)
        result = cognition.prepare_task(task, cwd)
    elif event_name == "task.event":
        item = _working_event(payload.get("event"))
        goal = _bounded_text(payload.get("goal", ""), "payload.goal", 500, allow_empty=True)
        result = engine.record_event(item, session_id, project_id, goal)
    elif event_name == "task.finished":
        result = engine.finish_summary(_finish_summary(payload.get("summary", {}), session_id, project_id))
    elif event_name == "memory.feedback":
        candidate_id = _identifier(payload.get("candidate_id"), "candidate")
        outcome = _bounded_text(payload.get("outcome"), "payload.outcome", 32)
        result = cognition.reinforce_candidate(candidate_id, outcome)
    elif event_name == "memory.consolidate":
        result = cognition.consolidate()
    elif event_name == "memory.maintain":
        result = cognition.maintain()
    else:
        result = cognition.metrics()
    return {"protocol_version": PROTOCOL_VERSION, "event": event_name, "result": result}


def _validate_envelope(envelope: object) -> None:
    if not isinstance(envelope, dict):
        raise ValueError("event envelope must be a JSON object")
    try:
        encoded = json.dumps(envelope, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("event envelope must be JSON serializable") from exc
    if len(encoded) > 64_000:
        raise ValueError("event envelope exceeds the 64 KB local safety limit")
    if envelope.get("version") != PROTOCOL_VERSION:
        raise ValueError(f"protocol version must be {PROTOCOL_VERSION}")
    if envelope.get("event") not in SUPPORTED_EVENTS:
        raise ValueError("unsupported lifecycle event")
    if "payload" in envelope and not isinstance(envelope["payload"], dict):
        raise ValueError("payload must be a JSON object")


def _identifier(value: object, fallback: str) -> str:
    text = str(value or fallback).strip()
    if not text or len(text) > 200:
        raise ValueError("session_id, project_id, and candidate_id must be 1–200 characters")
    return text


def _bounded_text(value: object, field: str, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if (not text and not allow_empty) or len(text) > limit:
        raise ValueError(f"{field} must contain at most {limit} characters")
    if contains_secret(text):
        raise ValueError(f"{field} appears to contain a credential and was not stored")
    return text


def _working_event(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("payload.event must be a JSON object")
    kind = _bounded_text(value.get("kind"), "payload.event.kind", 32)
    content = _bounded_text(value.get("content"), "payload.event.content", 1_200)
    try:
        importance = float(value.get("importance", 0.5))
    except (TypeError, ValueError) as exc:
        raise ValueError("payload.event.importance must be numeric") from exc
    if not 0 <= importance <= 1:
        raise ValueError("payload.event.importance must be between 0 and 1")
    return {"kind": kind, "content": content, "importance": importance}


def _finish_summary(value: object, session_id: str, project_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("payload.summary must be a JSON object")
    summary: dict[str, Any] = {"sessionId": session_id, "project": project_id, "candidates": []}
    for field in SUMMARY_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list) or len(items) > 5:
            raise ValueError(f"payload.summary.{field} must contain at most five items")
        summary[field] = [_bounded_text(item, f"payload.summary.{field}", 500) for item in items]
    candidates = value.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > 5:
        raise ValueError("payload.summary.candidates must contain at most five items")
    summary["candidates"] = [_candidate(item, session_id, project_id) for item in candidates]
    return summary


def _candidate(value: object, session_id: str, project_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("each candidate must be a JSON object")
    candidate_type = _bounded_text(value.get("type", "lesson"), "candidate.type", 32)
    source = _bounded_text(value.get("source", "agent_observed"), "candidate.source", 32)
    if source not in {"agent_observed", "external_untrusted"}:
        raise ValueError("candidate.source must be agent_observed or external_untrusted; use memory.feedback for user confirmation")
    candidate = {
        "type": candidate_type,
        "summary": _bounded_text(value.get("summary"), "candidate.summary", 500),
        "context": _bounded_text(value.get("context"), "candidate.context", 1_200),
        "source": source,
        "source_session": session_id,
        "project": project_id,
    }
    if "subject" in value:
        candidate["subject"] = _bounded_text(value["subject"], "candidate.subject", 120)
    if "tags" in value:
        if not isinstance(value["tags"], list) or len(value["tags"]) > 8:
            raise ValueError("candidate.tags must contain at most eight items")
        candidate["tags"] = [_bounded_text(item, "candidate.tags", 40) for item in value["tags"]]
    return candidate
