#!/usr/bin/env bash
# memory-save.sh — SessionEnd Hook: Full Memory Lifecycle Processing
#
# 4-phase orchestration:
#   Phase 1: SUMMARIZE — transcript → structured session archive
#   Phase 2: EVALUATE  — score candidates, promote/archive/discard
#   Phase 3: COMPRESS  — check triggers, run L1+L2+L3 compression
#   Phase 4: CLEANUP   — rotate journal, update stats, update indices
#
# Hook protocol:
#   stdin:  {"session_id":"...", "transcript_path":"...", "cwd":"...", "hook_event_name":"SessionEnd"}
#   stdout: nothing (or {"continue":true})
#   stderr: progress messages
#   exit:   0 (non-blocking — memory ops never block session end)
#
# Install: add to settings.json SessionEnd hooks with timeout: 120

set -euo pipefail
export PYTHONIOENCODING=utf-8

# ── Bootstrap ────────────────────────────────────────────────────────────────

# Resolve paths: prefer env vars, fallback to script's own directory
MEMORY_DIR="${MEMORY_HOME:-$HOME/.claude/memory}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COREPY="${COREPY:-$SCRIPT_DIR/memory-core.py}"
PYTHON="${PYTHON:-python}"
export PYTHON

# Source shared utilities if available (check script dir first, then ~/.claude/hooks)
if [ -f "$SCRIPT_DIR/memory-lib.sh" ]; then
  source "$SCRIPT_DIR/memory-lib.sh"
elif [ -f "$HOME/.claude/hooks/memory-lib.sh" ]; then
  source "$HOME/.claude/hooks/memory-lib.sh"
fi

# Read hook input
INPUT=$(cat 2>/dev/null || echo '{}')
SESSION_ID=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" 2>/dev/null || echo "unknown")
TRANSCRIPT=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "$HOME")
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DATE=$(date +%Y-%m-%d)

# Determine project slug for archive filename
PROJECT=$(echo "$CWD" | tr '/\\' '-' | tr -cd '[:alnum:]-' | sed 's/-\+/-/g' | sed 's/^-//;s/-$//')
[ -z "$PROJECT" ] && PROJECT="global"

log() { echo "[memory-save] $1" >&2; }
warn() { echo "[memory-save] ⚠ $1" >&2; }

log "=== SessionEnd: $SESSION_ID ==="
log "Project: $PROJECT  |  Transcript: ${TRANSCRIPT:-none}"

# ── Phase 1: SUMMARIZE ──────────────────────────────────────────────────────

log "Phase 1/4: Summarizing session transcript..."

SUMMARY_JSON=""

if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  SUMMARY_JSON=$("$PYTHON" "$COREPY" summarize \
    --transcript "$TRANSCRIPT" \
    --session "$SESSION_ID" \
    --project "$PROJECT" 2>/dev/null || echo "")
elif [ -n "$TRANSCRIPT" ]; then
  warn "Transcript not found: $TRANSCRIPT"
  SUMMARY_JSON=$("$PYTHON" "$COREPY" summarize \
    --transcript "/nonexistent" \
    --session "$SESSION_ID" \
    --project "$PROJECT" 2>/dev/null || echo "")
else
  log "No transcript available — generating minimal summary."
  SUMMARY_JSON=$(cat << EOF
{
  "sessionId": "$SESSION_ID",
  "date": "$DATE",
  "project": "$PROJECT",
  "tasks_completed": ["(no transcript — summary unavailable)"],
  "key_decisions": [],
  "discoveries": [],
  "user_feedback": [],
  "outcomes": [],
  "lessons_learned": [],
  "entries_count": 0,
  "auto_generated": true
}
EOF
)
fi

# Build an episodic record from the working-memory journal before archive.
EPISODE_JSON=$("$PYTHON" "$COREPY" build-episode --session "$SESSION_ID" --project "$PROJECT" --task "$PROJECT" --outcome '{"status":"session_ended","summary":"SessionEnd completed"}' --confidence 0.5 2>/dev/null || echo '')

