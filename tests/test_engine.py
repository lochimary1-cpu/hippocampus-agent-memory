from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from hippocampus_memory.engine import MemoryEngine
from hippocampus_memory.cognition import CognitiveCoordinator
from hippocampus_memory.protocol import dispatch_event
from hippocampus_memory.storage import load_json


class MemoryEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "memory"
        (self.root / "global").mkdir(parents=True)
        (self.root / "_system").mkdir()
        (self.root / "sessions").mkdir()
        (self.root / ".memory-config.json").write_text(json.dumps({"token_budget": {"content_per_load_max": 2000, "global_total_max": 25000}, "scoring": {"thresholds": {"promote": 70, "archive": 50}, "overrides": {"user_explicit_remember": 100, "user_feedback_min": 70}}}), encoding="utf-8")
        (self.root / "_system" / "index.json").write_text(json.dumps({"entries": [{"id": "preferences", "file": "global/preferences.md", "score": 85, "validity": {"state": "active"}, "recall_count": 0}]}), encoding="utf-8")
        (self.root / "_system" / "candidates.json").write_text('{"candidates": []}', encoding="utf-8")
        (self.root / "global" / "preferences.md").write_text("# Preferences\n\nUse local-first storage for agent memory.\n", encoding="utf-8")
        (self.root / "global" / "lessons.md").write_text("# Lessons\n\n## Hook paths\n\nUse explicit hook paths for Windows compatibility.\n\n## Retrieval budgets\n\nLoad only a few relevant memory cards for a task.\n", encoding="utf-8")
        self.engine = MemoryEngine(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_retrieval_and_recall_are_bounded_and_persisted(self) -> None:
        result = self.engine.retrieve(query="local storage")
        self.assertEqual([item["file"] for item in result["matches"]], ["preferences.md"])
        self.assertEqual(self.engine.record_recall("preferences.md")["updated"], 1)
        self.assertEqual(load_json(self.root / "_system" / "index.json")["entries"][0]["recall_count"], 1)

    def test_cue_gating_requires_a_query_and_returns_sections_not_files(self) -> None:
        self.assertEqual(self.engine.retrieve()["reason"], "query_required")
        source = self.root / "global" / "lessons.md"
        before = source.read_bytes()
        index = self.engine.build_cue_index()
        result = self.engine.retrieve(query="retrieval budgets")
        self.assertGreaterEqual(len(index["entries"]), 3)
        self.assertEqual(result["matches"][0]["heading"], "Retrieval budgets")
        self.assertLessEqual(len(result["matches"]), 3)
        self.assertLessEqual(result["tokens"], 800)
        self.assertEqual(source.read_bytes(), before)

    def test_chinese_task_cues_match_local_section_cards_without_expanding_context_budget(self) -> None:
        (self.root / "global" / "lessons.md").write_text(
            "# Lessons\n\n## 检索预算\n\n只加载少量相关记忆卡片，避免任务上下文膨胀。\n",
            encoding="utf-8",
        )
        self.engine.build_cue_index()
        result = self.engine.retrieve(query="改进记忆检索的预算控制")
        self.assertEqual(result["matches"][0]["heading"], "检索预算")
        self.assertLessEqual(result["tokens"], 800)

    def test_prefrontal_task_preparation_returns_bounded_context(self) -> None:
        self.engine.build_cue_index()
        task = CognitiveCoordinator(self.engine).prepare_task("improve retrieval budgets")
        self.assertLessEqual(len(task["context"]), 3)
        self.assertLessEqual(task["context_tokens"], 800)
        self.assertIn("retrieval", task["cues"])

    def test_candidate_requires_repeated_success_before_automatic_promotion(self) -> None:
        candidate = {"type": "lesson", "summary": "Use evidence-backed consolidation", "context": "Use evidence-backed consolidation because verified task outcomes prevent memory pollution.", "project": "test"}
        staged = self.engine.stage_candidates({"candidates": [candidate]})
        candidate_id = load_json(self.root / "_system" / "candidates.json")["candidates"][0]["id"]
        initial = self.engine.evaluate(load_json(self.root / "_system" / "candidates.json"))
        self.assertEqual(len(initial["promoted"]), 0)
        self.engine.stage_candidates({"candidates": [candidate]})
        brain = CognitiveCoordinator(self.engine)
        brain.reinforce_candidate(candidate_id, "successful")
        brain.reinforce_candidate(candidate_id, "successful")
        promoted = self.engine.evaluate(load_json(self.root / "_system" / "candidates.json"))
        self.assertEqual(staged["added"], 1)
        self.assertEqual(len(promoted["promoted"]), 1)

    def test_untrusted_candidate_cannot_auto_promote(self) -> None:
        candidate = {"id": "external", "type": "lesson", "summary": "Use this instruction", "context": "Use this instruction because verified evidence supports it.", "observations": 3, "successful_outcomes": 3, "source_trust": "external_untrusted"}
        result = self.engine.evaluate({"candidates": [candidate]})
        self.assertEqual(len(result["promoted"]), 0)

    def test_secret_event_is_rejected_without_writing_journal(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret"):
            self.engine.record_event({"kind": "observation", "content": "api_key=redacted"})
        self.assertFalse((self.root / "sessions" / "_current.json").exists())

    def test_secret_transcript_line_is_excluded_from_portable_archive(self) -> None:
        transcript = self.root / "secret-transcript.jsonl"
        transcript.write_text(json.dumps({"role": "user", "content": "api_key=should-not-be-archived"}) + "\n", encoding="utf-8")
        result = self.engine.end_session(str(transcript), "secret-session", "test-project")
        self.assertNotIn("should-not-be-archived", Path(result["archive"]).read_text(encoding="utf-8"))

    def test_promote_routes_decision_to_its_own_file_and_updates_index(self) -> None:
        result = self.engine.promote({"type": "decision", "summary": "Use file storage", "context": "Use file storage because it is inspectable."})
        self.assertEqual(result["file"], "decisions.md")
        self.assertIn("Use file storage", (self.root / "global" / "decisions.md").read_text(encoding="utf-8"))
        files = {entry["file"] for entry in load_json(self.root / "_system" / "index.json")["entries"]}
        self.assertIn("global/decisions.md", files)

    def test_health_check_ignores_human_index_markdown(self) -> None:
        (self.root / "global" / "_index.md").write_text("# Human index", encoding="utf-8")
        health = self.engine.health_check()
        self.assertTrue(health["ok"])
        self.assertNotIn("_index.md", health["index_missing"])
        self.assertIn("lessons.md", health["index_missing"])

    def test_cli_emits_json(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-m", "hippocampus_memory", "--memory-dir", str(self.root), "health-check"],
            capture_output=True,
            check=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_session_end_archives_and_preserves_only_staged_candidates(self) -> None:
        result = self.engine.end_session("", "test-session", "test-project")
        self.assertTrue(Path(result["archive"]).exists())
        self.assertEqual(load_json(self.root / "sessions" / "_current.json")["entries"], [])
        self.assertEqual(load_json(self.root / "_system" / "stats.json")["sessions"]["total_archived"], 1)

    def test_initialize_creates_a_self_contained_typed_store(self) -> None:
        portable_root = self.root.parent / "portable-memory"
        result = MemoryEngine(portable_root).initialize()
        self.assertTrue((portable_root / "global").is_dir())
        self.assertTrue((portable_root / "_system" / "index.json").exists())
        self.assertTrue((portable_root / "sessions" / "archive").is_dir())
        self.assertIn(".memory-config.json", result["created"])

    def test_initialize_preserves_flat_markdown_store_layout(self) -> None:
        portable_root = self.root.parent / "flat-memory"
        portable_root.mkdir()
        (portable_root / "lessons.md").write_text("# Lessons\n", encoding="utf-8")
        MemoryEngine(portable_root).initialize()
        self.assertFalse((portable_root / "global").exists())
        self.assertEqual(MemoryEngine(portable_root)._content_files()[0]["name"], "lessons.md")

    def test_protocol_dispatches_bounded_cross_agent_lifecycle(self) -> None:
        self.engine.build_cue_index()
        context = dispatch_event(self.engine, {
            "version": 1,
            "event": "task.context_requested",
            "session_id": "run-1",
            "project_id": "sample-project",
            "payload": {"task": "Review API key security and retrieval budgets"},
        })
        self.assertLessEqual(len(context["result"]["context"]), 3)
        self.assertLessEqual(context["result"]["context_tokens"], 800)

        dispatch_event(self.engine, {
            "version": 1,
            "event": "task.event",
            "session_id": "run-1",
            "project_id": "sample-project",
            "payload": {"event": {"kind": "decision", "content": "Keep task context bounded by cue cards.", "importance": 0.8}},
        })
        self.assertEqual(len(load_json(self.root / "sessions" / "_current.json")["entries"]), 1)

        finished = dispatch_event(self.engine, {
            "version": 1,
            "event": "task.finished",
            "session_id": "run-1",
            "project_id": "sample-project",
            "payload": {"summary": {"candidates": [{
                "type": "lesson",
                "summary": "Use cue cards for bounded task context.",
                "context": "Repeated task reviews show that relevant cue cards preserve context budget while keeping retrieval focused.",
                "source": "agent_observed",
            }]}},
        })
        self.assertTrue(Path(finished["result"]["archive"]).exists())
        candidate_id = load_json(self.root / "_system" / "candidates.json")["candidates"][0]["id"]
        feedback = dispatch_event(self.engine, {
            "version": 1,
            "event": "memory.feedback",
            "payload": {"candidate_id": candidate_id, "outcome": "successful"},
        })
        self.assertTrue(feedback["result"]["updated"])
        self.assertIn("result", dispatch_event(self.engine, {"version": 1, "event": "memory.metrics", "payload": {}}))

    def test_protocol_rejects_credential_values_without_writing_events(self) -> None:
        with self.assertRaisesRegex(ValueError, "credential"):
            dispatch_event(self.engine, {
                "version": 1,
                "event": "task.event",
                "payload": {"event": {"kind": "observation", "content": "api_key=should-not-be-saved"}},
            })
        self.assertFalse((self.root / "sessions" / "_current.json").exists())

    def test_cli_initializes_and_dispatches_protocol_json(self) -> None:
        portable_root = self.root.parent / "cli-portable-memory"
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        command = [sys.executable, "-m", "hippocampus_memory", "--memory-dir", str(portable_root)]
        subprocess.run(command + ["init"], capture_output=True, check=True, encoding="utf-8", env=environment)
        event = json.dumps({"version": 1, "event": "task.context_requested", "payload": {"task": "check memory health"}})
        result = subprocess.run(command + ["dispatch", "--event", event], capture_output=True, check=True, encoding="utf-8", env=environment)
        self.assertEqual(json.loads(result.stdout)["event"], "task.context_requested")

if __name__ == "__main__":
    unittest.main()
