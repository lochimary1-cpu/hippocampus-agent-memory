# 🧠 Hippocampus Agent Memory

仿人类海马体的、本地优先的 AI Agent 分层认知记忆核心。

Hippocampus Agent Memory 不会把完整历史对话重新塞进模型上下文。它将记忆分为工作记忆、情景档案、可塑候选记忆和长期皮层记忆；仅在当前任务有明确线索时，检索少量相关片段。

## Why this project

Many agent memory systems make context progressively larger as tasks accumulate. That raises cost and often makes recall less precise. This project uses a hippocampus-inspired lifecycle instead:

```text
Current task
  → prefrontal task framing and cue extraction
  → hippocampal cue gate
  → at most three relevant memory cards
  → compact working events and episodic summary
  → evidence-gated candidate consolidation
  → typed long-term memory
```

## Features

- Local-first, inspectable Markdown and JSON storage; no database, server, embedding API, or telemetry.
- Cue-gated retrieval: no task cue means no long-term context injection.
- Bounded context: a normal retrieval returns at most three cards and roughly 800 estimated tokens.
- Working, episodic, plastic candidate, and typed long-term memory layers.
- Semi-autonomous consolidation: agent observations need repeated evidence before promotion; explicit user confirmation is a separate event.
- Conflict marking and metadata decay without deleting source Markdown.
- A versioned host-neutral lifecycle protocol for Claude Code, Codex, OpenCode, custom agents, and task runners.
- Local credential-pattern rejection for protocol events and archives.

## Quick start

Requires Python 3.10 or later.

```bash
git clone https://github.com/lochimary1-cpu/hippocampus-agent-memory.git
cd hippocampus-agent-memory
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .

hippocampus-memory --memory-dir ./.hippocampus/memory init
hippocampus-memory --memory-dir ./.hippocampus/memory prepare-task --task "review retrieval cost"
hippocampus-memory --memory-dir ./.hippocampus/memory metrics
```

## Cross-agent lifecycle

Every host uses the same small JSON envelope. At task start, request context:

```json
{
  "version": 1,
  "event": "task.context_requested",
  "session_id": "run-042",
  "project_id": "my-project",
  "payload": {"task": "Investigate a deployment failure"}
}
```

Pass it to `hippocampus-memory --memory-dir <store> dispatch --event -`. The result's `context` is the only returned value intended for model injection. During meaningful milestones send `task.event`; at task completion send a compact `task.finished` summary; use `memory.feedback` only when later evidence or the user validates a candidate.

See the complete [cross-agent guide](docs/cross-agent-usage.md) and [Protocol v1 schema](protocol/schema-v1.json).

## Memory and token policy

| Situation | Model context from long-term memory |
|---|---:|
| No task cue | 0 tokens |
| Normal task | ≤ 3 cards, ~800 estimated tokens |
| Episodic archive | 0 tokens by default |
| Deep recall | Explicit follow-up only |

The cue index is a rebuildable local derivative. Chinese task text receives short local phrase cues so unspaced text can match relevant sections without increasing the context budget.

## Project structure

```text
src/hippocampus_memory/   Core engine, cognition coordinator, protocol, CLI
protocol/                 Versioned lifecycle schema
docs/                     Architecture and cross-agent integration guide
tests/                    Unit and integration tests
```

## Development

```bash
python -m unittest discover -s tests -q
python -m compileall -q src
```

## Current limits

- Retrieval is local lexical cue matching; a vector/semantic layer is intentionally optional and not required at startup.
- This repository ships the portable core, not a bundled native hook adapter for every host. Connect a host with a thin wrapper around the protocol events it actually supports.
- Use one memory store per project by default. If several agents share a store, serialize writes at the task-runner layer until cross-process locking is introduced.

## License

[MIT](LICENSE) © 2026 lochimary1-cpu.
