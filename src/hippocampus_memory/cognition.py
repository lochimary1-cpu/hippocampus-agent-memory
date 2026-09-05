"""Semi-autonomous coordination around the memory engine."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .models import keywords, utc_now
from .storage import load_json, save_json

if TYPE_CHECKING:
    from .engine import MemoryEngine


class CognitiveCoordinator:
    """Task gating, evidence-backed consolidation, and metadata maintenance."""

    def __init__(self, engine: "MemoryEngine"):
        self.engine = engine

    def prepare_task(self, task: str, cwd: str = "") -> dict[str, Any]:
        context = self.engine.retrieve(scope="global", query=task, cwd=cwd)
        return {"task": task, "cues": sorted(keywords(task))[:12], "memory_budget": 800, "context": context["matches"], "context_tokens": context["tokens"], "deep_recall_available": bool(task and not context["matches"])}

    def reinforce_candidate(self, candidate_id: str, outcome: str) -> dict[str, Any]:
        allowed = {"successful", "failed", "irrelevant", "user_confirmed"}
        if outcome not in allowed:
            raise ValueError("outcome must be successful, failed, irrelevant, or user_confirmed")
        data = load_json(self.engine.candidates_path, {"candidates": []})
        for candidate in data.get("candidates", []):
            if candidate.get("id") != candidate_id:
                continue
            candidate["outcomes"] = candidate.get("outcomes", []) + [{"outcome": outcome, "at": utc_now()}]
            candidate["successful_outcomes"] = int(candidate.get("successful_outcomes", 0)) + int(outcome in {"successful", "user_confirmed"})
            candidate["failed_outcomes"] = int(candidate.get("failed_outcomes", 0)) + int(outcome in {"failed", "irrelevant"})
            if outcome == "user_confirmed":
                candidate["source_trust"] = "user_confirmed"
            save_json(self.engine.candidates_path, data)
            return {"updated": True, "id": candidate_id, "outcome": outcome}
        return {"updated": False, "id": candidate_id, "outcome": outcome}

    def detect_conflicts(self, candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        records = candidates if candidates is not None else load_json(self.engine.candidates_path, {"candidates": []}).get("candidates", [])
        groups: dict[str, list[dict[str, Any]]] = {}
        for candidate in records:
            subject = str(candidate.get("subject", "")).strip().lower()
            if subject:
                groups.setdefault(subject, []).append(candidate)
        return [{"subject": subject, "candidate_ids": [item.get("id") for item in group], "status": "disputed"} for subject, group in groups.items() if len({str(item.get("summary", "")).strip().lower() for item in group}) > 1]

    def consolidate(self) -> dict[str, Any]:
        data = load_json(self.engine.candidates_path, {"candidates": []})
        conflicts = self.detect_conflicts(data.get("candidates", []))
        disputed = {candidate_id for conflict in conflicts for candidate_id in conflict["candidate_ids"]}
        for candidate in data.get("candidates", []):
            if candidate.get("id") in disputed:
                candidate["validity"] = {"state": "disputed"}
        evaluated = self.engine.evaluate(data)
        promoted = [self.engine.promote(candidate) for candidate in evaluated["promoted"]]
        retained = []
        for candidate in evaluated["archived"]:
            candidate["_cycle"] = int(candidate.get("_cycle", 0)) + 1
            if candidate["_cycle"] < 3:
                retained.append(candidate)
        save_json(self.engine.candidates_path, {"_comment": "Plastic candidates pending evidence-backed consolidation.", "candidates": retained})
        return {"promoted": len(promoted), "retained": len(retained), "discarded": len(evaluated["discarded"]), "conflicts": conflicts}

    def maintain(self) -> dict[str, Any]:
        index = self.engine._index()
        half_lives = self.engine.config().get("consolidation", {}).get("type_half_lives", {})
        now = datetime.now(timezone.utc)
        retired = []
        for entry in index["entries"]:
            reference = entry.get("last_recalled_at") or entry.get("modified") or entry.get("created")
            try:
                observed = datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                age_days = max(0, (now - observed).days)
            except ValueError:
                age_days = 0
            half_life = float(half_lives.get(entry.get("type"), 90))
            entry["score"] = round(float(entry.get("score", 70)) * math.pow(0.5, age_days / half_life), 1)
            entry["age_days"] = age_days
            if entry["score"] < 30 and age_days > half_life:
                entry["validity"] = {"state": "retired", "reason": "unreferenced_decay", "at": utc_now()}
                retired.append(entry.get("id"))
        save_json(self.engine.index_path, index)
        return {"maintained": len(index["entries"]), "retired": retired}

    def metrics(self) -> dict[str, Any]:
        candidates = load_json(self.engine.candidates_path, {"candidates": []}).get("candidates", [])
        index = self.engine._index()["entries"]
        cue_index = load_json(self.engine.cue_index_path, {"entries": []})
        return {"cue_cards": len(cue_index.get("entries", [])), "plastic_candidates": len(candidates), "reinforced_candidates": sum(int(item.get("successful_outcomes", 0)) >= 2 for item in candidates), "retired_memories": sum(item.get("validity", {}).get("state") == "retired" for item in index), "disputed_memories": sum(item.get("validity", {}).get("state") == "disputed" for item in index)}
