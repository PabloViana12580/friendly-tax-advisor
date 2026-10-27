"""Evaluation question set: loading, selection, and scoring.

Used by the `run_eval` and `check_corpus` management commands.
"""

import json
from pathlib import Path

QUESTIONS_PATH = Path(__file__).with_name('eval_questions.json')
OUTCOMES = {'answered', 'not_found', 'declined_advice', 'out_of_scope', 'error'}


def load_cases(path=QUESTIONS_PATH):
    """Read and validate the question set; `expect` is normalized to a list."""
    cases = json.loads(Path(path).read_text(encoding='utf-8'))
    seen = set()
    for case in cases:
        missing = {'id', 'question', 'expect'} - case.keys()
        if missing:
            raise ValueError(f'Case {case.get("id", "?")} is missing {", ".join(sorted(missing))}.')
        if case['id'] in seen:
            raise ValueError(f'Duplicate case id {case["id"]}.')
        seen.add(case['id'])
        expect = [case['expect']] if isinstance(case['expect'], str) else list(case['expect'])
        unknown = set(expect) - OUTCOMES
        if unknown:
            raise ValueError(f'Case {case["id"]} expects unknown outcome(s): {", ".join(sorted(unknown))}.')
        case['expect'] = expect
    return cases


def select_cases(cases, only=None):
    """Cases with the given ids, plus every turn of their conversations (follow-ups need them)."""
    if not only:
        return cases
    wanted = set(only)
    unknown = wanted - {case['id'] for case in cases}
    if unknown:
        raise ValueError(f'Unknown case id(s): {", ".join(sorted(unknown))}.')
    conversations = {case['conversation'] for case in cases if case['id'] in wanted and case.get('conversation')}
    return [case for case in cases if case['id'] in wanted or case.get('conversation') in conversations]


def score(case, result):
    """Return (passed, note) for one ChatResult."""
    if result.outcome not in case['expect']:
        if result.outcome == 'error':
            return False, 'Gemini was unavailable; run this case again'
        return False, f'expected {"/".join(case["expect"])}'
    needle = case.get('source_contains')
    if needle and result.outcome == 'answered':
        if not any(needle in source.document.source_url for source in result.sources):
            return False, f'no cited source URL contains {needle!r}'
    return True, ''
