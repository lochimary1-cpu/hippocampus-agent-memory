#!/usr/bin/env bash
# memory-lib.sh — Shared utility functions for memory management hooks
# Source this in other hook scripts: source "$HOME/.claude/hooks/memory-lib.sh"
#
# Provides: resolve_memory_dir, estimate_tokens, safe_json_get,
#           rotate_journal, log_operation, get_config

set -euo pipefail
export PYTHONIOENCODING=utf-8

# Python detection (Windows: python3 in App Store is broken, python is real)
PYTHON=""
for py in python3 python; do
  if command -v "$py" > /dev/null 2>&1 && "$py" --version > /dev/null 2>&1; then
    PYTHON="$py"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  PYTHON="python"  # fallback
fi
export PYTHON

# Resolve MEMORY_DIR: use env var or default to ~/.claude/memory
# Only set if not already defined by the calling script.
if [ -z "${MEMORY_DIR:-}" ]; then
  MEMORY_DIR="${MEMORY_HOME:-$HOME/.claude/memory}"
fi
CONFIG_FILE="$MEMORY_DIR/.memory-config.json"
SYSTEM_DIR="$MEMORY_DIR/_system"

# ── Path Resolution ──────────────────────────────────────────────────────────

resolve_memory_dir() {
  # Determine memory scope (global or project) from current working directory.
  # Walk up from CWD looking for .claude/ directory.
  # Returns: "global" or "project:<project_root>"
  local cwd="${1:-$(pwd)}"
  local dir="$cwd"

  while [ "$dir" != "/" ] && [ "$dir" != "." ]; do
    if [ -d "$dir/.claude/memory" ]; then
      echo "project:$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done

  echo "global"
}

get_project_memory_dir() {
  # Return the project-level memory directory, or empty if none.
  local scope
  scope="$(resolve_memory_dir "${1:-$(pwd)}")"
  if [[ "$scope" == project:* ]]; then
    echo "${scope#project:}/.claude/memory"
  else
    echo ""
  fi
}

# ── Token Estimation ─────────────────────────────────────────────────────────

estimate_tokens() {
  # Conservative estimate: ~3 characters per token for mixed content.
  # Accepts: file path or stdin text.
  # Returns: integer token count.
  if [ $# -ge 1 ] && [ -f "$1" ]; then
    local chars
    chars=$(wc -c < "$1" | tr -d ' ')
    echo $(( chars / 3 ))
  else
    local chars
    chars=$(echo "$*" | wc -c | tr -d ' ')
    echo $(( chars / 3 ))
  fi
}

check_token_budget() {
  # Check if adding new_tokens would exceed budget for a scope.
  # Args: scope (global|project), new_tokens
  # Returns: 0 if within budget, 1 if exceeded.
  local scope="$1"
  local new_tokens="${2:-0}"
  local stats_file="$SYSTEM_DIR/stats.json"

  if [ ! -f "$stats_file" ]; then
    return 0  # No stats yet, assume OK
  fi

  local current usage budget
  if [ "$scope" = "global" ]; then
    current=$(${PYTHON} -c "import json; print(json.load(open('$stats_file'))['global']['total_tokens'])" 2>/dev/null || echo "0")
    budget=$(${PYTHON} -c "import json; print(json.load(open('$CONFIG_FILE'))['token_budget']['global_total_max'])" 2>/dev/null || echo "25000")
  else
    current=$(${PYTHON} -c "import json; print(json.load(open('$stats_file'))['global']['total_tokens'])" 2>/dev/null || echo "0")
    budget=$(${PYTHON} -c "import json; print(json.load(open('$CONFIG_FILE'))['token_budget']['per_project_total_max'])" 2>/dev/null || echo "15000")
  fi

  if [ "$((current + new_tokens))" -gt "$budget" ]; then
    return 1
  fi
  return 0
}

# ── JSON Helpers ─────────────────────────────────────────────────────────────

safe_json_get() {
  # Extract a field value from a JSON string without jq.
  # Uses python3 for reliable parsing.
  # Args: json_string field_name
  local json="$1"
  local field="$2"

  ${PYTHON} -c "
import sys, json
try:
    data = json.loads(sys.argv[1])
    val = data.get(sys.argv[2], '')
    print(val if val else '')
except:
    print('')
" "$json" "$field" 2>/dev/null
}

# ── File Operations ──────────────────────────────────────────────────────────

rotate_journal() {
  # Reset _current.json to empty template.
  # Old contents are NOT archived here — archive happens in Phase 1 of memory-save.sh.
  local journal="$MEMORY_DIR/sessions/_current.json"

  cat > "$journal" << 'JEOF'
{
  "_comment": "Live session journal — overwritten each session. Entries appended by agent during session.",
  "sessionId": null,
  "started": null,
  "project": null,
  "entries": []
}
JEOF
}

log_operation() {
  # Append a timestamped operation entry to compression-log.json.
  # Args: operation_type description [token_before] [token_after]
  local op_type="$1"
  local description="$2"
  local token_before="${3:-0}"
  local token_after="${4:-0}"
  local log_file="$SYSTEM_DIR/compression-log.json"
  local date_str
  date_str=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  ${PYTHON} -c "
import sys, json
log_file = sys.argv[1]
entry = {
    'date': sys.argv[2],
    'operation': sys.argv[3],
    'description': sys.argv[4],
    'token_before': int(sys.argv[5]),
    'token_after': int(sys.argv[6])
}
try:
    with open(log_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
except:
    data = {'operations': []}
data.setdefault('operations', []).append(entry)
with open(log_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
" "$log_file" "$date_str" "$op_type" "$description" "$token_before" "$token_after"
}

get_config() {
  # Read a config value from .memory-config.json by dot-separated path.
  # Args: "scoring.thresholds.promote"
  local path="$1"
  ${PYTHON} -c "
import sys, json
keys = sys.argv[1].split('.')
with open('$CONFIG_FILE') as f:
    data = json.load(f)
for k in keys:
    data = data.get(k, {})
print(data if not isinstance(data, dict) else '')
" "$path" 2>/dev/null
}
