"""Template rendering for node configs. W2-owned.

`{{inputs.x}}`, `{{nodes.<id>.output.<path>}}` (also `nodes.<id>.status`), `{{run.id}}`, `{{evidence}}` (labels list only).
A string that is exactly one template keeps the JSON type of the value; inside a larger string, a list of strings renders
as "- item" lines and other objects/arrays as compact JSON. A missing path renders '' (or None for whole-value templates)
and is reported as a warning.
"""
from __future__ import annotations

import json
import re

from .spec import TEMPLATE

_PART = re.compile(r'([^.\[\]]+)|\[(\d+)\]')


def split_path(path: str) -> list:
    parts = []
    for name, index in _PART.findall(path):
        parts.append(int(index) if index else name)
    return parts


def resolve(path: str, env: dict):
    """Returns (found, value)."""
    parts = split_path(path)
    if not parts:
        return False, None
    if parts == ['evidence']:
        return True, env.get('evidence_labels', [])
    cur = env
    for p in parts:
        if isinstance(cur, dict) and not isinstance(p, int) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and isinstance(p, int) and p < len(cur):
            cur = cur[p]
        elif isinstance(cur, list) and isinstance(p, str) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return False, None
    return True, cur


def _text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return '\n'.join('- ' + v for v in value)  # a list of strings inside text reads as bullet lines
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def render(value, env: dict, warnings: list | None = None):
    """Render templates recursively in str/list/dict values."""
    if isinstance(value, str):
        whole = TEMPLATE.fullmatch(value.strip())
        if whole:
            found, result = resolve(whole.group(1), env)
            if not found and warnings is not None:
                warnings.append('missing:' + whole.group(1)[:120])
            return result if found else None

        def sub(m):
            found, result = resolve(m.group(1), env)
            if not found and warnings is not None:
                warnings.append('missing:' + m.group(1)[:120])
            return _text(result) if found else ''
        return TEMPLATE.sub(sub, value)
    if isinstance(value, list):
        return [render(v, env, warnings) for v in value]
    if isinstance(value, dict):
        return {k: render(v, env, warnings) for k, v in value.items()}
    return value


def template_refs(value) -> list[str]:
    """All template paths used in a config value (for validation)."""
    out = []
    if isinstance(value, str):
        out += [m.group(1) for m in TEMPLATE.finditer(value)]
    elif isinstance(value, list):
        for v in value: out += template_refs(v)
    elif isinstance(value, dict):
        for v in value.values(): out += template_refs(v)
    return out


def has_template(value) -> bool:
    return isinstance(value, str) and bool(TEMPLATE.search(value))