# Write archive
ARCHIVE_FILE="$MEMORY_DIR/sessions/archive/${DATE}-${PROJECT}-${SESSION_ID}.json"
mkdir -p "$(dirname "$ARCHIVE_FILE")"

if [ -n "$SUMMARY_JSON" ]; then
  "$PYTHON" -c "import sys, json; from pathlib import Path; summary=json.load(sys.stdin); episode=json.loads(sys.argv[2]) if sys.argv[2] else None; summary['episode']=episode; Path(sys.argv[1]).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')" "$ARCHIVE_FILE" "$EPISODE_JSON" <<< "$SUMMARY_JSON"
  "$PYTHON" "$COREPY" stage-candidates --summary - <<< "$SUMMARY_JSON" >/dev/null 2>/dev/null || warn "  → Candidate staging failed"
  log "  → Session archived: $ARCHIVE_FILE ($(wc -c < "$ARCHIVE_FILE" | tr -d ' ') bytes)"

  if declare -f log_operation > /dev/null 2>&1; then
    log_operation "summarize" "$SESSION_ID → archive/$(basename "$ARCHIVE_FILE")" 0 0
  fi
else
  warn "  → Summary generation failed — skipping archive."
fi

# ── Phase 2: EVALUATE ────────────────────────────────────────────────────────

log "Phase 2/4: Evaluating staged candidates..."

CANDIDATES_FILE="$MEMORY_DIR/_system/candidates.json"
EVAL_RESULT=""
PROMOTED_COUNT=0
ARCHIVED_COUNT=0
DISCARDED_COUNT=0

if [ -f "$CANDIDATES_FILE" ]; then
  CANDIDATES=$(cat "$CANDIDATES_FILE")

  # Only evaluate if there are candidates
  CANDIDATE_COUNT=$(echo "$CANDIDATES" | python -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('candidates',[])))" 2>/dev/null || echo "0")

  if [ "$CANDIDATE_COUNT" -gt 0 ]; then
    EVAL_RESULT=$("$PYTHON" "$COREPY" evaluate \
      --candidates "$CANDIDATES" \
      --scope global 2>/dev/null || echo "")

    if [ -n "$EVAL_RESULT" ]; then
      PROMOTED_COUNT=$(echo "$EVAL_RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('summary',{}).get('promoted_count',0))" 2>/dev/null || echo "0")
      ARCHIVED_COUNT=$(echo "$EVAL_RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('summary',{}).get('archived_count',0))" 2>/dev/null || echo "0")
      DISCARDED_COUNT=$(echo "$EVAL_RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('summary',{}).get('discarded_count',0))" 2>/dev/null || echo "0")

      # Promote entries that scored ≥70
      if [ "$PROMOTED_COUNT" -gt 0 ]; then
        log "  → Promoting $PROMOTED_COUNT entries..."

        echo "$EVAL_RESULT" | python -c "
import sys, json
data = json.load(sys.stdin)
for entry in data.get('promoted', []):
    print(json.dumps(entry))
" 2>/dev/null | while IFS= read -r entry_json; do
          if [ -n "$entry_json" ]; then
            "$PYTHON" "$COREPY" promote \
              --entry "$entry_json" \
              --scope global \
              --file "lessons.md" 2>/dev/null || warn "  → Promote failed for one entry"
          fi
        done

        log "  → Promoted $PROMOTED_COUNT entries to long-term memory."
      fi

      # Update candidates: keep archived, remove promoted and discarded
      echo "$EVAL_RESULT" | python -c "
import sys, json
data = json.load(sys.stdin)
# Keep only archived entries (score 50-69), increment cycle
archived = []
for entry in data.get('archived', []):
    entry['_cycle'] = entry.get('_cycle', 0) + 1
    if entry['_cycle'] < 3:  # Max 3 staging cycles
        archived.append(entry)
# Write back
with open('$CANDIDATES_FILE', 'w', encoding='utf-8') as f:
    json.dump({'candidates': archived, '_comment': 'Staged memory candidates pending evaluation.'}, f, indent=2, ensure_ascii=False)
" 2>/dev/null
    fi
  else
    log "  → No candidates to evaluate."
  fi
