#!/usr/bin/env python
"""Read-only health checks for the Agent memory system."""
import json
import os
from pathlib import Path

ROOT = Path(os.path.expanduser('~/.claude/memory'))

def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'_error': str(exc)}

def main():
    checks = []
    json_files = list(ROOT.rglob('*.json')) if ROOT.exists() else []
    for path in json_files:
        data = load(path)
        checks.append({'file': str(path.relative_to(ROOT)), 'ok': '_error' not in data, 'error': data.get('_error')})
    index = load(ROOT / '_system' / 'index.json')
    indexed = {e.get('file') for e in index.get('entries', []) if isinstance(e, dict)}
    markdown = {f'global/{p.name}' for p in (ROOT / 'global').glob('*.md')} if (ROOT / 'global').exists() else set()
    missing = sorted(markdown - indexed)
    return {
        'ok': all(c['ok'] for c in checks),
        'json_checks': checks,
        'index_missing_files': missing,
        'stats_present': (ROOT / '_system' / 'stats.json').exists(),
        'candidates_present': (ROOT / '_system' / 'candidates.json').exists(),
        'working_memory_present': (ROOT / 'sessions' / '_current.json').exists(),
    }

if __name__ == '__main__':
    print(json.dumps(main(), indent=2, ensure_ascii=False))
