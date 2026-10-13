"""Deterministic guardrails (T04). W2-owned. LLM-assisted checks live in nodes.py and are optional.

PII masking: regex + checksum (SG NRIC/FIN incl. M-series, UEN formats, IBAN mod-97, card Luhn, phone, email). Values are
replaced with stable per-run placeholders like [NRIC_1]; the mapping lives in memory only (RunCtx) and is dropped when the
run ends. Design choice: approved action payloads are executed exactly as previewed, so placeholders are NOT unmasked into
writes (preview == execution; approvers see placeholders). Nothing in this module persists values.

Injection: instruction-like spans in retrieved text are replaced with QUARANTINE.
Grounding: every factual sentence must cite valid evidence labels of this run, either inline [E#] or via the enclosing
object's evidence_ids. Unsupported claims are removed (strip) and reported.
"""
from __future__ import annotations

import copy
import re

PII_KINDS = ('nric', 'uen', 'iban', 'card', 'phone', 'email')
PLACEHOLDER = re.compile(r'\[(?:NRIC|UEN|IBAN|CARD|PHONE|EMAIL)_\d+\]')
QUARANTINE = '[QUARANTINED instruction-like text]'

# ---------------------------------------------------------------- PII detectors
_NRIC = re.compile(r'(?<![A-Za-z0-9])([STFGM]\d{7}[A-Z])(?![A-Za-z0-9])', re.I)
_NRIC_PARTS = re.compile(r'([STFGM])(\d{7})([A-Z])')
_UEN = re.compile(r'(?<![A-Za-z0-9])(\d{8}[A-Z]|(?:19|20)\d{7}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])(?![A-Za-z0-9])')
_UEN_TYPES = set('LP LL FC PF RF MQ MM NB CC CS MB FM GS GA GB DP CP NR CM CD MD HS VH CH MH CL XL CX RP TU TC FB FN PA PB SS MC SM'.split())
_IBAN = re.compile(r'(?<![A-Za-z0-9])([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})(?![A-Za-z0-9])')
_CARD = re.compile(r'(?<![\d-])(\d(?:[ -]?\d){12,18})(?![\d-])')
_EMAIL = re.compile(r'(?<![\w.+-])([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24})(?![\w-])')
_PHONE = re.compile(r'(?<![\w+])((?:\+65[ -]?)?[689]\d{3}[ -]?\d{4}|\+\d{1,3}[ -]?\(?\d{1,4}\)?(?:[ -]?\d{2,4}){2,4})(?![\w])')


def nric_valid(value: str) -> bool:
    m = _NRIC_PARTS.fullmatch(value.upper())
    if not m:
        return False
    prefix, digits, check = m.group(1), m.group(2), m.group(3)
    total = sum(int(d) * w for d, w in zip(digits, (2, 7, 6, 5, 4, 3, 2)))
    offset = {'S': 0, 'T': 4, 'F': 0, 'G': 4, 'M': 3}[prefix]
    table = {'S': 'JZIHGFEDCBA', 'T': 'JZIHGFEDCBA', 'F': 'XWUTRQPNMLK', 'G': 'XWUTRQPNMLK', 'M': 'XWUTRQPNJLK'}[prefix]
    return table[(total + offset) % 11] == check


def uen_valid(value: str) -> bool:
    v = value.upper()
    if re.fullmatch(r'\d{8}[A-Z]', v):
        return True  # ROB business number format (check letter algorithm is not published)
    if re.fullmatch(r'(?:19|20)\d{7}[A-Z]', v):
        return 1800 < int(v[:4]) <= 2100
    if re.fullmatch(r'[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]', v):
        return v[3:5] in _UEN_TYPES
    return False


def iban_valid(value: str) -> bool:
    v = value.replace(' ', '').upper()
    if not 15 <= len(v) <= 34 or not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]+', v):
        return False
    digits = ''.join(str(int(c, 36)) for c in v[4:] + v[:4])
    return int(digits) % 97 == 1


def luhn_valid(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0


# (kind, regex, validator). Order matters: longer/structured identifiers first so phones never eat card digits.
DETECTORS = [
    ('email', _EMAIL, lambda v: True),
    ('iban', _IBAN, iban_valid),
    ('card', _CARD, luhn_valid),
    ('nric', _NRIC, nric_valid),
    ('uen', _UEN, uen_valid),
    ('phone', _PHONE, lambda v: sum(c.isdigit() for c in v) >= 8),
]


class Masker:
    """Stable per-run placeholders. In-memory only."""

    def __init__(self):
        self._map: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, re.sub(r'[\s-]', '', value).upper())
        if key not in self._map:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._map[key] = f'[{kind.upper()}_{self._counts[kind]}]'
        return self._map[key]

    def mask(self, text: str, kinds) -> tuple[str, dict]:
        counts: dict[str, int] = {}
        if not text:
            return text, counts
        kinds = set(kinds or ())
        for kind, regex, valid in DETECTORS:
            if kind not in kinds:
                continue

            def sub(m, kind=kind, valid=valid):
                value = m.group(1)
                if PLACEHOLDER.fullmatch(value) or not valid(value):
                    return m.group(0)
                counts[kind] = counts.get(kind, 0) + 1
                return m.group(0).replace(value, self._placeholder(kind, value))
            text = regex.sub(sub, text)
        return text, counts

    def mask_obj(self, value, kinds, counts: dict | None = None):
        counts = {} if counts is None else counts
        if isinstance(value, str):
            out, c = self.mask(value, kinds)
            for k, n in c.items(): counts[k] = counts.get(k, 0) + n
            return out
        if isinstance(value, list):
            return [self.mask_obj(v, kinds, counts) for v in value]
        if isinstance(value, dict):
            return {k: self.mask_obj(v, kinds, counts) for k, v in value.items()}
        return value


