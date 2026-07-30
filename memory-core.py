#!/usr/bin/env python
"""
memory-core.py — Intelligence engine for the Memory Management System.

CLI: python3 memory-core.py <command> [flags] [--json-input | --args ...]

Commands:
  summarize   Analyze transcript JSONL → structured session summary
  evaluate    Score candidates with 5-dim algorithm → {promoted, archived, discarded}
  compress    Run 3-level compression on memory files (L1 dedup, L2 abstract, L3 reorganize)
  promote     Write a memory entry to target markdown file with proper frontmatter
  stats       Recalculate token counts → _system/stats.json
  decay       Apply age-based score decay to all memories

Design: No external dependencies beyond Python stdlib. All config from .memory-config.json.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

MEMORY_DIR = os.environ.get("MEMORY_HOME", os.path.expanduser("~/.claude/memory"))
CONFIG_FILE = os.environ.get("MEMORY_CONFIG", os.path.join(MEMORY_DIR, ".memory-config.json"))
SYSTEM_DIR = os.path.join(MEMORY_DIR, "_system")
GLOBAL_DIR = os.path.join(MEMORY_DIR, "global")
INDEX_FILE = os.path.join(SYSTEM_DIR, "index.json")
SCORING_FILE = os.path.join(SYSTEM_DIR, "scoring.json")
CANDIDATES_FILE = os.path.join(SYSTEM_DIR, "candidates.json")
COMPRESSION_LOG = os.path.join(SYSTEM_DIR, "compression-log.json")
STATS_FILE = os.path.join(SYSTEM_DIR, "stats.json")

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def atomic_write_text(path, content):
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.memory-', dir=directory, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def save_json(path, data):
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + chr(10))

def estimate_tokens(text):
    """Conservative UTF-8-independent estimate shared by all core operations."""
    return max(1, len(text) // 3)


def stable_entry_id(entry):
    raw = entry.get('id')
    if raw:
        return str(raw)
    payload = '|'.join([
        str(entry.get('type', 'lesson')),
        str(entry.get('summary', '')).strip(),
        str(entry.get('context', '')).strip(),
        ','.join(sorted(str(t) for t in entry.get('tags', []))),
    ])
    return 'memory-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def jaccard_similarity(a_words, b_words):
    """Jaccard coefficient: |A ∩ B| / |A ∪ B|."""
    if not a_words or not b_words:
        return 0.0
    a_set = set(a_words)
    b_set = set(b_words)
    intersection = len(a_set & b_set)
    union = len(a_set | b_set)
    return intersection / union if union > 0 else 0.0


def extract_keywords(text):
    """Extract meaningful words (>=3 chars, not stopwords)."""
    stopwords = {
        'the', 'and', 'for', 'this', 'that', 'with', 'from', 'have',
        'was', 'are', 'but', 'not', 'you', 'all', 'can', 'has', 'had',
        'its', 'been', 'when', 'will', 'would', 'could', 'should', 'being',
        'also', 'just', 'like', 'very', 'much', 'then', 'than', 'into'
    }
    english = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text.lower())
    chinese = re.findall(r'[一-鿿]{2,}', text)
    return [w for w in english if w not in stopwords] + chinese


def resolve_memory_root(scope='global', cwd=''):
    """Resolve global or project memory using a normalized filesystem path."""
    if scope == 'global':
        return GLOBAL_DIR
    start = Path(cwd or os.getcwd()).expanduser()
    if not start.exists():
        start = start.parent
    for candidate in [start, *start.parents]:
        project_memory = candidate / '.claude' / 'memory'
        if project_memory.is_dir():
            return str(project_memory)
    return ''


def read_memory_files(scope="global", cwd=''):
    """Read all markdown memory files for the selected scope."""
    memory_root = resolve_memory_root(scope, cwd)
    if not memory_root or not os.path.exists(memory_root):
        return []


    results = []
    for fname in sorted(os.listdir(memory_root)):
        if fname.startswith('_') or not fname.endswith('.md'):
            continue
        fpath = os.path.join(memory_root, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        results.append({
            'file': fpath,
            'name': fname,
            'content': content,
            'tokens': estimate_tokens(content)
        })
    return results


def get_existing_memories(scope="global"):
    """Get all existing long-term memories from index."""
    index = load_json(INDEX_FILE)
    return [e for e in index.get('entries', []) if e.get('id')]


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_memory(entry, existing_memories=None, config=None):
    """
    5-dimension composite scoring.
    Returns float in [0, 100].
    """
    if config is None:
        config = load_config()
    if existing_memories is None:
        existing_memories = get_existing_memories()

    weights = config['scoring']['weights']
    thresholds = config['scoring']['thresholds']

    # Check overrides first
    text = entry.get('summary', '') + ' ' + entry.get('context', '')
    if any(kw in text.lower() for kw in ['remember this', '记住', '保存', '记住这个']):
        return float(config['scoring']['overrides']['user_explicit_remember'])

    if entry.get('type') == 'feedback' or entry.get('source') == 'user_feedback':
        # User feedback always gets at least the feedback minimum
        base = float(config['scoring']['overrides']['user_feedback_min'])
    else:
        base = 0.0

    scores = {
        'generality':    _score_generality(entry),
        'actionability': _score_actionability(entry),
        'durability':    _score_durability(entry, config),
        'novelty':       _score_novelty(entry, existing_memories),
        'specificity':   _score_specificity(entry),
    }

    composite = sum(scores[k] * weights[k] for k in scores) * 100
    return max(base, composite)


def _score_generality(entry):
    score = 0.5  # neutral start
    text = (entry.get('summary', '') + ' ' + entry.get('context', '')).lower()

    # Multi-project applicability signals
    multi_project_tags = {'pattern', 'workflow', 'architecture', 'cognitive', 'lesson'}
    tags = set(t.lower() for t in entry.get('tags', []))
    if tags & multi_project_tags:
        score += 0.3

    # Abstract pattern indicators
    if re.search(r'(prefer|avoid|always|never|pattern|principle|rule)\b', text):
        score += 0.2

    # Specific file paths suggest low generality
    if re.search(r'[/\\][a-zA-Z0-9_.-]+\.(py|sh|md|json|ts|js|yaml|yml)\b', text):
        score -= 0.4

    # Debug output or stack traces
    if re.search(r'(traceback|stack trace|error at line|debug)', text):
        score -= 0.3

    return max(0.0, min(1.0, score))


def _score_actionability(entry):
    score = 0.5
    text = (entry.get('summary', '') + ' ' + entry.get('context', '')).lower()

    # Decision language
    decision_words = r'\b(chose|decided|prefer|avoid|switch|migrate|adopt|replace)\b'
    if re.search(decision_words, text):
        score += 0.3

    # Concrete action steps
    if re.search(r'(\d+\.|step|first|then|finally|must|should|need to)', text):
        score += 0.2

    # Vague platitudes
    if len(text.split()) < 20 and not re.search(decision_words, text):
        score -= 0.2

    return max(0.0, min(1.0, score))


def _score_durability(entry, config):
    """Age-decayed durability based on content type."""
    import math

    entry_type = entry.get('type', 'reference')
    half_lives = config['consolidation']['type_half_lives']
    half_life = half_lives.get(entry_type, 90)  # days
    decay_lambda = config['consolidation']['age_decay_lambda']

    # Parse creation date
    created_str = entry.get('created', entry.get('time', ''))
    try:
        if created_str:
            created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - created).days
        else:
            age_days = 0
    except (ValueError, TypeError):
        age_days = 0

    # Decay formula: e^(-lambda * age)
    durability = math.exp(-decay_lambda * age_days)

    # Adjust by type half-life (normalized to 90-day baseline)
    type_factor = half_life / 90.0
    durability *= type_factor

    return max(0.1, min(1.0, durability))


def _score_novelty(entry, existing_memories):
    """1.0 - max similarity with any existing memory."""
    if not existing_memories:
        return 1.0

    entry_words = extract_keywords(entry.get('summary', ''))
    if not entry_words:
        return 0.5

    max_sim = 0.0
    for existing in existing_memories:
        # Compare tags first
        tag_sim = jaccard_similarity(entry.get('tags', []), existing.get('tags', []))
        existing_text = ' '.join([
            str(existing.get('description', '')),
            str(existing.get('summary', '')),
            str(existing.get('context', '')),
        ])
        content_sim = jaccard_similarity(entry_words, extract_keywords(existing_text))
        max_sim = max(max_sim, tag_sim * 0.5 + content_sim * 0.5)

    return 1.0 - max_sim


def _score_specificity(entry):
    """Higher = more concrete and specific."""
    text = entry.get('summary', '') + ' ' + entry.get('context', '')

    if len(text) < 50:
        return 0.2

    # Count concrete nouns (technical terms, proper names, numbers)
    concrete_patterns = [
        r'\b[A-Z][a-z]+\b',           # Capitalized words
        r'\b\d+\b',                     # Numbers
        r'[a-z]+[A-Z][a-z]*',          # camelCase
        r'[a-z]+_[a-z]+',              # snake_case
        r'\b(api|cli|hook|bash|python|json|yaml|http)\b',  # Technical terms
    ]
    concrete = sum(1 for p in concrete_patterns for _ in re.finditer(p, text))

    # Count abstract words
    abstract_words = r'\b(approach|method|system|process|concept|pattern|framework|strategy)\b'
    abstract_count = len(re.findall(abstract_words, text.lower()))

    total_words = len(text.split())
    if total_words == 0:
        return 0.0

    ratio = (concrete + 1) / (abstract_count + concrete + 2)
    return max(0.1, min(1.0, ratio))


# ── Summarization ────────────────────────────────────────────────────────────

def summarize_session(transcript_path, session_id, project):
    """
    Analyze transcript JSONL to extract structured session summary.
    Returns dict ready for sessions/archive/*.json.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return {
            'sessionId': session_id,
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'project': project,
            'tasks_completed': ['(transcript not available for analysis)'],
            'key_decisions': [],
            'discoveries': [],
            'user_feedback': [],
            'outcomes': [],
            'lessons_learned': [],
            'entries_count': 0,
            'auto_generated': True
        }

    # Read and parse JSONL transcript
    lines = []
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, PermissionError):
        return {
            'sessionId': session_id,
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'project': project,
            'tasks_completed': ['(could not read transcript)'],
            'key_decisions': [],
            'discoveries': [],
            'user_feedback': [],
            'outcomes': [],
            'lessons_learned': [],
            'entries_count': 0,
            'auto_generated': True
        }

    # Analyze last ~50 exchanges (most recent context)
    recent = lines[-50:] if len(lines) > 50 else lines

    decisions = []
    discoveries = []
    feedback = []
    outcomes = []
    lessons = []
    tasks = []

    for entry in recent:
        # Extract text content from various message formats
        text = _extract_text_from_entry(entry)
        role = str(entry.get('role', entry.get('message', {}).get('role', ''))).lower()

        if not text:
            continue

        text_lower = text.lower()

        # Chinese and English event detection
        decision_re = r"(cho[os]e|decided|decide|opt(ed)? for|went with|prefer|avoid)|决定|选择|采用|改用|优先|避免"
        discovery_re = r"(found that|discovered|realized|noticed|turns out|the root cause|this happened because)|发现|原来|注意到|根因|原因是"
        feedback_re = r"(correct|wrong|don't do that|not what i|change this|instead|should be)|不对|不是这样|改成|应该|不要|请改"
        lesson_re = r"(lesson|learned|fixed|resolved|solved|bug|error|failed|mistake)|修复|解决|错误|失败|问题|教训|学到"
        task_re = r"(completed|finished|done|implemented|created|built|written)|完成|实现|创建|写好|已处理"

        if re.search(decision_re, text_lower) or any(token in text_lower for token in ('记住', '保存', 'remember this')):
            snippet = text[:300].strip()
            if len(snippet) > 10:
                decisions.append(snippet)
        if re.search(discovery_re, text_lower):
            snippet = text[:300].strip()
            if len(snippet) > 20:
                discoveries.append(snippet)
        if re.search(feedback_re, text_lower) and role == 'user':
            snippet = text[:300].strip()
            if len(snippet) > 10:
                feedback.append(snippet)
        if re.search(lesson_re, text_lower):
            snippet = text[:300].strip()
            if len(snippet) > 20:
                lessons.append(snippet)
        if re.search(task_re, text_lower):
            snippet = text[:200].strip()
            if len(snippet) > 20:
                tasks.append(snippet)

    decisions = _deduplicate_snippets(decisions)[:5]
    discoveries = _deduplicate_snippets(discoveries)[:5]
    feedback = _deduplicate_snippets(feedback)[:5]
    lessons = _deduplicate_snippets(lessons)[:5]
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def make_candidate(kind, text, source):
        entry = {
            'type': kind,
            'summary': text[:160],
            'context': text,
            'tags': [kind, 'session'],
            'source': source,
            'source_session': session_id,
            'project': project,
            'scope': 'global' if kind in {'feedback', 'preference', 'pattern'} else 'project',
            'created': now,
            'source_refs': [{'session_id': session_id, 'section': kind}],
            'evidence': [{'type': 'transcript_excerpt', 'text': text[:300]}],
            'confidence': 0.6 if source == 'user_feedback' else 0.5,
        }
        return normalize_candidate(entry)

    # Infer a conservative episode outcome from explicit verification/result language.
    outcome_status = 'unknown'
    outcome_summary = ''
    for text in [*lessons, *tasks, *discoveries]:
        lowered = text.lower()
        if any(word in lowered for word in ('failed', 'failure', '失败', '错误', 'blocked', '阻塞')):
            outcome_status = 'failure'
            outcome_summary = text[:300]
            break
        if any(word in lowered for word in ('verified', 'passed', 'success', '完成', '成功', '已处理')):
            outcome_status = 'success'
            outcome_summary = text[:300]
    outcomes = [{'status': outcome_status, 'summary': outcome_summary, 'source': 'heuristic'}]

    candidates = []
    candidates.extend(make_candidate('decision', text, 'session') for text in decisions)
    candidates.extend(make_candidate('pattern', text, 'session') for text in discoveries)
    candidates.extend(make_candidate('feedback', text, 'user_feedback') for text in feedback)
    candidates.extend(make_candidate('lesson', text, 'session') for text in lessons)

    return {
        'sessionId': session_id,
        'date': now[:10],
        'project': project,
        'tasks_completed': _deduplicate_snippets(tasks)[:10],
        'key_decisions': decisions,
        'discoveries': discoveries,
        'user_feedback': feedback,
        'outcomes': _deduplicate_snippets(outcomes)[:5],
        'lessons_learned': lessons,
        'candidates': candidates,
        'entries_count': len(recent),
        'auto_generated': True
    }


def _extract_text_from_entry(entry):
    """Extract human-readable text from a JSONL transcript entry."""
    if isinstance(entry, str):
        return entry
    if 'message' in entry:
        msg = entry['message']
        if isinstance(msg.get('content'), str):
            return msg['content']
        elif isinstance(msg.get('content'), list):
            parts = []
            for block in msg['content']:
                if isinstance(block, dict) and block.get('type') == 'text':
                    parts.append(block.get('text', ''))
            return ' '.join(parts)
    if 'text' in entry:
        return entry['text']
    if 'content' in entry and isinstance(entry['content'], str):
        return entry['content']
    return ''


def _deduplicate_snippets(snippets):
    """Remove near-duplicate snippets using Jaccard similarity."""
    if len(snippets) <= 1:
        return snippets

    kept = []
    for s in snippets:
        s_words = set(extract_keywords(s))
        is_dup = False
        for k in kept:
            k_words = set(extract_keywords(k))
            if jaccard_similarity(s_words, k_words) > 0.7:
                is_dup = True
                break
        if not is_dup:
            kept.append(s)
    return kept


# ── Candidate staging ─────────────────────────────────────────────────────────

WORKING_JOURNAL = os.path.join(MEMORY_DIR, 'sessions', '_current.json')
EVENT_KINDS = {'observation', 'hypothesis', 'decision', 'action', 'result', 'verification', 'open_loop'}
SECRET_RE = re.compile(r'(api[_ -]?key|auth[_ -]?token|password|passwd|secret|private[_ -]?key|cookie|bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]{12,})', re.I)


def _load_working_journal():
    if not os.path.exists(WORKING_JOURNAL):
        return {'sessionId': None, 'started': None, 'project': None, 'goal': '', 'entries': []}
    try:
        data = load_json(WORKING_JOURNAL)
        data.setdefault('entries', [])
        return data
    except (OSError, json.JSONDecodeError):
        return {'sessionId': None, 'started': None, 'project': None, 'goal': '', 'entries': []}


def record_working_event(event, session_id='', project='', goal=''):
    content = str(event.get('content', '')).strip()
    kind = str(event.get('kind', '')).strip().lower()
    if kind not in EVENT_KINDS:
        raise ValueError('kind must be one of: ' + ', '.join(sorted(EVENT_KINDS)))
    if not content:
        raise ValueError('content is required')
    if SECRET_RE.search(content):
        raise ValueError('event rejected: possible credential or secret detected')

    journal = _load_working_journal()
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    journal['sessionId'] = journal.get('sessionId') or session_id or 'unknown'
    journal['started'] = journal.get('started') or now
    journal['project'] = journal.get('project') or project
    journal['goal'] = journal.get('goal') or goal
    entries = journal.setdefault('entries', [])
    seq = max([int(e.get('seq', 0)) for e in entries] or [0]) + 1
    item = {
        'seq': seq,
        'timestamp': now,
        'task_id': event.get('task_id', ''),
        'kind': kind,
        'content': content,
        'tool': event.get('tool', ''),
        'importance': max(0.0, min(1.0, float(event.get('importance', 0.5)))),
        'sensitivity': event.get('sensitivity', 'normal'),
        'refs': event.get('refs', []),
    }
    entries.append(item)
    budget = load_config()['token_budget'].get('session_journal_max', 3000)
    while entries and sum(estimate_tokens(str(e)) for e in entries) > budget:
        low = min(range(len(entries)), key=lambda i: (entries[i].get('importance', 0), entries[i].get('seq', 0)))
        entries.pop(low)
    atomic_write_text(WORKING_JOURNAL, json.dumps(journal, indent=2, ensure_ascii=False) + chr(10))
    return {'status': 'recorded', 'seq': seq, 'entries': len(entries), 'truncated': len(entries) < seq}


def infer_outcome(entries):
    for event in reversed(entries):
        text = str(event.get('content', '')).lower()
        if event.get('kind') == 'verification' and any(w in text for w in ('pass', 'passed', 'success', '成功', '通过', 'verified', '验证通过')):
            return {'status': 'success', 'summary': event.get('content', ''), 'source_event_seq': event.get('seq')}
        if any(w in text for w in ('fail', 'failed', 'failure', '失败', '错误', 'blocked', '阻塞')):
            return {'status': 'failure', 'summary': event.get('content', ''), 'source_event_seq': event.get('seq')}
    return {'status': 'unknown', 'summary': '', 'source_event_seq': None}


def build_episode(session_id='', project='', task='', outcome=None, confidence=0.0, evidence=None):
    journal = _load_working_journal()
    session_id = session_id or journal.get('sessionId') or 'unknown'
    if outcome is None:
        outcome = infer_outcome(journal.get('entries', []))
    entries = journal.get('entries', [])
    evidence = evidence or [{'type': 'working_event', 'event_seq': e.get('seq')} for e in entries]

    return {
        'episode_id': 'episode-' + hashlib.sha256(str(session_id or journal.get('sessionId')).encode('utf-8')).hexdigest()[:16],
        'session_id': session_id or journal.get('sessionId'),
        'project': project or journal.get('project'),
        'task': task or journal.get('goal', ''),
        'started': journal.get('started'),
        'ended': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'events': entries,
        'outcome': outcome or {'status': 'unknown', 'summary': ''},
        'lessons': [e.get('content') for e in entries if e.get('kind') == 'verification'],
        'open_loops': [e for e in entries if e.get('kind') == 'open_loop'],
        'confidence': max(0.0, min(1.0, float(confidence))),
        'evidence': evidence or [],
    }


def working_state(limit=20):
    journal = _load_working_journal()
    entries = journal.get('entries', [])[-max(1, min(int(limit), 100)):]
    return {
        'sessionId': journal.get('sessionId'),
        'project': journal.get('project'),
        'goal': journal.get('goal', ''),
        'entries': entries,
        'open_loops': [e for e in entries if e.get('kind') == 'open_loop'],
    }


def normalize_candidate(candidate, source_refs=None):
    """Apply the semantic/procedural candidate schema without changing legacy fields."""
    item = dict(candidate)
    item.setdefault('kind', 'procedural' if item.get('type') in {'lesson', 'pattern'} else 'semantic')
    item.setdefault('confidence', 0.5)
    item.setdefault('evidence', [])
    item.setdefault('source_refs', source_refs or [])
    item.setdefault('validity', {'state': 'active'})
    item.setdefault('salience', {'total': 0.0, 'why': []})
    item['confidence'] = max(0.0, min(1.0, float(item['confidence'])))
    item['id'] = stable_entry_id(item)
    return item


def stage_candidates(candidates, scope='global'):
    existing = load_json(CANDIDATES_FILE)
    staged = existing.get('candidates', []) if isinstance(existing, dict) else []
    by_id = {stable_entry_id(item): item for item in staged}
    added = 0
    for candidate in candidates:
        candidate = normalize_candidate(candidate)
        candidate.setdefault('scope', scope)
        candidate['id'] = stable_entry_id(candidate)
        if candidate['id'] not in by_id:
            by_id[candidate['id']] = candidate
            added += 1
    result = {'candidates': list(by_id.values()), '_comment': 'Staged memory candidates pending evaluation.'}
    save_json(CANDIDATES_FILE, result)
    return {'added': added, 'total': len(result['candidates'])}


# ── Evaluation ───────────────────────────────────────────────────────────────

def evaluate_candidates(candidates, scope="global"):
    """
    Score all staged candidates and classify them.
    Returns {promoted: [...], archived: [...], discarded: [...]}.
    """
    config = load_config()
    thresholds = config['scoring']['thresholds']
    existing = get_existing_memories(scope)

    promoted = []
    archived = []
    discarded = []

    for entry in candidates:
        score = score_memory(entry, existing, config)
        entry['_score'] = round(score, 1)

        if score >= thresholds['promote']:
            entry['_decision'] = 'promoted'
            promoted.append(entry)
        elif score >= thresholds['archive']:
            entry['_decision'] = 'archived'
            archived.append(entry)
        else:
            entry['_decision'] = 'discarded'
            discarded.append(entry)

    # Update scoring history
    _update_scoring_history(promoted + archived + discarded)

    return {
        'promoted': promoted,
        'archived': archived,
        'discarded': discarded,
        'summary': {
            'total': len(candidates),
            'promoted_count': len(promoted),
            'archived_count': len(archived),
            'discarded_count': len(discarded)
        }
    }


def _update_scoring_history(entries):
    """Append evaluation results to _system/scoring.json."""
    scores = load_json(SCORING_FILE)
    if 'scores' not in scores:
        scores['scores'] = {}

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    for entry in entries:
        eid = entry.get('id', entry.get('summary', 'unknown')[:40])
        if eid not in scores['scores']:
            scores['scores'][eid] = {'current': 0, 'history': []}

        scores['scores'][eid]['current'] = entry.get('_score', 0)
        scores['scores'][eid]['history'].append({
            'date': today,
            'score': entry.get('_score', 0),
            'decision': entry.get('_decision', 'unknown')
        })

    save_json(SCORING_FILE, scores)


# ── Promotion ────────────────────────────────────────────────────────────────

def promote_memory(entry, scope="global", target_file="lessons.md"):
    """
    Write a memory entry to the target markdown file.
    Appends as a new section, updates index and stats.
    """
    config = load_config()

    if scope == "global":
        memory_root = GLOBAL_DIR
        index_data = load_json(INDEX_FILE)
    else:
        memory_root = os.path.join(MEMORY_DIR, "..", "memory")
        index_data = {'entries': []}

    file_path = os.path.join(memory_root, target_file)

    # Build markdown section
    entry_type = entry.get('type', 'lesson')
    title = entry.get('summary', 'New Memory')[:80]
    context = entry.get('context', '')
    tags = entry.get('tags', [])
    score = entry.get('_score', 70)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    tag_str = ', '.join(tags)
    section = f"""

## {title}

- **Type**: {entry_type}
- **Tags**: {tag_str}
- **Added**: {now}
- **Score**: {score}

{context}

"""

    # Append only if this stable entry is not already indexed.
    existing_ids = {e['id'] for e in index_data.get('entries', [])}
    entry_id = stable_entry_id(entry)
    if entry_id not in existing_ids:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if os.path.exists(file_path):
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(section)
        else:
            atomic_write_text(file_path, f"# {target_file.replace('.md', '').replace('-', ' ').title()}\n" + section)

    # Update index
    entry_id = stable_entry_id(entry)
    token_count = estimate_tokens(section)

    existing_ids = {e['id'] for e in index_data.get('entries', [])}

    if entry_id not in existing_ids:
        index_data.setdefault('entries', []).append({
            'id': entry_id,
            'summary': title,
            'context': context,
            'tags': tags,
            'scope': scope,
            'file': f"{'global' if scope == 'global' else 'project'}/{target_file}",
            'type': entry_type,
            'tags': tags,
            'score': score,
            'created': now,
            'modified': now,
            'tokens': token_count,
            'generation': entry.get('_generation', 0)
        })

    save_json(INDEX_FILE, index_data)

    # Log
    log_file = COMPRESSION_LOG if scope == "global" else os.path.join(memory_root, "..", "_system", "compression-log.json")
    _log_compression(log_file, 'promote',
                     f'{entry_id} — {title[:60]}',
                     token_before=0, token_after=token_count)

    return {'status': 'promoted', 'file': file_path, 'tokens': token_count}


def _log_compression(log_file, op_type, description, token_before=0, token_after=0):
    """Append to compression log."""
    log = load_json(log_file)
    log.setdefault('operations', []).append({
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'operation': op_type,
        'description': description,
        'token_before': token_before,
        'token_after': token_after
    })
    save_json(log_file, log)


# ── Compression ──────────────────────────────────────────────────────────────

def compress_memories(scope="global", dry_run=False):
    """
    3-level compression: L1 dedup → L2 abstraction → L3 reorganization.
    Triggered per thresholds in .memory-config.json.
    """
    config = load_config()
    triggers = config['compression']['triggers']
    sim_config = config['compression']['similarity']

    memories = read_memory_files(scope)
    if not memories:
        return {'operations': [], 'message': 'No memory files to compress.'}

    operations = []

    # --- L1: Deduplication ---
    for mem in memories:
        sections = _split_sections(mem['content'])
        if len(sections) <= triggers['deduplication']['entries_per_category']:
            continue

        merged_sections, dedup_ops = _dedup_sections(
            sections,
            threshold=sim_config['jaccard_threshold']
        )

        if dedup_ops:
            if not dry_run:
                _write_memory_file(mem['file'], merged_sections)
            operations.extend(dedup_ops)

    # --- L2: Abstraction ---
    for mem in memories:
        sections = _split_sections(mem['content'])
        if len(sections) < triggers['abstraction']['entries_per_category']:
            continue

        abstracted_sections, abstract_ops = _abstract_sections(
            sections,
            min_similar=triggers['abstraction']['min_similar'],
            shared_tags_min=sim_config['shared_tags_min']
        )

        if abstract_ops:
            if not dry_run:
                _write_memory_file(mem['file'], abstracted_sections)
            operations.extend(abstract_ops)

    # --- L3: Reorganization ---
    if len(memories) >= triggers['reorganization']['entries_per_category']:
        reorg_ops = _reorganize_categories(memories, scope, dry_run)
        operations.extend(reorg_ops)

    # Log
    if not dry_run and operations:
        for op in operations:
            _log_compression(
                COMPRESSION_LOG,
                op.get('type', 'unknown'),
                op.get('description', ''),
                op.get('token_before', 0),
                op.get('token_after', 0)
            )

    return {
        'operations': operations,
        'dry_run': dry_run,
        'summary': f'{len(operations)} operations {"(dry run)" if dry_run else "applied"}'
    }


def _split_sections(content):
    """Split markdown into sections by ## headers."""
    sections = []
    current_title = '_preamble'
    current_lines = []

    for line in content.split('\n'):
        if line.startswith('## '):
            if current_lines:
                sections.append({
                    'title': current_title,
                    'content': '\n'.join(current_lines).strip()
                })
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            'title': current_title,
            'content': '\n'.join(current_lines).strip()
        })

    return sections


def _dedup_sections(sections, threshold=0.7):
    """L1 dedup: merge sections with Jaccard similarity > threshold."""
    operations = []
    merged = []
    used = set()

    for i, sec_a in enumerate(sections):
        if i in used:
            continue
        cluster = [sec_a]
        cluster_ids = [i]

        for j, sec_b in enumerate(sections):
            if j <= i or j in used:
                continue
            sim = jaccard_similarity(
                extract_keywords(sec_a['content']),
                extract_keywords(sec_b['content'])
            )
            if sim > threshold:
                cluster.append(sec_b)
                cluster_ids.append(j)

        if len(cluster) > 1:
            # Merge: keep longest, add unique lines from others
            best = max(cluster, key=lambda s: len(s['content']))
            unique_lines = _get_unique_lines(best, cluster)
            merged_content = best['content'] + '\n\n' + '\n'.join(unique_lines)
            merged.append({'title': best['title'], 'content': merged_content})

            token_before = sum(estimate_tokens(s['content']) for s in cluster)
            token_after = estimate_tokens(merged_content)
            operations.append({
                'type': 'dedup',
                'description': f'Merged {len(cluster)} similar sections under "{best["title"][:60]}"',
                'token_before': token_before,
                'token_after': token_after
            })
            used.update(cluster_ids)
        else:
            merged.append(sec_a)
            used.add(i)

    # Add remaining unprocessed
    for i, sec in enumerate(sections):
        if i not in used:
            merged.append(sec)

    return merged, operations


def _get_unique_lines(best, cluster):
    """Get lines from other cluster members not already in best."""
    best_lines = set(best['content'].split('\n'))
    unique = []
    for sec in cluster:
        if sec is best:
            continue
        for line in sec['content'].split('\n'):
            line = line.strip()
            if line and line not in best_lines:
                unique.append(line)
                best_lines.add(line)
    return unique[:10]  # Cap at 10 unique lines to avoid bloating


def _abstract_sections(sections, min_similar=3, shared_tags_min=2):
    """L2 abstraction: group similar sections into principles."""
    operations = []
    result = list(sections)

    if len(sections) < min_similar:
        return result, operations

    # Find groups by tag similarity
    # (Without explicit tags in sections, use keyword overlap)
    groups = []
    used = set()

    for i, sec_a in enumerate(sections):
        if i in used:
            continue
        group = [i]
        kw_a = set(extract_keywords(sec_a['content']))

        for j, sec_b in enumerate(sections):
            if j <= i or j in used:
                continue
            kw_b = set(extract_keywords(sec_b['content']))
            if len(kw_a & kw_b) >= shared_tags_min:
                group.append(j)

        if len(group) >= min_similar:
            groups.append(group)
            used.update(group)

    for group in groups:
        group_sections = [sections[i] for i in group]
        # Extract common theme from titles and keywords
        all_kw = []
        for s in group_sections:
            all_kw.extend(extract_keywords(s['title']))

        # Most frequent keywords as theme
        from collections import Counter
        common_kw = [k for k, c in Counter(all_kw).most_common(3) if c >= 2]
        theme = ' & '.join(common_kw) if common_kw else group_sections[0]['title']

        # Build principle
        titles = [s['title'] for s in group_sections]
        principle = (
            f"## Consolidated: {theme}\n\n"
            f"_Derived from {len(group_sections)} related entries:_\n"
            + '\n'.join(f'- {t[:120]}' for t in titles) +
            f"\n\n**Key pattern**: When working with {theme}, "
            f"similar patterns emerged across {len(group_sections)} entries. "
            f"Consider the common thread connecting these instances.\n"
        )

        token_before = sum(estimate_tokens(s['content']) for s in group_sections)
        token_after = estimate_tokens(principle)

        # Replace all group entries with one principle
        for idx in sorted(group, reverse=True):
            result.pop(idx)
        result.append({'title': f'Consolidated: {theme}', 'content': principle})

        reduction_pct = round((1 - token_after / token_before) * 100) if token_before > 0 else 0
        operations.append({
            'type': 'abstract',
            'description': f'Abstracted {len(group_sections)} entries into principle: {theme[:60]}',
            'token_before': token_before,
            'token_after': token_after
        })

    return result, operations


def _reorganize_categories(memories, scope, dry_run):
    """L3 reorganization: check category sizes, propose merges/splits."""
    operations = []
    small_cats = []
    large_cats = []

    for mem in memories:
        sections = _split_sections(mem['content'])
        entry_count = len([s for s in sections if s['title'] != '_preamble'])

        if entry_count < 3:
            small_cats.append((mem, entry_count))
        elif entry_count > 20:
            large_cats.append((mem, entry_count))

    for mem, count in small_cats:
        operations.append({
            'type': 'reorg_warning',
            'description': f'Small category: {mem["name"]} ({count} entries) — consider merging',
            'token_before': mem['tokens'],
            'token_after': mem['tokens']
        })

    for mem, count in large_cats:
        operations.append({
            'type': 'reorg_warning',
            'description': f'Large category: {mem["name"]} ({count} entries) — consider splitting',
            'token_before': mem['tokens'],
            'token_after': mem['tokens']
        })

    return operations


def _write_memory_file(path, sections):
    """Reconstruct markdown from sections and write to file."""
    content = ""
    for sec in sections:
        if sec['title'] == '_preamble':
            content += sec['content'] + '\n'
        else:
            content += f"## {sec['title']}\n{sec['content']}\n\n"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content.rstrip() + '\n')


# ── Stats ────────────────────────────────────────────────────────────────────

def recalculate_stats(scope="global", cwd=''):
    """Recalculate token counts while preserving the complete stats schema."""
    config = load_config()
    budgets = config['token_budget']

    memories = read_memory_files(scope, cwd)
    total_tokens = sum(m['tokens'] for m in memories)
    total_files = len(memories)

    categories = {}
    for mem in memories:
        cat = mem['name'].replace('.md', '')
        budget_key = {
            'preferences': 'preferences',
            'patterns': 'patterns',
            'lessons': 'lessons_global',
            'goals': 'goals',
            'architecture': 'architecture',
            'reference': 'reference',
            'status': 'status'
        }.get(cat, 'lessons_global')

        categories[cat] = {
            'files': 1,
            'tokens': mem['tokens'],
            'budget': budgets.get('per_memory_file_max', {}).get(budget_key, 2000)
        }

    stats = load_json(STATS_FILE)
    stats['global'] = {
        'total_files': total_files,
        'total_tokens': total_tokens,
        'budget': budgets['global_total_max'],
        'usage_pct': round(total_tokens / budgets['global_total_max'] * 100, 1) if budgets['global_total_max'] > 0 else 0,
        'categories': categories
    }

    # Preserve session counters
    if 'sessions' not in stats:
        stats['sessions'] = {
            'total_archived': 0,
            'sessions_since_consolidation': 0,
            'last_consolidation': datetime.now(timezone.utc).strftime('%Y-%m-%d')
        }

    if 'performance' not in stats:
        stats['performance'] = {
            'estimated_tokens_saved': 0,
            'roi_ratio': 0,
            'retrieval_hits': 0,
            'retrieval_misses': 0
        }

    save_json(STATS_FILE, stats)
    return stats


# ── Decay ─────────────────────────────────────────────────────────────────────

def apply_decay(scope="global"):
    """Apply age-based score decay to all memories."""
    import math
    config = load_config()

    index = load_json(INDEX_FILE)
    today = datetime.now(timezone.utc)
    decay_lambda = config['consolidation']['age_decay_lambda']
    retirement_threshold = config['scoring']['thresholds']['retirement_decay']
    unreferenced_days = config['consolidation']['unreferenced_decay_days']

    updated = []
    retired = []

    for entry in index.get('entries', []):
        modified_str = entry.get('modified', entry.get('created', ''))
        try:
            modified = datetime.fromisoformat(modified_str.replace('Z', '+00:00'))
            age_days = (today - modified).days
        except (ValueError, TypeError):
            age_days = 0

        # Age decay
        decay_factor = math.exp(-decay_lambda * age_days)
        current_score = entry.get('score', 70)
        new_score = round(current_score * decay_factor, 1)

        entry['score'] = new_score
        entry['_age_days'] = age_days
        entry['_decayed'] = True

        if new_score < retirement_threshold and age_days > unreferenced_days:
            entry['validity'] = {
                'state': 'retired',
                'reason': 'age_decay',
                'retired_at': today.strftime('%Y-%m-%dT%H:%M:%SZ')
            }
            entry['retired'] = True
            retired.append(entry)
            updated.append(entry)
        else:
            updated.append(entry)

    index['entries'] = updated
    save_json(INDEX_FILE, index)

    # Log retirements
    if retired:
        _log_compression(
            COMPRESSION_LOG,
            'decay_retire',
            f'Retired {len(retired)} memories due to score decay',
            token_before=len(retired),
            token_after=0
        )

    return {
        'decayed': len(updated),
        'retired': len(retired),
        'retired_ids': [e.get('id') for e in retired]
    }


def retrieve_memories(scope='global', query='', cwd=''):
    """Return bounded memories ranked by cue match plus stored quality metadata."""
    memories = read_memory_files(scope, cwd)
    index = load_json(INDEX_FILE).get('entries', [])
    metadata = {str(e.get('file', '')).replace('global/', '').replace('project/', ''): e for e in index}
    terms = set(extract_keywords(' '.join([query, cwd])))
    ranked = []
    for mem in memories:
        meta = metadata.get(mem['name'], {})
        cue = len(terms & set(extract_keywords(mem['name'] + ' ' + mem['content'])))
        quality = float(meta.get('score', 0)) / 100.0
        recency = float(meta.get('recall_count', 0)) * 0.02
        score = cue + quality + recency
        if not terms:
            score = 1.0 + quality + recency
        ranked.append((score, mem, meta))
    ranked.sort(key=lambda item: (-item[0], item[1]['name']))
    limit = load_config()['token_budget']['content_per_load_max']
    selected, used = [], 0
    for score, mem, meta in ranked:
        if score <= 0 or used + mem['tokens'] > limit:
            continue
        selected.append({'file': mem['name'], 'content': mem['content'], 'score': round(score, 3), 'metadata': meta})
        used += mem['tokens']
    return {'matches': selected, 'tokens': used, 'query': query, 'scope': scope}


def record_recall_outcome(file_name, outcome, task_id='', evidence=''):
    """Record whether a recalled memory helped, was irrelevant, or was wrong."""
    allowed = {'helpful', 'irrelevant', 'wrong', 'confirmed'}
    if outcome not in allowed:
        raise ValueError('outcome must be one of: ' + ', '.join(sorted(allowed)))
    data = load_json(INDEX_FILE)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    updated = 0
    for entry in data.get('entries', []):
        normalized = str(entry.get('file', '')).replace('global/', '').replace('project/', '')
        if normalized == file_name or entry.get('id') == file_name:
            history = entry.setdefault('recall_history', [])
            history.append({'outcome': outcome, 'task_id': task_id, 'evidence': evidence, 'at': now})
            entry['last_recall_outcome'] = outcome
            if outcome in {'helpful', 'confirmed'}:
                entry['confidence'] = min(1.0, float(entry.get('confidence', 0.5)) + 0.05)
            elif outcome == 'wrong':
                entry['confidence'] = max(0.0, float(entry.get('confidence', 0.5)) - 0.15)
                entry.setdefault('validity', {})['state'] = 'disputed'
            updated += 1
    if updated:
        save_json(INDEX_FILE, data)
    return {'updated': updated, 'file': file_name, 'outcome': outcome}


def record_recall(file_name, scope='global'):
    """Record that a memory file was actually selected for context."""
    data = load_json(INDEX_FILE)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    found = 0
    for entry in data.get('entries', []):
        normalized = str(entry.get('file', '')).replace('global/', '').replace('project/', '')
        if normalized == file_name or entry.get('id') == file_name:
            entry['last_recalled_at'] = now
            entry['recall_count'] = int(entry.get('recall_count', 0)) + 1
            entry['modified'] = now[:10]
            found += 1
    if found:
        save_json(INDEX_FILE, data)
    return {'updated': found, 'file': file_name, 'timestamp': now}


def detect_conflicts(entries):
    """Conservative conflict detector: same key with materially different summaries."""
    groups = {}
    for entry in entries:
        key = entry.get('subject') or entry.get('key') or (entry.get('tags') or ['unknown'])[0]
        groups.setdefault(str(key), []).append(entry)
    conflicts = []
    for key, group in groups.items():
        summaries = {str(e.get('summary', '')).strip().lower() for e in group}
        if len(summaries) > 1 and len(group) > 1:
            conflicts.append({'key': key, 'entries': [e.get('id') for e in group], 'status': 'disputed'})
    return conflicts


def soft_retire(entry_id, reason='decay'):
    data = load_json(INDEX_FILE)
    changed = False
    for entry in data.get('entries', []):
        if entry.get('id') == entry_id:
            entry['validity'] = {'state': 'retired', 'reason': reason, 'retired_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            entry['retired'] = True
            changed = True
    if changed:
        save_json(INDEX_FILE, data)
    return {'retired': changed, 'id': entry_id, 'reason': reason}


def health_check():
    """Validate memory state and report index, JSON, and budget drift."""
    checks = []
    for path in Path(MEMORY_DIR).rglob('*.json'):
        try:
            json.loads(path.read_text(encoding='utf-8'))
            checks.append({'file': str(path), 'ok': True})
        except Exception as exc:
            checks.append({'file': str(path), 'ok': False, 'error': str(exc)})
    memories = read_memory_files('global')
    index = load_json(INDEX_FILE)
    indexed = {str(e.get('file', '')).replace('global/', '') for e in index.get('entries', [])}
    actual = {m['name'] for m in memories}
    return {
        'ok': all(c['ok'] for c in checks),
        'json_checks': checks,
        'index_missing': sorted(actual - indexed),
        'index_orphans': sorted(indexed - actual),
        'global_files': len(memories),
        'global_tokens': sum(m['tokens'] for m in memories),
        'candidate_count': len(load_json(CANDIDATES_FILE).get('candidates', [])),
    }


def rebuild_index():
    """Rebuild global index entries from actual Markdown files without deleting metadata."""
    old = load_json(INDEX_FILE)
    by_file = {str(e.get('file', '')).replace('global/', ''): e for e in old.get('entries', [])}
    entries = []
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for mem in read_memory_files('global'):
        e = dict(by_file.get(mem['name'], {}))
        e.setdefault('id', mem['name'].removesuffix('.md'))
        e['file'] = 'global/' + mem['name']
        e.setdefault('type', 'lesson')
        e.setdefault('tags', [])
        e.setdefault('score', 70)
        e.setdefault('confidence', 0.5)
        e.setdefault('validity', {'state': 'active'})
        e.setdefault('recall_count', 0)
        e.setdefault('last_recalled_at', None)
        e.setdefault('supersedes', [])
        e.setdefault('conflicts', [])
        e.setdefault('created', now)
        e['modified'] = now
        e['tokens'] = mem['tokens']
        entries.append(e)
    result = {'_comment': 'Programmatic memory index — machine-readable, for fast retrieval queries', 'scope': 'global', 'updated': now, 'entries': entries}
    save_json(INDEX_FILE, result)
    return {'rebuilt': len(entries), 'files': [e['file'] for e in entries]}


def revise_memory(entry_id, supersedes=None, reason='', state='active'):
    data = load_json(INDEX_FILE)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    changed = False
    for entry in data.get('entries', []):
        if entry.get('id') == entry_id:
            entry['validity'] = {'state': state, 'reason': reason, 'updated_at': now}
            if supersedes:
                entry['supersedes'] = supersedes if isinstance(supersedes, list) else [supersedes]
            entry['modified'] = now[:10]
            changed = True
    if changed:
        save_json(INDEX_FILE, data)
    return {'revised': changed, 'id': entry_id, 'state': state}


# ── CLI ──────────────────────────────────────────────────────────────────────


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Memory Management Core Engine')
    sub = parser.add_subparsers(dest='command', required=True)

    # summarize
    p_sum = sub.add_parser('summarize', help='Analyze transcript → session summary')
    p_sum.add_argument('--transcript', required=True)
    p_sum.add_argument('--session', required=True)
    p_sum.add_argument('--project', default='unknown')

    # evaluate
    p_eval = sub.add_parser('evaluate', help='Score candidates')
    p_eval.add_argument('--candidates', required=True)
    p_eval.add_argument('--scope', default='global')

    # compress
    p_comp = sub.add_parser('compress', help='Run compression')
    p_comp.add_argument('--scope', default='global')
    p_comp.add_argument('--dry-run', action='store_true')

    # promote
    p_prom = sub.add_parser('promote', help='Promote memory to long-term file')
    p_prom.add_argument('--entry', required=True)
    p_prom.add_argument('--scope', default='global')
    p_prom.add_argument('--file', default='lessons.md')

    # stats
    p_stats = sub.add_parser('stats', help='Recalculate token stats')
    p_stats.add_argument('--scope', default='global')

    # decay
    p_decay = sub.add_parser('decay', help='Apply age-based score decay')
    p_decay.add_argument('--scope', default='global')

    # retrieve
    p_retrieve = sub.add_parser('retrieve', help='Retrieve bounded relevant memories')
    p_retrieve.add_argument('--scope', default='global')
    p_retrieve.add_argument('--query', default='')
    p_retrieve.add_argument('--cwd', default='')

    # recall and memory maintenance
    p_recall = sub.add_parser('record-recall', help='Record selected memory usage')
    p_recall.add_argument('--file', required=True)
    p_recall.add_argument('--scope', default='global')

    p_recall_outcome = sub.add_parser('recall-outcome', help='Record whether recalled memory helped')
    p_recall_outcome.add_argument('--file', required=True)
    p_recall_outcome.add_argument('--outcome', required=True)
    p_recall_outcome.add_argument('--task-id', default='')
    p_recall_outcome.add_argument('--evidence', default='')

    p_conflicts = sub.add_parser('detect-conflicts', help='Detect conflicting memory entries')
    p_conflicts.add_argument('--entries', required=True)

    p_retire = sub.add_parser('soft-retire', help='Soft-retire an index entry')
    p_retire.add_argument('--id', required=True)
    p_retire.add_argument('--reason', default='decay')

    p_health = sub.add_parser('health-check', help='Validate memory state')

    p_rebuild = sub.add_parser('rebuild-index', help='Rebuild global memory index')

    p_revise = sub.add_parser('revise-memory', help='Update memory validity or supersession')
    p_revise.add_argument('--id', required=True)
    p_revise.add_argument('--state', default='active')
    p_revise.add_argument('--reason', default='')
    p_revise.add_argument('--supersedes', default='')

    # stage candidates from a summary JSON
    p_stage = sub.add_parser('stage-candidates', help='Stage candidates from summary JSON')
    p_stage.add_argument('--summary', required=True)

    # episode
    p_episode = sub.add_parser('build-episode', help='Build an episodic record from working memory')
    p_episode.add_argument('--session', default='')
    p_episode.add_argument('--project', default='')
    p_episode.add_argument('--task', default='')
    p_episode.add_argument('--outcome', default='')
    p_episode.add_argument('--confidence', type=float, default=0.0)

    # working memory
    p_event = sub.add_parser('record-event', help='Record a structured working-memory event')
    p_event.add_argument('--event', required=True)
    p_event.add_argument('--session', default='')
    p_event.add_argument('--project', default='')
    p_event.add_argument('--goal', default='')

    p_state = sub.add_parser('working-state', help='Read bounded working memory')
    p_state.add_argument('--limit', type=int, default=20)

    args = parser.parse_args()

    try:
        if args.command == 'summarize':
            result = summarize_session(args.transcript, args.session, args.project)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'evaluate':
            if args.candidates == '-':
                raw = sys.stdin.read()
            else:
                raw = args.candidates
            candidates = json.loads(raw)
            if isinstance(candidates, dict) and 'candidates' in candidates:
                candidates = candidates['candidates']
            if not isinstance(candidates, list):
                candidates = [candidates]
            result = evaluate_candidates(candidates, args.scope)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'compress':
            result = compress_memories(args.scope, args.dry_run)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'promote':
            if args.entry == '-':
                raw = sys.stdin.read()
            else:
                raw = args.entry
            entry = json.loads(raw)
            result = promote_memory(entry, args.scope, args.file)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'stats':
            result = recalculate_stats(args.scope)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'decay':
            result = apply_decay(args.scope)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'retrieve':
            result = retrieve_memories(args.scope, args.query, args.cwd)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'record-recall':
            result = record_recall(args.file, args.scope)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'recall-outcome':
            result = record_recall_outcome(args.file, args.outcome, args.task_id, args.evidence)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'detect-conflicts':
            raw = sys.stdin.read() if args.entries == '-' else args.entries
            result = detect_conflicts(json.loads(raw))
            print(json.dumps({'conflicts': result}, indent=2, ensure_ascii=False))

        elif args.command == 'soft-retire':
            result = soft_retire(args.id, args.reason)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'health-check':
            print(json.dumps(health_check(), indent=2, ensure_ascii=False))

        elif args.command == 'rebuild-index':
            print(json.dumps(rebuild_index(), indent=2, ensure_ascii=False))

        elif args.command == 'revise-memory':
            supersedes = [x for x in args.supersedes.split(',') if x] if args.supersedes else None
            result = revise_memory(args.id, supersedes, args.reason, args.state)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'stage-candidates':
            raw = sys.stdin.read() if args.summary == '-' else args.summary
            summary = json.loads(raw)
            result = stage_candidates(summary.get('candidates', []))
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'build-episode':
            outcome = json.loads(args.outcome) if args.outcome else None
            result = build_episode(args.session, args.project, args.task, outcome, args.confidence)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'record-event':
            raw = sys.stdin.read() if args.event == '-' else args.event
            result = record_working_event(json.loads(raw), args.session, args.project, args.goal)
            print(json.dumps(result, indent=2, ensure_ascii=False))

        elif args.command == 'working-state':
            print(json.dumps(working_state(args.limit), indent=2, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({'error': str(e), 'command': args.command}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
