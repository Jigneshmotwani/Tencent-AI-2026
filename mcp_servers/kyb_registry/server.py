"""kyb-registry: local MCP server over SYNTHETIC DEMO data (fictional registry, screening lists and risk rules).

Run over stdio (default; how the app registers it):
    .venv/Scripts/python.exe -m mcp_servers.kyb_registry.server
Run over streamable HTTP (loopback only):
    .venv/Scripts/python.exe -m mcp_servers.kyb_registry.server --http --port 8765   # endpoint http://127.0.0.1:8765/mcp

Every payload carries "synthetic": true and a "SYNTHETIC DEMO" label. No real people, companies or lists.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

DATA_FILE = Path(__file__).with_name('data.json')
LABEL = 'SYNTHETIC DEMO: fictional registry/screening data for the Northstar Pay KYB demo. Not real.'
FUZZY_THRESHOLD = 0.85

server = MCPServer(
    name='kyb-registry',
    title='KYB Registry (synthetic)',
    description='SYNTHETIC DEMO company registry, sanctions/PEP/adverse-media screening and KYB risk rules.',
    instructions='All data is synthetic demo data. Use lookup_company for registry profiles, screen_names for '
                 'sanctions/PEP/adverse-media screening, and get_risk_rules for jurisdiction/industry/UBO rules.',
    version='1.0.0',
    log_level='WARNING',
)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(DATA_FILE.read_text(encoding='utf-8'))


def _envelope(**payload: Any) -> dict[str, Any]:
    return {'synthetic': True, 'label': LABEL, **payload}


def _norm(value: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', value.lower())).strip()


def _action(entry_list: str, score: float, guidance: dict) -> str:
    if entry_list == 'sanctions' or score >= guidance['escalate_at_or_above']:
        return 'escalate to Compliance'
    if score >= guidance['no_action_below']:
        return 'requires analyst review'
    return 'no action'


@server.tool(annotations=READ_ONLY, description='Look up a company in the SYNTHETIC KYB registry by Singapore UEN. Returns the registry profile: '
                         'name, UEN, status, incorporation date, SSIC code + industry, registered address, directors[], '
                         'shareholders[] (percent, individual/corporate, jurisdiction) and all_names[] (directors + '
                         'shareholders, convenient input for screen_names).')
def lookup_company(uen: Annotated[str, Field(description='Singapore UEN, e.g. 201912345K', pattern=r'^[0-9A-Za-z]{9,10}$')]) -> dict[str, Any]:
    key = uen.strip().upper()
    company = _data()['companies'].get(key)
    if company is None:
        return _envelope(found=False, uen=key, registry=_data()['registry_name'])
    names: list[str] = []
    for person in company['directors'] + company['shareholders']:
        if person['name'] not in names:
            names.append(person['name'])
    return _envelope(found=True, registry=_data()['registry_name'], **company, all_names=names)


@server.tool(annotations=READ_ONLY, description='Screen person/company names against SYNTHETIC sanctions, PEP and adverse-media lists. Returns, per '
                         'name, status (clear|potential_match) and hits with list, matched_name, match_score (0-1), '
                         'category, summary and recommended_action (escalate at >=0.85 or any sanctions match; '
                         'analyst review at 0.50-0.84).')
def screen_names(names: Annotated[list[Annotated[str, Field(min_length=1, max_length=120)]],
                                  Field(min_length=1, max_length=25, description='Names to screen (directors, shareholders, UBOs, the company)')]) -> dict[str, Any]:
    screening = _data()['screening']
    guidance = screening['review_guidance']
    results = []
    for raw in names:
        name = _norm(raw)
        hits = []
        for entry in screening['entries']:
            score = entry['match_score'].get(name)
            if score is None:
                ratio = difflib.SequenceMatcher(None, name, _norm(entry['name'])).ratio()
                score = round(ratio, 2) if ratio >= FUZZY_THRESHOLD else None
            if score is None:
                continue
            hits.append({'entry_id': entry['entry_id'], 'list': entry['list'], 'matched_name': entry['name'],
                         'match_score': score, 'category': entry['category'], 'summary': entry['summary'],
                         'recommended_action': _action(entry['list'], score, guidance)})
        hits.sort(key=lambda h: -h['match_score'])
        results.append({'name': raw, 'status': 'potential_match' if hits else 'clear', 'hits': hits})
    return _envelope(lists_checked=screening['lists'], results=results,
                     potential_matches=sum(1 for r in results if r['hits']), guidance=guidance)


@server.tool(annotations=READ_ONLY, description='Get SYNTHETIC KYB risk rules for a country (ISO code, e.g. SG): high-risk jurisdictions, high-risk '
                         'and elevated-monitoring industries (SSIC prefixes), UBO threshold (25%) with the corporate '
                         'look-through rule, screening thresholds and document age limits.')
def get_risk_rules(country: Annotated[str, Field(description='Country code, e.g. SG', min_length=2, max_length=40)] = 'SG') -> dict[str, Any]:
    key = country.strip().upper()
    if key in ('SINGAPORE', 'SGP'):
        key = 'SG'
    rules = _data()['risk_rules'].get(key)
    if rules is None:
        return _envelope(found=False, country=key, note='No synthetic rules for this country; SG rules are the only seeded set.')
    return _envelope(found=True, **rules)


def main() -> None:
    parser = argparse.ArgumentParser(description='kyb-registry MCP server (synthetic demo data)')
    parser.add_argument('--http', action='store_true', help='serve streamable HTTP on 127.0.0.1 instead of stdio')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        server.run('streamable-http', host='127.0.0.1', port=args.port)
    else:
        server.run('stdio')


if __name__ == '__main__':
    main()