# ---------------------------------------------------------------- prompt injection
INJECTION_PATTERNS = [re.compile(p, re.I) for p in (
    r'\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|system|your)\b[^.\n]{0,30}\b(instructions?|prompts?|rules|directions|guidelines|policies)\b',
    r'\byou are now\b', r'\bfrom now on,? (you|the assistant|the model)\b',
    r'\b(act|behave) as (an? )?(admin|administrator|system|developer|unrestricted|jailbroken)\b',
    r'\bpretend (to be|you are)\b',
    r'\b(new|updated|revised|override) (system )?(instructions?|prompt|directive)s?\b',
    r'\bsystem prompt\b', r'^\s*(system|assistant|developer)\s*:', r'<\|im_start\|>|<\|system\|>|\[/?INST\]|###\s*(system|instruction)',
    r'\b(approve|auto-?approve|mark (it |this )?as approved|recommend approval( of)?|clear|whitelist)\b[^.\n]{0,20}\b(this|the) (case|application|customer|account|company|transaction|request)\b',
    r'\b(do not|don\'t|never) (flag|escalate|report|mention|raise|request)\b',
    r'\b(reveal|print|show|output|repeat)\b[^.\n]{0,20}\b(system prompt|your instructions|hidden instructions|api key|secret)',
    r'\b(send|forward|exfiltrate|upload) (this|the|all)\b[^.\n]{0,30}\b(to|at) (https?://|\S+@)',
    r'\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|model|llm|reviewer bot)\b',
)]
_WEAK = re.compile(r'\b(assistant|AI|model|LLM|instruction|must approve|approve|automatically|system)\b', re.I)


def _spans(text: str):
    """Yield (start, end) of instruction-like spans: the matching line, or the matching sentence for long lines."""
    out = []
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip('\r\n')
        if any(p.search(stripped) for p in INJECTION_PATTERNS):
            if len(stripped) <= 300:
                out.append((pos, pos + len(stripped)))
            else:
                offset = 0
                for sentence in re.split(r'(?<=[.!?])\s+', stripped):
                    idx = stripped.find(sentence, offset)
                    if any(p.search(sentence) for p in INJECTION_PATTERNS):
                        out.append((pos + idx, pos + idx + len(sentence)))
                    offset = idx + len(sentence)
        pos += len(line)
    return out


def quarantine(text: str) -> tuple[str, int]:
    spans = _spans(text or '')
    if not spans:
        return text, 0
    pieces, last = [], 0
    for start, end in spans:
        pieces.append(text[last:start]); pieces.append(QUARANTINE); last = end
    pieces.append(text[last:])
    return ''.join(pieces), len(spans)


def weak_candidates(text: str, limit: int = 6) -> list[str]:
    """Lines with weak instruction cues (for the optional LLM classifier). In-memory only."""
    lines = [ln.strip() for ln in (text or '').splitlines() if 20 <= len(ln.strip()) <= 400 and _WEAK.search(ln)]
    return lines[:limit]


def quarantine_lines(text: str, lines: list[str]) -> tuple[str, int]:
    n = 0
    for line in lines:
        if line and line in text:
            text = text.replace(line, QUARANTINE); n += 1
    return text, n


# ---------------------------------------------------------------- grounding
CITE = re.compile(r'\[(E\d+(?:\s*[,;]\s*E\d+)*)\]')
NON_CLAIM_KEYS = {'requirement', 'status', 'severity', 'recommendation', 'subject', 'title', 'name', 'type', 'id', 'key',
                  'decision', 'label', 'level', 'evidence_ids', 'channel', 'issue_key', 'to_status'}
