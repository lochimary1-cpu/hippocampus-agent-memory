"""Host-independent memory operations backed by the established file format."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import contains_secret, estimate_tokens, keywords, memory_file_for, normalize_entry, stable_id, utc_now
from .storage import default_memory_dir, load_json, memory_content_dir, resolve_project_memory, save_json, system_dir


class MemoryEngine:
    def __init__(self, memory_dir: str | Path | None = None):
        self.memory_dir = Path(memory_dir).expanduser() if memory_dir else default_memory_dir()

    def initialize(self) -> dict[str, Any]:
        """Create a portable memory home without overwriting existing state."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        # A new portable store uses the typed global content directory. Do not
        # create it in an existing flat Markdown store: that would silently
        # change where compatibility reads look for established memories.
        global_dir = self.memory_dir / "global"
        has_flat_markdown = any(self.memory_dir.glob("*.md"))
        if global_dir.exists() or not has_flat_markdown:
            global_dir.mkdir(parents=True, exist_ok=True)
        system_dir(self.memory_dir).mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "sessions" / "archive").mkdir(parents=True, exist_ok=True)
        defaults = {
            self.config_path: {
                "schema_version": 1,
                "scoring": {"thresholds": {"promote": 70, "archive": 50}, "overrides": {"user_explicit_remember": 100, "user_feedback_min": 70}},
                "retrieval": {"task_context_max": 800, "task_context_cards_max": 3},
                "token_budget": {"content_per_load_max": 2000, "global_total_max": 25000},
                "consolidation": {"type_half_lives": {"preference": 180, "decision": 90, "pattern": 120, "lesson": 60, "procedure": 120}},
            },
            self.index_path: {"schema_version": 1, "entries": []},
            self.candidates_path: {"schema_version": 1, "candidates": []},
            self.stats_path: {"schema_version": 1, "sessions": {"total_archived": 0, "sessions_since_consolidation": 0}},
            self.journal_path: {"schema_version": 1, "sessionId": None, "started": None, "project": None, "entries": []},
        }
        created = []
        for path, value in defaults.items():
            if not path.exists():
                save_json(path, value)
                created.append(str(path.relative_to(self.memory_dir)))
        return {"memory_dir": str(self.memory_dir), "created": created}

    @property
    def config_path(self) -> Path:
        return self.memory_dir / ".memory-config.json"

    @property
    def index_path(self) -> Path:
        return system_dir(self.memory_dir) / "index.json"

    @property
    def candidates_path(self) -> Path:
        return system_dir(self.memory_dir) / "candidates.json"

    @property
    def stats_path(self) -> Path:
        return system_dir(self.memory_dir) / "stats.json"

    @property
    def cue_index_path(self) -> Path:
        return system_dir(self.memory_dir) / "cue-index.json"

    @property
    def journal_path(self) -> Path:
        return self.memory_dir / "sessions" / "_current.json"

    def config(self) -> dict[str, Any]:
        return load_json(self.config_path)

    def _scope_root(self, scope: str, cwd: str = "") -> Path | None:
        if scope == "global":
            return self.memory_dir
        if scope == "project":
            return resolve_project_memory(cwd, ignored=(self.memory_dir, default_memory_dir(), Path.home() / ".claude" / "memory"))
        raise ValueError("scope must be global or project")

    def _content_files(self, scope: str = "global", cwd: str = "") -> list[dict[str, Any]]:
        root = self._scope_root(scope, cwd)
        if root is None:
            return []
        content_dir = memory_content_dir(root)
        if not content_dir.exists():
            return []
        files = []
        for path in sorted(content_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            content = path.read_text(encoding="utf-8")
            files.append({"name": path.name, "path": path, "content": content, "tokens": estimate_tokens(content)})
        return files

    def _index(self) -> dict[str, Any]:
        data = load_json(self.index_path, {"entries": []})
        data.setdefault("entries", [])
        return data

    def build_cue_index(self, persist: bool = True) -> dict[str, Any]:
        """Derive small, section-level retrieval cards from human-readable Markdown."""
        entries = []
        source_fingerprints = {}
        quality_by_file = {str(item.get("file", "")).split("/")[-1]: item for item in self._index()["entries"]}
        for memory in self._content_files("global"):
            content = memory["content"]
            source_key = f"global/{memory['name']}"
            source_fingerprints[source_key] = memory["path"].stat().st_mtime_ns
            sections = list(re.finditer(r"^##\s+(.+?)\s*$", content, re.MULTILINE))
            ranges = [(match.group(1).strip(), match.end(), sections[index + 1].start() if index + 1 < len(sections) else len(content)) for index, match in enumerate(sections)]
            if not ranges:
                title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), memory["name"].removesuffix(".md"))
                ranges = [(title, 0, len(content))]
            for heading, start, end in ranges:
                body = content[start:end].strip()
                summary = re.sub(r"\s+", " ", body)[:360]
                if not summary or contains_secret(summary):
                    continue
                identity = f"global/{memory['name']}::{heading}"
                metadata = quality_by_file.get(memory["name"], {})
                entries.append({
                    "id": "card-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                    "file": f"global/{memory['name']}",
                    "heading": heading,
                    "scope": "global",
                    "summary": summary,
                    "keywords": sorted(keywords(memory["name"] + " " + heading + " " + summary)),
                    "tokens": estimate_tokens(body),
                    "quality": float(metadata.get("score", 70)) / 100,
                    "source_modified": source_fingerprints[source_key],
                })
        result = {"schema_version": 1, "generated_at": utc_now(), "source_fingerprints": source_fingerprints, "entries": entries}
        if persist:
            save_json(self.cue_index_path, result)
        return result

    def _cue_cards(self, scope: str, cwd: str) -> list[dict[str, Any]]:
        if scope != "global":
            return []
        if not self.cue_index_path.exists():
            return self.build_cue_index(persist=False)["entries"]
        data = load_json(self.cue_index_path, {"entries": []})
        current = {f"global/{item['name']}": item["path"].stat().st_mtime_ns for item in self._content_files("global")}
        if data.get("source_fingerprints") != current:
            return self.build_cue_index(persist=False)["entries"]
        return data.get("entries", [])

    def retrieve(self, scope: str = "global", query: str = "", cwd: str = "") -> dict[str, Any]:
        config = self.config()
        retrieval = config.get("retrieval", {})
        limit = int(retrieval.get("task_context_max", 800))
        max_cards = int(retrieval.get("task_context_cards_max", 3))
        terms = keywords(query)
        if not terms:
            return {"matches": [], "tokens": 0, "query": query, "scope": scope, "reason": "query_required"}
        ranked = []
        for card in self._cue_cards(scope, cwd):
            cue = len(terms & set(card.get("keywords", [])))
            if not cue:
                continue
            ranked.append((cue + float(card.get("quality", 0)), card))
        ranked.sort(key=lambda item: (-item[0], item[1]["file"], item[1]["heading"]))
        selected, used = [], 0
        for score, card in ranked:
            if len(selected) >= max_cards or used >= limit:
                continue
            available_chars = max(0, (limit - used) * 3)
            content = card["summary"][:available_chars]
            if not content:
                continue
            tokens = estimate_tokens(content)
            selected.append({"id": card["id"], "file": card["file"].split("/")[-1], "heading": card["heading"], "content": content, "score": round(score, 3), "metadata": {"scope": card["scope"], "source_modified": card["source_modified"]}})
            used += tokens
        return {"matches": selected, "tokens": used, "query": query, "scope": scope}

    def record_recall(self, file_name: str) -> dict[str, Any]:
        data = self._index()
        now = utc_now()
        updated = 0
        for entry in data["entries"]:
            if entry.get("id") == file_name or str(entry.get("file", "")).split("/")[-1] == file_name:
                entry["last_recalled_at"] = now
                entry["recall_count"] = int(entry.get("recall_count", 0)) + 1
                entry["modified"] = now[:10]
                updated += 1
        if updated:
            save_json(self.index_path, data)
        return {"updated": updated, "file": file_name, "timestamp": now}

    def record_event(self, event: dict[str, Any], session_id: str = "", project: str = "", goal: str = "") -> dict[str, Any]:
        if contains_secret(json.dumps(event, ensure_ascii=False)):
            raise ValueError("event rejected because it appears to contain a secret")
        allowed = {"observation", "hypothesis", "decision", "action", "result", "verification", "open_loop"}
        if event.get("kind") not in allowed:
            raise ValueError("event.kind is invalid")
        journal = load_json(self.journal_path, {"entries": []})
        journal.setdefault("entries", [])
        journal["sessionId"] = session_id or journal.get("sessionId")
        journal["project"] = project or journal.get("project")
        journal["goal"] = goal or journal.get("goal")
        journal.setdefault("started", utc_now())
        item = dict(event)
        item.setdefault("at", utc_now())
        item.setdefault("importance", 0.5)
        journal["entries"].append(item)
        save_json(self.journal_path, journal)
        return {"recorded": True, "entries": len(journal["entries"])}

    def summarize(self, transcript: str, session: str, project: str) -> dict[str, Any]:
        lines: list[dict[str, Any]] = []
        path = Path(transcript) if transcript else None
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-50:]:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not lines:
            return {"sessionId": session, "date": utc_now()[:10], "project": project, "tasks_completed": ["(transcript not available for analysis)"], "key_decisions": [], "discoveries": [], "user_feedback": [], "outcomes": [], "lessons_learned": [], "candidates": [], "entries_count": 0, "auto_generated": True}
        groups = {"decision": [], "pattern": [], "feedback": [], "lesson": [], "task": []}
        patterns = {
            "decision": r"决定|选择|采用|改用|优先|避免|\b(decided|choose|chose|prefer|avoid)\b",
            "pattern": r"发现|注意到|根因|原因是|\b(found|discovered|noticed|root cause)\b",
            "feedback": r"不对|改成|应该|不要|\b(wrong|instead|should be|don't)\b",
            "lesson": r"修复|解决|错误|失败|问题|教训|\b(fixed|resolved|failed|error|lesson)\b",
            "task": r"完成|实现|创建|已处理|\b(completed|implemented|created|finished)\b",
        }
        for record in lines:
            text, role = self._transcript_text(record)
            if not text or contains_secret(text):
                continue
            for kind, pattern in patterns.items():
                if re.search(pattern, text, re.IGNORECASE) and (kind != "feedback" or role == "user"):
                    groups[kind].append(text[:300].strip())
        for kind in groups:
            groups[kind] = list(dict.fromkeys(groups[kind]))[:5]
        candidates = []
        for kind in ("decision", "pattern", "feedback", "lesson"):
            for text in groups[kind]:
                candidates.append(normalize_entry({"type": kind, "summary": text[:160], "context": text, "tags": [kind, "session"], "source": "user_feedback" if kind == "feedback" else "session", "source_session": session, "project": project, "scope": "global" if kind in {"feedback", "pattern"} else "project"}))
        return {"sessionId": session, "date": utc_now()[:10], "project": project, "tasks_completed": groups["task"], "key_decisions": groups["decision"], "discoveries": groups["pattern"], "user_feedback": groups["feedback"], "outcomes": [], "lessons_learned": groups["lesson"], "candidates": candidates, "entries_count": len(lines), "auto_generated": True}

    @staticmethod
    def _transcript_text(record: dict[str, Any]) -> tuple[str, str]:
        message = record.get("message", {}) if isinstance(record, dict) else {}
        role = str(record.get("role") or message.get("role") or "").lower()
        content = message.get("content", record.get("content", record.get("text", "")))
        if isinstance(content, list):
            content = " ".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
        return (str(content) if isinstance(content, str) else "", role)

    def stage_candidates(self, summary: dict[str, Any]) -> dict[str, Any]:
        data = load_json(self.candidates_path, {"candidates": []})
        data.setdefault("candidates", [])
        existing = {stable_id(item): item for item in data["candidates"] if isinstance(item, dict)}
        added = 0
        reinforced = 0
        for candidate in summary.get("candidates", []):
            normalized = normalize_entry(candidate)
            if contains_secret(normalized.get("summary", "")) or contains_secret(normalized.get("context", "")):
                continue
            normalized.setdefault("observations", 1)
            normalized.setdefault("successful_outcomes", 0)
            normalized.setdefault("failed_outcomes", 0)
            normalized.setdefault("source_trust", "user_confirmed" if normalized.get("source") == "user_feedback" else "agent_observed")
            if normalized["id"] not in existing:
                data["candidates"].append(normalized)
                existing[normalized["id"]] = normalized
                added += 1
            else:
                current = existing[normalized["id"]]
                current["observations"] = int(current.get("observations", 1)) + 1
                current["source_sessions"] = sorted(set(current.get("source_sessions", []) + [normalized.get("source_session", "")]))
                reinforced += 1
        save_json(self.candidates_path, data)
        return {"added": added, "reinforced": reinforced, "total": len(data["candidates"])}

    def _score(self, entry: dict[str, Any]) -> float:
        config = self.config()
        text = (str(entry.get("summary", "")) + " " + str(entry.get("context", ""))).lower()
        overrides = config.get("scoring", {}).get("overrides", {})
        if any(token in text for token in ("remember this", "记住", "保存")):
            return float(overrides.get("user_explicit_remember", 100))
        score = 45.0
        if entry.get("source") == "user_feedback" or entry.get("type") == "feedback":
            score = max(score, float(overrides.get("user_feedback_min", 70)))
        if len(text) >= 80:
            score += 12
        if len(keywords(text)) >= 5:
            score += 10
        if any(token in text for token in ("because", "验证", "verified", "always", "避免", "流程", "should")):
            score += 10
        if entry.get("project") and entry.get("type") in {"decision", "lesson"}:
            score += 8
        score += min(14, 7 * int(entry.get("successful_outcomes", 0)))
        score -= min(30, 15 * int(entry.get("failed_outcomes", 0)))
        return min(round(score, 1), 100.0)

    def evaluate(self, candidates: dict[str, Any]) -> dict[str, Any]:
        config = self.config()
        thresholds = config.get("scoring", {}).get("thresholds", {})
        promote_at = float(thresholds.get("promote", 70))
        archive_at = float(thresholds.get("archive", 50))
        result = {"promoted": [], "archived": [], "discarded": []}
        for item in candidates.get("candidates", []):
            entry = normalize_entry(item)
            entry["score"] = self._score(entry)
            explicit = entry.get("source_trust") == "user_confirmed"
            reinforced = int(entry.get("observations", 0)) >= 2 and int(entry.get("successful_outcomes", 0)) >= 2
            blocked = entry.get("source_trust") == "external_untrusted" or entry.get("validity", {}).get("state") == "disputed"
            if not blocked and entry["score"] >= promote_at and (explicit or reinforced):
                result["promoted"].append(entry)
            elif entry["score"] >= archive_at:
                result["archived"].append(entry)
            else:
                result["discarded"].append(entry)
        result["summary"] = {f"{kind}_count": len(value) for kind, value in result.items() if isinstance(value, list)}
        return result

    def promote(self, entry: dict[str, Any], file_name: str = "") -> dict[str, Any]:
        item = normalize_entry(entry)
        if contains_secret(item.get("context", "")):
            raise ValueError("memory rejected because it appears to contain a secret")
        file_name = file_name or memory_file_for(item["type"])
        target = memory_content_dir(self.memory_dir) / file_name
        section = f"\n## {item['id']}\n\n{item['summary']}\n\n- Type: {item['type']}\n- Confidence: {item['confidence']}\n- Source: {item['source']}\n- Created: {item['created']}\n"
        existing = target.read_text(encoding="utf-8") if target.exists() else f"# {target.stem.replace('-', ' ').title()}\n"
        if f"## {item['id']}\n" not in existing:
            from .storage import atomic_write_text
            atomic_write_text(target, existing.rstrip() + section)
        data = self._index()
        if not any(current.get("id") == item["id"] for current in data["entries"]):
            data["entries"].append({"id": item["id"], "file": f"global/{file_name}", "type": item["type"], "tags": item["tags"], "score": item.get("score", 70), "confidence": item["confidence"], "validity": {"state": "active"}, "recall_count": 0, "last_recalled_at": None, "supersedes": [], "conflicts": [], "created": item["created"][:10], "modified": utc_now()[:10], "tokens": estimate_tokens(section)})
            data["updated"] = utc_now()[:10]
            save_json(self.index_path, data)
        return {"promoted": True, "file": file_name, "id": item["id"]}

    def stats(self) -> dict[str, Any]:
        config = self.config()
        files = self._content_files()
        total = sum(item["tokens"] for item in files)
        budget = int(config.get("token_budget", {}).get("global_total_max", 25000))
        data = load_json(self.stats_path, {})
        data["global"] = {"total_files": len(files), "total_tokens": total, "budget": budget, "usage_pct": round(total / budget * 100, 1) if budget else 0}
        data.setdefault("sessions", {"total_archived": 0, "sessions_since_consolidation": 0})
        save_json(self.stats_path, data)
        return data

    def end_session(self, transcript: str, session: str, project: str) -> dict[str, Any]:
        """Run the portable, non-destructive consolidation lifecycle."""
        summary = self.summarize(transcript, session, project)
        return self.finish_summary(summary)

    def finish_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Consolidate a host-neutral episodic summary."""
        summary = dict(summary)
        if contains_secret(json.dumps(summary, ensure_ascii=False)):
            raise ValueError("summary rejected because it appears to contain a secret")
        summary.setdefault("sessionId", "unknown")
        summary.setdefault("date", utc_now()[:10])
        summary.setdefault("project", "global")
        summary.setdefault("candidates", [])
        archive_dir = self.memory_dir / "sessions" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        project_name = self._safe_archive_component(summary["project"], "global")
        session_name = self._safe_archive_component(summary["sessionId"], "session")
        archive = archive_dir / f"{summary['date']}-{project_name}-{session_name}.json"
        from .storage import atomic_write_text
        atomic_write_text(archive, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

        staged = self.stage_candidates(summary)
        from .cognition import CognitiveCoordinator
        consolidation = CognitiveCoordinator(self).consolidate()
        save_json(self.journal_path, {"_comment": "Live session journal — overwritten each session.", "sessionId": None, "started": None, "project": None, "entries": []})
        stats = self.stats()
        stats["sessions"]["total_archived"] = int(stats["sessions"].get("total_archived", 0)) + 1
        stats["sessions"]["sessions_since_consolidation"] = int(stats["sessions"].get("sessions_since_consolidation", 0)) + 1
        stats["sessions"]["last_session"] = utc_now()
        save_json(self.stats_path, stats)
        return {"archive": str(archive), "staged": staged, "consolidation": consolidation, "stats": stats["global"]}

    @staticmethod
    def _safe_archive_component(value: object, fallback: str) -> str:
        """Keep host-provided identifiers inside the archive directory."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
        return (cleaned or fallback)[:80]

    def health_check(self) -> dict[str, Any]:
        checks = []
        for path in self.memory_dir.rglob("*.json"):
            try:
                load_json(path)
                checks.append({"file": str(path.relative_to(self.memory_dir)), "ok": True})
            except ValueError as exc:
                checks.append({"file": str(path.relative_to(self.memory_dir)), "ok": False, "error": str(exc)})
        actual = {item["name"] for item in self._content_files()}
        indexed = {str(item.get("file", "")).split("/")[-1] for item in self._index()["entries"]}
        return {"ok": all(item["ok"] for item in checks), "json_checks": checks, "index_missing": sorted(actual - indexed), "index_orphans": sorted(indexed - actual), "global_files": len(actual), "candidate_count": len(load_json(self.candidates_path, {"candidates": []}).get("candidates", []))}
