"""Safe expression evaluator for edge conditions (no eval/exec). W2-owned.

Grammar is a whitelisted subset of Python expressions: literals, names (inputs, nodes, run), attribute/constant-subscript
access into dicts/lists, comparisons, boolean ops, `not`, arithmetic + - * / %, `in`, list/tuple literals and a few pure
functions (len, lower, upper, str, int, float, bool, contains, startswith). `{{ path }}` wrappers are accepted and unwrapped.
Anything else (calls to other names, lambdas, comprehensions, dunder access, starred, f-strings) is rejected at parse time.
"""
from __future__ import annotations

import ast
import re

MAX_LEN = 300
ROOTS = {'inputs', 'nodes', 'run', 'true', 'false', 'null', 'True', 'False', 'None'}
FUNCS = {
    'len': lambda x: len(x) if isinstance(x, (str, list, dict, tuple)) else 0,
    'lower': lambda x: str(x).lower(), 'upper': lambda x: str(x).upper(), 'str': lambda x: '' if x is None else str(x),
    'int': lambda x: int(float(x)), 'float': lambda x: float(x), 'bool': bool,
    'contains': lambda a, b: (b in a) if isinstance(a, (str, list, dict, tuple)) else False,
    'startswith': lambda a, b: str(a).startswith(str(b)),
}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
        ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}
_TEMPLATE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')


class ExpressionError(ValueError):
    pass


def _unwrap(expr: str) -> str:
    return _TEMPLATE.sub(lambda m: m.group(1), expr or '').strip()


def parse(expr: str) -> ast.Expression:
    """Parse and structurally whitelist. Raises ExpressionError with a safe message."""
    text = _unwrap(expr)
    if not text or len(text) > MAX_LEN:
        raise ExpressionError('expression empty or too long')
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError:
        raise ExpressionError('expression syntax error') from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd)):
            continue
        if isinstance(node, (ast.BoolOp, ast.UnaryOp, ast.Compare, ast.List, ast.Tuple, ast.IfExp)):
            continue
        if isinstance(node, tuple(_CMP)) or isinstance(node, tuple(_BIN)):
            continue
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
            continue
        if isinstance(node, ast.Name):
            if node.id not in ROOTS and node.id not in FUNCS:
                raise ExpressionError(f'unknown name {node.id[:30]}')
            continue
        if isinstance(node, ast.Attribute):
            if node.attr.startswith('_'):
                raise ExpressionError('private attribute access')
            continue
        if isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Constant):
                raise ExpressionError('only constant subscripts are allowed')
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ExpressionError('function not allowed')
            continue
        raise ExpressionError(f'construct not allowed: {type(node).__name__}')
    return tree


def _get(container, key):
    if isinstance(container, dict):
        return container.get(key)
    if isinstance(container, (list, tuple)) and isinstance(key, int) and -len(container) <= key < len(container):
        return container[key]
    return None


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ('true', 'True'): return True
        if node.id in ('false', 'False'): return False
        if node.id in ('null', 'None'): return None
        return env.get(node.id)
    if isinstance(node, ast.Attribute):
        return _get(_eval(node.value, env), node.attr)
    if isinstance(node, ast.Subscript):
        return _get(_eval(node.value, env), node.slice.value)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for v in node.values:
                result = _eval(v, env)
                if not result: return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, env)
            if result: return result
        return result
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, env)
        if isinstance(node.op, ast.Not): return not value
        if isinstance(node.op, ast.USub): return -value
        return +value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, env), _eval(node.right, env)
        if isinstance(left, str) and isinstance(right, str) and len(left) + len(right) > 10000:
            raise ExpressionError('string too long')
        if isinstance(node.op, ast.Mult) and (isinstance(left, (str, list)) or isinstance(right, (str, list))):
            raise ExpressionError('sequence repetition not allowed')
        if isinstance(node.op, ast.Mod) and isinstance(left, (str, bytes)):
            raise ExpressionError('string formatting not allowed')  # '%999999999d' % 1 would allocate ~1 GB
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) and max(abs(left), abs(right)) > 1e15:
            raise ExpressionError('number too large')
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            try:
                ok = _CMP[type(op)](left, right)
            except TypeError:
                ok = False
            if not ok: return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Call):
        return FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
    raise ExpressionError('construct not allowed')


def evaluate(expr: str, env: dict):
    """Evaluate a whitelisted expression against env {inputs, nodes:{id:{output,status}}, run}. Errors -> ExpressionError."""
    tree = parse(expr)
    try:
        return _eval(tree, env)
    except ExpressionError:
        raise
    except Exception:
        raise ExpressionError('expression evaluation failed') from None


def references(expr: str) -> set[str]:
    """Node ids referenced as nodes.<id> (for validation)."""
    try:
        tree = parse(expr)
    except ExpressionError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'nodes':
            out.add(node.attr)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'nodes' and isinstance(node.slice, ast.Constant):
            out.add(str(node.slice.value))
    return out
