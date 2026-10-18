"""Remote process supervisor for one Render web service (the Docker image's CMD). The counterpart of localstart.py.

One container runs everything the API needs on loopback, because the app only talks to its gateway and collector locally:
  OpenTelemetry Collector (optional, redaction allowlist, forwards to Grafana Cloud) -> LiteLLM gateway -> the API (uvicorn, $PORT).
The frontend is a separate Vercel project that proxies /api/* here, so the browser only ever talks to the Vercel origin.
Everything comes from environment variables (render.yaml); no .env file is read or written.
"""
import base64, hashlib, json, os, re, shutil, signal, subprocess, sys, time
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
import psycopg, yaml
from cryptography.fernet import Fernet
from . import litellm_config

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / '.local'
GATEWAY_PORT, COLLECTOR_PORT = 4000, 4318
PROVIDERS = ('drive', 'confluence', 'jira', 'slack')

def say(message): print('[remotestart] ' + message, flush=True)

def get(name, default=''):
    """Env value; an empty variable counts as unset (render.yaml leaves optional keys blank)."""
    return (os.environ.get(name) or '').strip() or default

def require(*names):
    missing = [n for n in names if not get(n)]
    if missing: raise SystemExit('Missing required environment variables: ' + ', '.join(missing) + '. See docs/DEPLOY.md.')

def fernet_key(raw):
    """A valid Fernet key as is; Render's generated 256-bit base64 value converted to the URL-safe alphabet; anything else is
    hashed into one deterministically (the same secret always gives the same key, so stored OAuth tokens stay readable)."""
    for candidate in (raw, raw.replace('+', '-').replace('/', '_'), raw.replace('+', '-').replace('/', '_') + '='):
        try:
            Fernet(candidate.encode()); return candidate
        except Exception: pass  # noqa: BLE001 - not a Fernet key, try the next form
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()).decode()

def alnum(raw):
    """Passwords end up in SQL literals and URLs; keep letters and digits only (stable for a given secret)."""
    cleaned = re.sub('[^A-Za-z0-9]', '', raw)
    return cleaned if len(cleaned) >= 24 else hashlib.sha256(raw.encode()).hexdigest()

def libpq(url):
    for prefix in ('postgresql+psycopg://', 'postgres://'):
        if url.startswith(prefix): return 'postgresql://' + url[len(prefix):]
    return url

def with_credentials(url, user, password):
    parts = urlsplit(url)
    host = parts.hostname + (f':{parts.port}' if parts.port else '')
    return urlunsplit((parts.scheme, f'{user}:{quote(password, safe="")}@{host}', parts.path, parts.query, ''))

def with_database(url, database=None, schema=None):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    if schema: query['schema'] = schema
    return urlunsplit((parts.scheme, parts.netloc, '/' + database if database else parts.path, urlencode(query), ''))

def collector_config(grafana):
    """The local collector config (observability/otel-collector.yaml, incl. its redaction allowlist) with the exporters swapped:
    Grafana Cloud's OTLP gateway when configured, otherwise a no-op sink. The allowlist stays the single source of truth."""
    config = yaml.safe_load((ROOT / 'observability/otel-collector.yaml').read_text())
    config['receivers']['otlp']['protocols']['http']['endpoint'] = f'127.0.0.1:{COLLECTOR_PORT}'
    config['processors']['memory_limiter']['limit_mib'] = 128
    if grafana:
        config['extensions'] = {'basicauth/grafana': {'client_auth': {'username': '${env:GRAFANA_OTLP_USER}', 'password': '${env:GRAFANA_OTLP_TOKEN}'}}}
        config['exporters'] = {'otlphttp/grafana': {'endpoint': '${env:GRAFANA_OTLP_ENDPOINT}', 'auth': {'authenticator': 'basicauth/grafana'}}}
        config['service']['extensions'] = ['basicauth/grafana']
        exporters = ['otlphttp/grafana']
    else:
        config['exporters'] = {'nop': {}}
        exporters = ['nop']
    for pipeline in config['service']['pipelines'].values(): pipeline['exporters'] = exporters
    return config

def wait_for_database(url):
    for _ in range(60):
        try:
            psycopg.connect(url, connect_timeout=5).close(); return
        except psycopg.OperationalError: time.sleep(2)
    raise SystemExit('The database did not become available.')

def litellm_database(owner_url):
    """LiteLLM keeps its own tables. Prefer a separate `litellm` database (as the local launcher does); if this user may not create
    one, use a `litellm` schema inside the app database. Returns the LiteLLM connection URL."""
    try:
        with psycopg.connect(owner_url, autocommit=True) as conn:
            if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', ('litellm',)).fetchone():
                conn.execute('CREATE DATABASE litellm')
        say('LiteLLM database: separate database "litellm".')
        return with_database(owner_url, database='litellm')
    except psycopg.Error as exc:
        say(f'Cannot create a separate LiteLLM database ({getattr(exc, "sqlstate", None) or type(exc).__name__}); using schema "litellm".')
        with psycopg.connect(owner_url, autocommit=True) as conn:
            conn.execute('CREATE SCHEMA IF NOT EXISTS litellm')
        return with_database(owner_url, schema='litellm')

