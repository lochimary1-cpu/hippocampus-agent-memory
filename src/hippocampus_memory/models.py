"""Small compatibility helpers for the established Markdown/JSON memory format."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


SECRET_PATTERN = re.compile(
    r"""(?ix)
    (?:
        \bsk-[a-z0-9_-]{8,}
        |-----BEGIN [A-Z ]*PRIVATE KEY-----
        |\b(?:api[_ -]?key|auth[_ -]?token|password|passwd|secret|cookie)\b\s*(?:=|:)\s*[\"']?\S{4,}
        |\bbearer\s+[a-z0-9._~+/-]{8,}
    )
    """
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def keywords(text: str) -> set[str]:
    stop_words = {"the", "and", "for", "this", "that", "with", "from", "have", "was", "are", "but", "not", "you", "all", "can", "has", "had", "its", "been", "when", "will", "would", "could", "should", "also", "just", "like"}
    english = {word for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower()) if word not in stop_words}
    chinese = set()
    for run in re.findall(r"[一-鿿]{2,}", text):
        chinese.add(run)
        # Chinese sentences have no spaces, so exact-run matching is too strict.
        # Two-character cues improve local recall without adding model context.
        chinese.update(run[index:index + 2] for index in range(len(run) - 1))
    return english | chinese


def stable_id(entry: dict[str, Any]) -> str:
    if entry.get("id"):
        return str(entry["id"])
    payload = "|".join((str(entry.get("type", "lesson")), str(entry.get("summary", "")).strip(), str(entry.get("context", "")).strip()))
    return "memory-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    result = dict(entry)
    result["id"] = stable_id(result)
    result.setdefault("type", "lesson")
    result.setdefault("summary", "")
    result.setdefault("context", result["summary"])
    result.setdefault("tags", [])
    result.setdefault("confidence", 0.5)
    result.setdefault("source", "session")
    result.setdefault("created", utc_now())
    result.setdefault("validity", {"state": "active"})
    return result


def contains_secret(value: object) -> bool:
    return bool(SECRET_PATTERN.search(str(value)))


def memory_file_for(entry_type: str) -> str:
    return {
        "preference": "preferences.md",
        "feedback": "preferences.md",
        "pattern": "patterns.md",
        "procedure": "procedures.md",
        "decision": "decisions.md",
        "lesson": "lessons.md",
    }.get(entry_type, "lessons.md")