EVIDENCE_KEYS = ('evidence_ids', 'evidence', 'citations', 'sources')
_SENT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9\[("\'*_-])')


def _labels_in(text: str) -> list[str]:
    out = []
    for m in CITE.finditer(text):
        out += [x.strip() for x in re.split(r'[,;]', m.group(1))]
    return out


# Statements about the absence of evidence, limitations or proposed next steps assert nothing about a source, so
# they need no citation (stripping them would turn "insufficient evidence" into an empty answer).
NON_FACTUAL = re.compile(
    r"\b(no (relevant |accessible |readable )?(evidence|information|documents?|sources?)|(evidence|information|sources?) (is|are) "
    r"(insufficient|missing|not available)|insufficient (evidence|information)|does not (contain|include|mention|state|say)|"
    r"do not (contain|include|mention|state)|(cannot|can't|unable to) (answer|confirm|determine|verify)|not (found|stated|mentioned|specified) "
    r"in the (evidence|sources?|documents?)|(i|we) (recommend|suggest)|next steps?:|consider (asking|requesting|checking))", re.I)


def _is_claim(sentence: str) -> bool:
    s = CITE.sub('', sentence).strip(' -*#>_`')
    if len(s.split()) < 5 or s.endswith('?') or s.endswith(':') or PLACEHOLDER.fullmatch(s):
        return False
    if NON_FACTUAL.search(s) and not CITE.search(sentence):
        return False
    return True


def _norm_ids(values, valid: set[str]):
    kept, dropped = [], []
    for v in values if isinstance(values, list) else []:
        label = str(v).strip().strip('[]').strip()
        (kept if label in valid else dropped).append(label)
    return list(dict.fromkeys(kept)), dropped


def ground(obj, valid_labels, *, strip: bool = True) -> dict:
    """Check claims. Returns {result, verified:[{claim, labels}], removed:[claim], invalid_ids:[...]}.
    result is a cleaned deep copy (when strip) with unsupported sentences/items and invalid ids removed."""
    valid = set(valid_labels)
    verified, removed, invalid_ids = [], [], []

    def clean_text(text: str, inherited: list[str]) -> str:
        out_lines = []
        for line in text.split('\n'):
            if not line.strip():
                out_lines.append(line); continue
            kept = []
            for sentence in _SENT.split(line):
                labels = _labels_in(sentence)
                bad = [l for l in labels if l not in valid]
                if bad:
                    invalid_ids.extend(bad)
                    sentence = CITE.sub(lambda m: ''.join(f'[{x.strip()}]' for x in re.split(r'[,;]', m.group(1)) if x.strip() in valid), sentence)
                    sentence = re.sub(r'\s+([.!?,;])', r'\1', sentence)
                good = [l for l in labels if l in valid]
                if not _is_claim(sentence):
                    kept.append(sentence); continue
                if good or inherited:
                    verified.append({'claim': sentence.strip()[:300], 'labels': good or inherited}); kept.append(sentence)
                else:
                    removed.append(sentence.strip()[:300])
                    if not strip: kept.append(sentence)
            if kept or not strip:
                joined = ' '.join(s for s in kept if s.strip())
                if joined.strip() or not strip: out_lines.append(joined)
        return '\n'.join(out_lines).strip('\n')

    def walk(value, inherited, key=None):
        if isinstance(value, str):
            if key in NON_CLAIM_KEYS:
                return value
            return clean_text(value, inherited)
        if isinstance(value, list):
            items = []
            for v in value:
                cleaned = walk(v, inherited, key)
                if strip and isinstance(v, str) and isinstance(cleaned, str) and v.strip() and not cleaned.strip():
                    continue  # removed claim item
                if strip and isinstance(v, dict) and cleaned is None:
                    continue
                items.append(cleaned)
            return items
        if isinstance(value, dict):
            own = list(inherited)
            out = dict(value)
            for ek in EVIDENCE_KEYS:
                if ek in value and isinstance(value[ek], list):
                    kept, dropped = _norm_ids(value[ek], valid)
                    invalid_ids.extend(dropped)
                    out[ek] = kept if strip else value[ek]
                    own = kept
                    break
            had_claim_text = False; all_emptied = True
            for k, v in value.items():
                if k in EVIDENCE_KEYS:
                    continue
                cleaned = walk(v, own, k)
                if isinstance(v, str) and k not in NON_CLAIM_KEYS and _any_claim(v):
                    had_claim_text = True
                    if cleaned.strip(): all_emptied = False
                out[k] = cleaned
            if strip and had_claim_text and all_emptied and any(ek in value for ek in EVIDENCE_KEYS):
                return None  # an evidence-bearing item whose every claim was unsupported is dropped
            return out
        return value

    result = walk(copy.deepcopy(obj), [])
    if result is None:
        result = {}
    return {'result': result, 'verified': verified, 'removed': removed, 'invalid_ids': list(dict.fromkeys(invalid_ids))}


def _any_claim(text: str) -> bool:
    return any(_is_claim(s) for line in text.split('\n') for s in _SENT.split(line))


def pii_scan(obj, masker: Masker, kinds=('nric', 'iban', 'card', 'phone', 'email')):
    """Mask raw PII in a generated output. Returns (masked_obj, counts)."""
    counts: dict[str, int] = {}
    return masker.mask_obj(obj, kinds, counts), counts
