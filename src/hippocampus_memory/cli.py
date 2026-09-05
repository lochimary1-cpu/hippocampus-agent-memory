"""JSON CLI for the portable memory engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import MemoryEngine
from .cognition import CognitiveCoordinator
from .protocol import dispatch_event
from .storage import load_json


def _json_argument(value: str) -> dict:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _summary_argument(value: str) -> dict:
    if value == "-":
        return json.load(sys.stdin)
    return load_json(Path(value))


def _protocol_argument(value: str) -> dict:
    if value == "-":
        try:
            return json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
    return _json_argument(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hippocampus-memory")
    parser.add_argument("--memory-dir", help="memory root; defaults to ~/.claude/memory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--event", type=_protocol_argument, required=True, help="protocol JSON object or - for stdin")
    retrieve = sub.add_parser("retrieve")
    retrieve.add_argument("--scope", default="global")
    retrieve.add_argument("--query", default="")
    retrieve.add_argument("--cwd", default="")
    sub.add_parser("build-cue-index")
    task = sub.add_parser("prepare-task")
    task.add_argument("--task", required=True)
    task.add_argument("--cwd", default="")
    recall = sub.add_parser("record-recall")
    recall.add_argument("--file", required=True)
    event = sub.add_parser("record-event")
    event.add_argument("--event", type=_json_argument, required=True)
    event.add_argument("--session", default="")
    event.add_argument("--project", default="")
    event.add_argument("--goal", default="")
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--transcript", default="")
    summarize.add_argument("--session", required=True)
    summarize.add_argument("--project", required=True)
    stage = sub.add_parser("stage-candidates")
    stage.add_argument("--summary", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--candidates", required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--entry", type=_json_argument, required=True)
    promote.add_argument("--file", default="")
    reinforce = sub.add_parser("reinforce-candidate")
    reinforce.add_argument("--id", required=True)
    reinforce.add_argument("--outcome", required=True)
    session_end = sub.add_parser("session-end")
    session_end.add_argument("--transcript", default="")
    session_end.add_argument("--session", required=True)
    session_end.add_argument("--project", required=True)
    sub.add_parser("stats")
    sub.add_parser("consolidate")
    sub.add_parser("maintain")
    sub.add_parser("metrics")
    sub.add_parser("detect-conflicts")
    sub.add_parser("health-check")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = MemoryEngine(args.memory_dir)
    cognition = CognitiveCoordinator(engine)
    try:
        if args.command == "init":
            result = engine.initialize()
        elif args.command == "dispatch":
            result = dispatch_event(engine, args.event)
        elif args.command == "retrieve":
            result = engine.retrieve(args.scope, args.query, args.cwd)
        elif args.command == "build-cue-index":
            result = engine.build_cue_index()
        elif args.command == "prepare-task":
            result = cognition.prepare_task(args.task, args.cwd)
        elif args.command == "record-recall":
            result = engine.record_recall(args.file)
        elif args.command == "record-event":
            result = engine.record_event(args.event, args.session, args.project, args.goal)
        elif args.command == "summarize":
            result = engine.summarize(args.transcript, args.session, args.project)
        elif args.command == "stage-candidates":
            result = engine.stage_candidates(_summary_argument(args.summary))
        elif args.command == "evaluate":
            result = engine.evaluate(_summary_argument(args.candidates))
        elif args.command == "promote":
            result = engine.promote(args.entry, args.file)
        elif args.command == "reinforce-candidate":
            result = cognition.reinforce_candidate(args.id, args.outcome)
        elif args.command == "session-end":
            result = engine.end_session(args.transcript, args.session, args.project)
        elif args.command == "stats":
            result = engine.stats()
        elif args.command == "consolidate":
            result = cognition.consolidate()
        elif args.command == "maintain":
            result = cognition.maintain()
        elif args.command == "metrics":
            result = cognition.metrics()
        elif args.command == "detect-conflicts":
            result = {"conflicts": cognition.detect_conflicts()}
        else:
            result = engine.health_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
