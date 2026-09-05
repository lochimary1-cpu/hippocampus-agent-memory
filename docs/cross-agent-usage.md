# Cross-agent usage

The memory core is host-neutral. An agent integration only needs to send a small JSON lifecycle event at task start, during meaningful work, and at task end. It does not need to expose a database, use embeddings, or preload a whole memory directory.

## 1. Create a portable memory home

Choose one explicit path per project when memories should be isolated. Use a deliberately shared path only when several agents should work from the same memory base.

```powershell
$engine = 'C:\path\to\hippocampus-memory'
$env:PYTHONPATH = "$engine\src"
$store = "$PWD\.hippocampus\memory"
python -m hippocampus_memory --memory-dir $store init
```

```bash
export PYTHONPATH="/path/to/hippocampus-memory/src"
store="$PWD/.hippocampus/memory"
python -m hippocampus_memory --memory-dir "$store" init
```

`--memory-dir` takes precedence over `HIPPOCAMPUS_MEMORY_DIR`. If neither is set, the compatibility default is `~/.claude/memory`.

## 2. Request task context

At the beginning of a task, the host sends only the task statement. The response is the only protocol output intended to be placed in the model context.

```json
{
  "version": 1,
  "event": "task.context_requested",
  "session_id": "run-042",
  "project_id": "website",
  "cwd": "/work/website",
  "payload": {"task": "Investigate the deployment failure"}
}
```

Pipe that JSON to the CLI as `dispatch --event -`. The returned `result.context` has at most three cards and about 800 estimated tokens. If no cues match, it returns no long-term memory; do not substitute a full-directory read.

Retrieval is local lexical cue matching today, including short Chinese phrase cues for unspaced text. That increases only the rebuildable disk index, never the context budget. Semantic/vector recall is a future optional layer, not a startup dependency.

## 3. Record only meaningful working events

During the task, write short observations, decisions, results, or open loops. Do not send raw prompts, transcripts, tool output, or credentials.

```json
{
  "version": 1,
  "event": "task.event",
  "session_id": "run-042",
  "project_id": "website",
  "payload": {
    "goal": "Restore the deployment",
    "event": {
      "kind": "decision",
      "content": "Use the project-local deployment configuration after the staging verification succeeds.",
      "importance": 0.8
    }
  }
}
```

Accepted event content is limited to 1,200 characters. The protocol refuses values that look like credentials and ignores unknown event fields.

## 4. Finish with a compact episodic summary

At task completion, an adapter sends an agent-written summary, not a transcript. Candidate memories remain plastic by default.

```json
{
  "version": 1,
  "event": "task.finished",
  "session_id": "run-042",
  "project_id": "website",
  "payload": {
    "summary": {
      "tasks_completed": ["Restored the staging deployment"],
      "key_decisions": ["Kept deployment configuration project-local"],
      "discoveries": ["The failure came from an outdated configuration path"],
      "user_feedback": [],
      "outcomes": ["Staging verification succeeded"],
      "lessons_learned": ["Verify configuration paths before changing deployment credentials"],
      "candidates": [{
        "type": "lesson",
        "summary": "Verify deployment configuration paths before changing credentials.",
        "context": "The verified staging failure was caused by an outdated project configuration path.",
        "source": "agent_observed",
        "tags": ["deployment", "verification"]
      }]
    }
  }
}
```

Each list is capped at five items. A candidate is not promoted merely because an agent proposes it: it needs two observations plus two successful outcomes. External material must be labelled `external_untrusted` and cannot auto-promote.

## 5. Reinforce, consolidate, maintain

After later task evidence, give a staged candidate an outcome. User confirmation is explicit and never inferred from agent prose.

```json
{"version":1,"event":"memory.feedback","payload":{"candidate_id":"memory-...","outcome":"successful"}}
```

Use `outcome: "user_confirmed"` only after the user explicitly validates that candidate. Then periodically dispatch `memory.consolidate`; `memory.maintain` decays index metadata but never deletes the Markdown memory source.

## Integration choices

| Agent host | Recommended integration | Notes |
|---|---|---|
| Claude Code | Thin hook adapter around the lifecycle protocol | Map supported lifecycle hooks to protocol events. Do not run two production writers for the same session. |
| Codex, OpenCode, Cursor, Aider, custom CLI | Thin wrapper around `hippocampus_memory dispatch --event -` | Call it at start/end if the host exposes hooks; otherwise call the same events from your task runner. The protocol does not depend on any particular hook API. |
| Python agent | Import `MemoryEngine` and `dispatch_event` | Keep the same envelope and dispatch order; no subprocess is required. |
| Manual operation | CLI commands | Useful for testing a store before wiring it into a host. |

The protocol schema is available at [schema-v1.json](../protocol/schema-v1.json). Native MCP transport and host-specific adapters are intentionally not bundled: the stable unit is the local lifecycle protocol, so an adapter can stay small and match the host's actual hook capabilities.

## Recommended adapter order

```text
task start → task.context_requested → inject only returned context
meaningful milestone → task.event (zero or a few times)
task end → task.finished → later evidence → memory.feedback
scheduled or manual → memory.consolidate / memory.maintain
```

Keep `session_id` stable for one run and `project_id` stable for one repository. Do not make a fresh memory store per task, and do not let unrelated projects silently share one store.
