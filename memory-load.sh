#!/usr/bin/env bash
# SessionStart memory retrieval. Fail-open: memory must never block startup.
set -u
export PYTHONIOENCODING=utf-8
PYTHON="${PYTHON:-python}"
MEMORY_HOME="${MEMORY_HOME:-$HOME/.claude/memory}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Resolve HOOK_DIR: prefer MEMORY_HOME, fallback to script's own directory
HOOK_DIR="${MEMORY_HOME%/memory}"
COREPY="${COREPY:-$SCRIPT_DIR/memory-core.py}"
INPUT=$(cat 2>/dev/null || printf '{}')
CWD=$(printf '%s' "$INPUT" | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || printf '')
[ -n "$CWD" ] || CWD=$(pwd)

emit_scope() {
  local scope="$1"
  local result
  result=$("$PYTHON" "$COREPY" retrieve --scope "$scope" --cwd "$CWD" 2>/dev/null || printf '{"matches":[]}')
  printf '%s' "$result" | "$PYTHON" -c '
import json,sys
try:
    data=json.load(sys.stdin)
    matches=data.get("matches",[])
    if matches:
        print("[Memory context]")
        for item in matches:
            print("\n### " + item.get("file", "memory"))
            print(item.get("content", "").strip())
except Exception:
    pass
' || true
  printf '%s' "$result" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(x.get("file","") for x in d.get("matches",[])))' 2>/dev/null | while IFS= read -r file; do
    [ -n "$file" ] && "$PYTHON" "$COREPY" record-recall --file "$file" --scope "$scope" >/dev/null 2>/dev/null || true
  done
}

emit_scope project
emit_scope global
exit 0
