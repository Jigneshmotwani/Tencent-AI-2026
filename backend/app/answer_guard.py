"""Deterministic post-generation check for chat answers (hallucination guard, no model call).

The prompt tells the model to use only the supplied evidence and cite every factual claim. This verifies it: a factual
sentence must either cite a valid evidence label of this run, or restate wording that actually appears in the evidence
(>= 60% of its distinctive terms). Anything else is treated as filled in from general knowledge and is removed. If nothing
supported remains, the answer is replaced by the fixed not-found text.
"""
import re

from .knowledge import _stems
from .workflows.guardrails import CITE, _SENT, _is_claim, _labels_in

NOT_FOUND = re.compile(r"^\s*I couldn.t find information", re.I)
OVERLAP = 0.6


def check_answer(answer: str, evidence: list[dict]) -> dict:
    """-> {text, removed: [sentence...], supported: int, not_found: bool}. `evidence` items need label/title/text."""
    if NOT_FOUND.match(answer or ''):
        return {'text': answer, 'removed': [], 'supported': 0, 'not_found': True}
    valid = {e['label'] for e in evidence}
    known = set()
    for e in evidence:
        known |= _stems((e.get('title') or '') + ' ' + (e.get('text') or ''))
    lines, removed, supported = [], [], 0
    for line in (answer or '').split('\n'):
        if not line.strip():
            lines.append(line); continue
        kept = []
        for sentence in _SENT.split(line):
            if not _is_claim(sentence):
                kept.append(sentence); continue
            if any(l in valid for l in _labels_in(sentence)):
                kept.append(sentence); supported += 1; continue
            terms = _stems(CITE.sub('', sentence))
            if len(terms) >= 3 and len(terms & known) / len(terms) >= OVERLAP:
                kept.append(sentence); supported += 1
            else:
                removed.append(sentence.strip()[:200])
        joined = ' '.join(s for s in kept if s.strip())
        if joined.strip():
            lines.append(joined)
    return {'text': '\n'.join(lines).strip(), 'removed': removed, 'supported': supported, 'not_found': False}
