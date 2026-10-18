"""Sets up Grafana Cloud for the deployed app: the Postgres datasource, the three dashboards and the scope-denials alert.

Run it once from your laptop after the Render service is live (docs/DEPLOY.md step 6):

    uv run --frozen python deploy/grafana_cloud_setup.py --url https://<stack>.grafana.net --token <service-account-token> \
        --pg-host <render external db host> --pg-password <GRAFANA_DB_PASSWORD from Render>

It finds your stack's Prometheus (Mimir) and Tempo datasources, points the dashboards and the alert at them, and prints the
GRAFANA_TEMPO_UID value to put into the Render environment. Safe to run again (everything is upserted). --dry-run only prints.
"""
import argparse, json, os, sys
from pathlib import Path
import httpx, yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.remotestart import alnum  # noqa: E402 - the same password normalisation the service applies to GRAFANA_DB_PASSWORD

OBS = ROOT / 'observability/grafana'
FOLDER = {'uid': 'internal-brain-governance', 'title': 'Governance'}
GROUP = 'governance'

def pick(datasources, kind):
    """Grafana Cloud's built-in datasources have uids like grafanacloud-prom / grafanacloud-traces."""
    matches = [d for d in datasources if d.get('type') == kind]
    preferred = [d for d in matches if str(d.get('uid', '')).startswith('grafanacloud')]
    if kind == 'prometheus': preferred = [d for d in preferred if d['uid'].endswith('prom')] or preferred  # not grafanacloud-usage
    return (preferred or matches or [None])[0]

def remap(node, mapping):
    """Replace the local datasource uids (prometheus/tempo) wherever a dashboard or rule refers to them."""
    if isinstance(node, dict):
        return {k: (mapping[v] if k in ('uid', 'datasourceUid') and isinstance(v, str) and v in mapping else remap(v, mapping)) for k, v in node.items()}
    if isinstance(node, list): return [remap(v, mapping) for v in node]
    return node

def alert_rule(mapping):
    spec = yaml.safe_load((OBS / 'provisioning/alerting/scope-denials.yaml').read_text())['groups'][0]['rules'][0]
    rule = {'uid': spec['uid'], 'title': spec['title'], 'ruleGroup': GROUP, 'folderUID': FOLDER['uid'], 'orgID': 1, 'condition': spec['condition'],
            'for': '0s', 'noDataState': spec['noDataState'], 'execErrState': spec['execErrState'], 'labels': spec.get('labels', {}),
            'annotations': spec.get('annotations', {}), 'data': remap(spec['data'], mapping), 'isPaused': False}
    return rule

class Grafana:
    def __init__(self, url, token, dry):
        self.dry = dry
        self.client = httpx.Client(base_url=url.rstrip('/'), timeout=30, headers={'Authorization': 'Bearer ' + token, 'X-Disable-Provenance': 'true'})
    def call(self, method, path, body=None, ok=(200, 201, 202), also=()):
        if self.dry and method != 'GET':
            print(f'  [dry-run] {method} {path}'); return None
        response = self.client.request(method, path, json=body)
        if response.status_code in also: return response
        if response.status_code not in ok:
            raise SystemExit(f'{method} {path} -> HTTP {response.status_code}: {response.text[:300]}')
        return response

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=os.environ.get('GRAFANA_URL'), help='https://<stack>.grafana.net')
    parser.add_argument('--token', default=os.environ.get('GRAFANA_API_TOKEN'), help='service account token with the Admin role')
    parser.add_argument('--pg-host', help='Render Postgres external hostname (Render dashboard > database > Connect > External)')
    parser.add_argument('--pg-port', default='5432')
    parser.add_argument('--pg-database', default='internal_brain')
    parser.add_argument('--pg-password', default=os.environ.get('GRAFANA_DB_PASSWORD'), help='the GRAFANA_DB_PASSWORD value from the Render service')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.url or not args.token: parser.error('--url and --token are required')
    grafana = Grafana(args.url, args.token, args.dry_run)

    sources = grafana.call('GET', '/api/datasources').json()
    prom, tempo = pick(sources, 'prometheus'), pick(sources, 'tempo')
    if not prom or not tempo: raise SystemExit('Could not find the stack\'s Prometheus and Tempo datasources. Is this a Grafana Cloud stack?')
    mapping = {'prometheus': prom['uid'], 'tempo': tempo['uid']}
    print(f'Prometheus datasource: {prom["name"]} ({prom["uid"]}); Tempo datasource: {tempo["name"]} ({tempo["uid"]})')

    if args.pg_host and args.pg_password:
        body = {'uid': 'appdb', 'name': 'AppMetadata', 'type': 'grafana-postgresql-datasource', 'access': 'proxy', 'url': f'{args.pg_host}:{args.pg_port}',
                'user': 'grafana_ro', 'jsonData': {'database': args.pg_database, 'sslmode': 'require', 'postgresVersion': 1600, 'maxOpenConns': 5},
                'secureJsonData': {'password': alnum(args.pg_password)}}
        exists = grafana.call('GET', '/api/datasources/uid/appdb', also=(404,)).status_code == 200
        grafana.call('PUT' if exists else 'POST', '/api/datasources/uid/appdb' if exists else '/api/datasources', body)
        print('Postgres datasource AppMetadata: ' + ('updated' if exists else 'created'))
    else:
        print('Skipping the Postgres datasource (give --pg-host and --pg-password); the usage, cost and governance panels need it.')

    for path in sorted((OBS / 'dashboards').glob('*.json')):
        dashboard = remap(json.loads(path.read_text()), mapping); dashboard['id'] = None
        grafana.call('POST', '/api/dashboards/db', {'dashboard': dashboard, 'overwrite': True, 'message': 'internal-brain deploy'})
        print(f'Dashboard imported: {dashboard["title"]} ({args.url.rstrip("/")}/d/{dashboard["uid"]})')

    grafana.call('POST', '/api/folders', FOLDER, also=(409, 412))
    rule = alert_rule(mapping)
    exists = grafana.call('GET', f'/api/v1/provisioning/alert-rules/{rule["uid"]}', also=(404,)).status_code == 200
    grafana.call('PUT' if exists else 'POST', f'/api/v1/provisioning/alert-rules/{rule["uid"]}' if exists else '/api/v1/provisioning/alert-rules', rule)
    print('Alert rule "' + rule['title'] + '": ' + ('updated' if exists else 'created') + ' (add a contact point in Grafana > Alerting to get notified).')

    print(f'\nNow set these on the Render service (Environment tab), then let it redeploy:\n  GRAFANA_URL={args.url.rstrip("/")}\n  GRAFANA_TEMPO_UID={tempo["uid"]}')

if __name__ == '__main__': main()
