# Semi-autonomous brain architecture

## Operating flow

```text
Agent host adapter
  → Lifecycle protocol: normalize a task event without retaining a host transcript
  → Prefrontal coordinator: extract task cues and set a context budget
  → Hippocampal gate: retrieve at most three matching cue cards
  → Working memory: record only important observations, decisions, results, and open loops
  → Session end: create an episodic archive and stage plastic candidates
  → Consolidation: promote only user-confirmed or repeatedly successful candidates
  → Cortex: persist typed preferences, decisions, patterns, procedures, and lessons
  → Maintenance: inspect conflict, decay metadata, and metrics
```

## Memory regions

| Region | Storage | Authority | Lifecycle |
|---|---|---|---|
| Prefrontal coordinator | Task frame in memory | Read-only | One task |
| Working memory | `sessions/_current.json` | Replaceable | One session |
| Episodic memory | `sessions/archive/*.json` | Append-only | Long-lived evidence |
| Plastic candidates | `_system/candidates.json` | Semi-autonomous | Evidence-gated |
| Cortex | Typed Markdown plus index | Evidence-backed writes | Long-term |
| Retrieval layer | `_system/cue-index.json` | Derived only | Rebuildable |
| Host boundary | Protocol v1 JSON envelope | Adapter-only | One task event |

## Plasticity rules

- A candidate from an agent observation needs at least two observations and two successful outcomes before automatic promotion.
- A user-confirmed candidate can be promoted after normal quality checks.
- Untrusted external material is blocked from automatic promotion.
- Conflicting candidates are marked disputed and withheld from automatic promotion.
- Maintenance retires only metadata; it does not delete Markdown source memory.

## Token policy

- No task cue: zero long-term-memory tokens.
- Normal task: at most three card summaries and 800 estimated tokens.
- Deeper recall is an explicit follow-up action, not a startup default.
- The protocol's task context request is the only lifecycle response eligible for model injection.

## Host integration boundary

Each host maps its own task lifecycle to Protocol v1. The portable core does not parse host-specific transcripts and does not require a particular hook system. An adapter may call the CLI at task start/end, or dispatch the same envelope through the Python API.