def main():
    require('DATABASE_URL', 'APP_ORIGIN', 'ENCRYPTION_KEY', 'LITELLM_MASTER_KEY', 'AUDIT_HMAC_KEY', 'APP_RUNTIME_DB_PASSWORD', 'GRAFANA_DB_PASSWORD')
    os.chdir(ROOT); LOCAL.mkdir(exist_ok=True)
    # render.yaml leaves optional keys blank; a blank variable must behave as unset so the app's own defaults apply.
    env = {k: v for k, v in os.environ.items() if v.strip()}
    app_origin = get('APP_ORIGIN').rstrip('/')
    if not app_origin.startswith('https://'): raise SystemExit('APP_ORIGIN must be the https URL of your Vercel site (no path).')
    owner_url = libpq(get('DATABASE_URL'))
    runtime_password = alnum(get('APP_RUNTIME_DB_PASSWORD'))
    master_key = get('LITELLM_MASTER_KEY')
    hosts = [h for h in (get('ALLOWED_HOSTS'), get('RENDER_EXTERNAL_HOSTNAME')) if h]
    grafana = all(get(k) for k in ('GRAFANA_OTLP_ENDPOINT', 'GRAFANA_OTLP_USER', 'GRAFANA_OTLP_TOKEN'))
    collector_bin = shutil.which('otelcol-contrib')
    env.update({
        'DEPLOYMENT': 'remote', 'APP_ORIGIN': app_origin, 'API_ORIGIN': get('API_ORIGIN', app_origin).rstrip('/'),
        'API_TLS_ORIGIN': get('API_TLS_ORIGIN', app_origin).rstrip('/'),  # Slack needs https; the Vercel origin already is, no TLS shim
        'COOKIE_SECURE': get('COOKIE_SECURE', 'true'), 'DEMO_MODE': 'false', 'ALLOWED_HOSTS': ','.join(hosts),
        'DATABASE_URL': owner_url, 'ENCRYPTION_KEY': fernet_key(get('ENCRYPTION_KEY')),
        'LITELLM_MASTER_KEY': master_key if master_key.startswith('sk-') else 'sk-' + master_key,
        'APP_RUNTIME_DB_PASSWORD': runtime_password, 'GRAFANA_DB_PASSWORD': alnum(get('GRAFANA_DB_PASSWORD')),
        'APP_DATABASE_URL': with_credentials(owner_url, 'app_runtime', runtime_password),
        'LITELLM_URL': f'http://127.0.0.1:{GATEWAY_PORT}', 'OTEL_ENDPOINT': f'http://127.0.0.1:{COLLECTOR_PORT}/v1/traces',
        'MODEL_ALIAS': get('MODEL_ALIAS', 'knowledge-model'),
        'LITELLM_TELEMETRY': 'False', 'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'DO_NOT_TRACK': '1', 'DISABLE_ERROR_LOGS': 'True',
        'TZ': 'GMT', 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1',
    })
    if not (get('MODEL_ID') and get('MODEL_API_KEY')): say('WARNING: MODEL_ID / MODEL_API_KEY are not set; answers will fail until they are.')
    if not hosts: say('WARNING: no RENDER_EXTERNAL_HOSTNAME / ALLOWED_HOSTS; requests proxied from Vercel will be rejected (HTTP 400).')

    wait_for_database(owner_url)
    litellm_url = litellm_database(owner_url)
    subprocess.run([sys.executable, '-m', 'backend.app.bootstrap'], env=env, check=True)
    # Roles can be refused by a managed host; then the API runs as the owner role (the audit trigger still applies).
    try:
        psycopg.connect(libpq(env['APP_DATABASE_URL']), connect_timeout=5).close()
    except psycopg.OperationalError:
        say('WARNING: the app_runtime database role is unavailable; the API will connect as the database owner.')
        env['APP_DATABASE_URL'] = ''

    config = litellm_config.build({**env}, otel=bool(collector_bin))
    configfile = LOCAL / 'litellm.yaml'; configfile.write_text(json.dumps(config)); os.chmod(configfile, 0o600)
    schema = Path(find_spec('litellm').submodule_search_locations[0]) / 'proxy/schema.prisma'
    env['LITELLM_DATABASE_URL'] = litellm_url
    subprocess.run(['prisma', 'db', 'push', '--schema', str(schema), '--skip-generate'], env={**env, 'DATABASE_URL': litellm_url}, check=True)

    commands = []
    if collector_bin:
        collector_file = LOCAL / 'otel-collector.yaml'; collector_file.write_text(yaml.safe_dump(collector_config(grafana)))
        commands.append(('collector', [collector_bin, f'--config={collector_file}'], env))
        say('Telemetry: ' + ('forwarding to Grafana Cloud.' if grafana else 'collector running, no Grafana Cloud credentials (data is dropped).'))
    else:
        say('Telemetry: otelcol-contrib not found; traces are not collected.')
    commands.append(('litellm', ['litellm', '--config', str(configfile), '--host', '127.0.0.1', '--port', str(GATEWAY_PORT),
        '--log_config', str(ROOT / 'config/logging.json')],
        {**env, 'DATABASE_URL': litellm_url, 'OTEL_EXPORTER': 'otlp_http', 'OTEL_SERVICE_NAME': 'litellm', 'STORE_MODEL_IN_DB': 'True'}))
    commands.append(('api', [sys.executable, '-m', 'uvicorn', 'backend.app.main:app', '--host', '0.0.0.0', '--port', get('PORT', '10000'), '--no-access-log'], env))
    for provider in PROVIDERS:
        say(f'OAuth redirect URI ({provider}): {env["API_TLS_ORIGIN"] if provider == "slack" else env["API_ORIGIN"]}/api/connections/{provider}/callback')

    children = [(name, subprocess.Popen(command, env=child_env, cwd=ROOT)) for name, command, child_env in commands]
    def stop(*_): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    status = 0
    try:
        while True:
            for name, child in children:
                if child.poll() is not None:
                    say(f'{name} exited with code {child.returncode}; stopping so the platform restarts the service.'); status = 1; raise KeyboardInterrupt()
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        for _, child in reversed(children):
            if child.poll() is None: child.terminate()
        for _, child in reversed(children):
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
    sys.exit(status)

if __name__ == '__main__': main()
