"""Grafana deep links (W5). Tempo datasource uid is provisioned as `tempo`; dashboards by fixed uids."""
import json
import re
from urllib.parse import quote

from ..config import settings

TRACE_ID = re.compile(r'^[0-9a-f]{32}$')
DASHBOARDS = {'usage': 'ai-usage-cost', 'governance': 'governance', 'runs': 'workflow-runs'}


def grafana_trace_url(trace_id: str) -> str:
    """Explore view of one trace in Tempo. Returns '' for anything that is not a 32-hex trace id."""
    trace_id = (trace_id or '').strip().lower()
    if not TRACE_ID.match(trace_id):
        return ''
    panes = {'t': {'datasource': 'tempo', 'queries': [{'refId': 'A', 'datasource': {'type': 'tempo', 'uid': 'tempo'},
                                                        'queryType': 'traceql', 'query': trace_id, 'limit': 20}],
                   'range': {'from': 'now-7d', 'to': 'now'}}}
    return settings().grafana_url.rstrip('/') + '/explore?schemaVersion=1&orgId=1&panes=' + quote(json.dumps(panes, separators=(',', ':')), safe='')


def grafana_dashboard_url(name: str) -> str:
    uid = DASHBOARDS.get(name)
    return settings().grafana_url.rstrip('/') + f'/d/{uid}' if uid else ''
