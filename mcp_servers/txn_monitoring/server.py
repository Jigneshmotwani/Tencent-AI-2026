"""txn-monitoring: local MCP server over SYNTHETIC DEMO transaction-monitoring data (fictional alerts, accounts,
counterparties and transfers). Read-only.

Run over stdio (default; how the app registers it):
    .venv/Scripts/python.exe -m mcp_servers.txn_monitoring.server
Run over streamable HTTP (loopback only):
    .venv/Scripts/python.exe -m mcp_servers.txn_monitoring.server --http --port 8766   # endpoint http://127.0.0.1:8766/mcp

Every payload carries "synthetic": true and a "SYNTHETIC DEMO" label. No real people, companies or accounts.
`jira_refs.json` (optional, written by the demo seeding command) maps alert ids to the FINCRIME issue that tracks them.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

HERE = Path(__file__).parent
DATA_FILE = HERE / 'data.json'
REFS_FILE = HERE / 'jira_refs.json'
LABEL = 'SYNTHETIC DEMO: fictional transaction-monitoring data for the Northstar Pay STR demo. Not real.'

server = MCPServer(
    name='txn-monitoring',
    title='Transaction monitoring (synthetic)',
    description='SYNTHETIC DEMO transaction-monitoring alerts, account transactions and counterparty profiles.',
    instructions='All data is synthetic demo data. Use get_alert for an alert with its rule, window, customer, transfers and '
                 'counterparties; list_transactions for an account history; get_counterparty for one counterparty profile.',
    version='1.0.0',
    log_level='WARNING',
)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(DATA_FILE.read_text(encoding='utf-8'))


def _refs() -> dict:
    try:
        value = json.loads(REFS_FILE.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _envelope(**payload: Any) -> dict[str, Any]:
    return {'synthetic': True, 'label': LABEL, **payload}


def _counterparty(cp_id: str) -> dict | None:
    return _data()['counterparties'].get(cp_id)


def _named(txn: dict) -> dict:
    cp = _counterparty(txn['counterparty_id']) or {}
    return {**txn, 'currency': _data()['currency'], 'counterparty_name': cp.get('name', ''), 'counterparty_country': cp.get('country', ''),
            'counterparty_new': bool(cp.get('new'))}


@server.tool(annotations=READ_ONLY, description='Get one SYNTHETIC transaction-monitoring alert by id (e.g. ALR-2291). Returns alert_id, status, '
                         'severity, score, rule {rule_id, name, logic}, window {start, end, hours}, summary, metrics (inbound_count, '
                         'inbound_total_sgd, new_overseas_senders, onward_outflow_sgd, multiple_of_expected), customer {customer_id, name, '
                         'uen, account_id, segment, kyc_risk_rating}, transactions[] (amount, direction, counterparty), counterparties[] '
                         '(id, name, country, new, notes), related_cases[] (prior case ids) and jira_issue (FINCRIME issue tracking the '
                         'alert, or null).')
def get_alert(alert_id: Annotated[str, Field(description='Alert id, e.g. ALR-2291', pattern=r'^[A-Za-z]{3}-\d{3,6}$')]) -> dict[str, Any]:
    key = alert_id.strip().upper()
    alert = _data()['alerts'].get(key)
    if alert is None:
        return _envelope(found=False, alert_id=key, note='No alert with this id in the synthetic monitoring system.')
    customer = _data()['customers'].get(alert['customer_id'], {})
    ledger = {t['txn_id']: t for t in _data()['transactions'].get(alert['account_id'], [])}
    transactions = [_named(ledger[t]) for t in alert['transaction_ids'] if t in ledger]
    counterparties = [cp for cp in (_counterparty(c) for c in alert['counterparty_ids']) if cp]
    refs = _refs()
    jira_issue = (refs.get('alerts') or {}).get(key) or refs.get('default_review_issue') or None
    body = {k: v for k, v in alert.items() if k not in ('transaction_ids', 'counterparty_ids')}
    return _envelope(found=True, system=_data()['system'], **body, customer=customer, transactions=transactions,
                     counterparties=counterparties, jira_issue=jira_issue)


@server.tool(annotations=READ_ONLY, description='List SYNTHETIC transactions for an account (e.g. ACC-7731-0042) over the last N days before '
                         'the monitoring as-of date. Returns transactions[] (txn_id, ts, direction in|out, amount, currency, '
                         'counterparty_id, counterparty_name, counterparty_country, counterparty_new, reference) plus inbound/outbound totals.')
def list_transactions(account_id: Annotated[str, Field(description='Account id, e.g. ACC-7731-0042', pattern=r'^ACC-\d{4}-\d{4}$')],
                      days: Annotated[int, Field(ge=1, le=180, description='Look-back window in days (1-180)')] = 30) -> dict[str, Any]:
    key = account_id.strip().upper()
    ledger = _data()['transactions'].get(key)
    if ledger is None:
        return _envelope(found=False, account_id=key)
    as_of = datetime.fromisoformat(_data()['as_of'])
    since = as_of - timedelta(days=days)
    rows = [_named(t) for t in ledger if since <= datetime.fromisoformat(t['ts']) <= as_of]
    inbound = sum(t['amount'] for t in rows if t['direction'] == 'in')
    outbound = sum(t['amount'] for t in rows if t['direction'] == 'out')
    return _envelope(found=True, account_id=key, as_of=_data()['as_of'], days=days, count=len(rows),
                     inbound_total_sgd=inbound, outbound_total_sgd=outbound, transactions=rows)


@server.tool(annotations=READ_ONLY, description='Get one SYNTHETIC counterparty profile by id (e.g. CP-9105): name, type, country, bank, '
                         'IBAN when known, first_seen, new (first seen in the last 30 days) and analyst notes.')
def get_counterparty(id: Annotated[str, Field(description='Counterparty id, e.g. CP-9105', pattern=r'^CP-\d{3,6}$')]) -> dict[str, Any]:
    key = id.strip().upper()
    cp = _counterparty(key)
    if cp is None:
        return _envelope(found=False, counterparty_id=key)
    return _envelope(found=True, **cp)


def main() -> None:
    parser = argparse.ArgumentParser(description='txn-monitoring MCP server (synthetic demo data)')
    parser.add_argument('--http', action='store_true', help='serve streamable HTTP on 127.0.0.1 instead of stdio')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.http:
        server.run('streamable-http', host='127.0.0.1', port=args.port)
    else:
        server.run('stdio')


if __name__ == '__main__':
    main()