else
  log "  → No candidates file found — skipping evaluation."
fi

log "  → Results: $PROMOTED_COUNT promoted | $ARCHIVED_COUNT archived | $DISCARDED_COUNT discarded"

# ── Phase 3: COMPRESS ────────────────────────────────────────────────────────

log "Phase 3/4: Checking compression triggers..."

COMPRESS_RESULT=$("$PYTHON" "$COREPY" compress --scope global 2>/dev/null || echo "")

if [ -n "$COMPRESS_RESULT" ]; then
  OP_COUNT=$(echo "$COMPRESS_RESULT" | python -c "import sys,json; print(len(json.load(sys.stdin).get('operations',[])))" 2>/dev/null || echo "0")

  if [ "$OP_COUNT" -gt 0 ]; then
    log "  → Compression applied: $OP_COUNT operations"
    echo "$COMPRESS_RESULT" | python -c "
import sys, json
for op in json.load(sys.stdin).get('operations', []):
    print(f'    • {op.get(\"type\",\"?\"):12s} {op.get(\"description\",\"\")[:80]}')
" 2>/dev/null
  else
    log "  → No compression needed this cycle."
  fi
else
  log "  → Compression check skipped (no triggers met)."
fi

# ── Phase 4: CLEANUP ─────────────────────────────────────────────────────────

log "Phase 4/4: Rotating session journal & updating stats..."

# Rotate session journal
JOURNAL="$MEMORY_DIR/sessions/_current.json"
cat > "$JOURNAL" << 'JEOF'
{
  "_comment": "Live session journal — overwritten each session. Entries appended by agent during session.",
  "sessionId": null,
  "started": null,
  "project": null,
  "entries": []
}
JEOF
log "  → Journal rotated: $JOURNAL"

# Recalculate stats
STATS_RESULT=$("$PYTHON" "$COREPY" stats --scope global 2>/dev/null || echo "")

if [ -n "$STATS_RESULT" ]; then
  TOTAL_TOKENS=$(echo "$STATS_RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('global',{}).get('total_tokens',0))" 2>/dev/null || echo "0")
  USAGE_PCT=$(echo "$STATS_RESULT" | python -c "import sys,json; print(json.load(sys.stdin).get('global',{}).get('usage_pct',0))" 2>/dev/null || echo "0")
  log "  → Stats updated: $TOTAL_TOKENS tokens ($USAGE_PCT% of budget)"
fi

# Increment session counter in stats.json
PYTHONIOENCODING=utf-8 python -c "
import sys, json
from datetime import datetime, timezone

stats_file = '$MEMORY_DIR/_system/stats.json'
try:
    with open(stats_file, encoding='utf-8') as f:
        stats = json.load(f)
except:
    stats = {}

stats.setdefault('sessions', {})
stats['sessions']['total_archived'] = stats['sessions'].get('total_archived', 0) + 1
stats['sessions']['sessions_since_consolidation'] = stats['sessions'].get('sessions_since_consolidation', 0) + 1
stats['sessions']['last_session'] = '$TIMESTAMP'

with open(stats_file, 'w', encoding='utf-8') as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)
" 2>/dev/null || warn "  → Failed to update session counter"

# Update MEMORY.md timestamp
if [ -f "$MEMORY_DIR/MEMORY.md" ]; then
  sed -i "s/Updated: .*/Updated: $DATE/" "$MEMORY_DIR/MEMORY.md" 2>/dev/null || true
fi

# Update global/_index.md timestamp
if [ -f "$MEMORY_DIR/global/_index.md" ]; then
  sed -i "s/Last updated: .*/Last updated: $DATE/" "$MEMORY_DIR/global/_index.md" 2>/dev/null || true
fi

# ── Done ─────────────────────────────────────────────────────────────────────

# Count archive files
ARCHIVE_COUNT=$(find "$MEMORY_DIR/sessions/archive/" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')

log "=== SessionEnd complete ==="
log "Archives: $ARCHIVE_COUNT  |  Promoted: $PROMOTED_COUNT  |  Compressed: ${OP_COUNT:-0}"
log "See you next session."

exit 0
