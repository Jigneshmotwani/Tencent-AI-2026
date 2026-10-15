"""Local process supervisor, native PostgreSQL fallback and optional Compose support."""
import argparse, json, os, shutil, signal, socket, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
from cryptography.fernet import Fernet
import secrets
ROOT=Path(__file__).resolve().parents[2]
LOCAL=ROOT/'.local'
WINDOWS=sys.platform=='win32'
VENV_BIN=ROOT/'.venv'/('Scripts' if WINDOWS else 'bin')
SITE_PACKAGES=ROOT/'.venv'/('Lib' if WINDOWS else f'lib/python{sys.version_info.major}.{sys.version_info.minor}')/'site-packages'
def venv_exe(name): return str(VENV_BIN/(name+'.exe' if WINDOWS else name))

def run(command, **kw):
    return subprocess.run(command,check=True,**kw)
def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(.3); return sock.connect_ex(('127.0.0.1',port))==0

def ensure_tls():
    """Self-signed loopback certificate for the HTTPS OAuth callback shim (Slack requires HTTPS redirect URLs).
    Browsers warn once; accept it for https://localhost:<LOCAL_PORT_API_TLS> or install an mkcert cert at the same paths."""
    import datetime, ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    folder=LOCAL/'tls'; folder.mkdir(exist_ok=True); cert,key=folder/'localhost.pem',folder/'localhost-key.pem'
    if cert.exists() and key.exists(): return cert,key
    private=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    start=datetime.datetime.now(datetime.timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(start+datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .sign(private,hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())); os.chmod(key,0o600)
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert,key

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--services',choices=['native','compose'],default='native'); args=parser.parse_args()
    os.chdir(ROOT); LOCAL.mkdir(exist_ok=True); os.chmod(LOCAL,0o700)
    values={**dotenv_values('.env.example'),**dotenv_values('.env')}
    values.update({k:os.environ[k] for k in values if k in os.environ})
    # Ports are configurable so the stack can coexist with other local projects; endpoint URLs derive from them.
    ports={name:int(values.get(key) or default) for name,key,default in (('db','LOCAL_PORT_DB',55432),('api','LOCAL_PORT_API',8000),
        ('app','LOCAL_PORT_APP',5173),('gateway','LOCAL_PORT_GATEWAY',4000),('collector','LOCAL_PORT_COLLECTOR',4318),
        ('api_tls','LOCAL_PORT_API_TLS',8443),('grafana','LOCAL_PORT_GRAFANA',3000),('tempo','LOCAL_PORT_TEMPO',3200),('prometheus','LOCAL_PORT_PROMETHEUS',9090))}
    values.update({'APP_ORIGIN':f"http://localhost:{ports['app']}",'API_ORIGIN':f"http://localhost:{ports['api']}",
        'API_TLS_ORIGIN':f"https://localhost:{ports['api_tls']}",'GRAFANA_URL':f"http://localhost:{ports['grafana']}",
        'LITELLM_URL':f"http://127.0.0.1:{ports['gateway']}",'OTEL_ENDPOINT':f"http://127.0.0.1:{ports['collector']}/v1/traces"})
    if not values.get('ENCRYPTION_KEY'): values['ENCRYPTION_KEY']=Fernet.generate_key().decode()
    if not values.get('LITELLM_MASTER_KEY'): values['LITELLM_MASTER_KEY']='sk-'+secrets.token_urlsafe(36)
    # Native credentials are generated once, host bindings stay loopback only.
    for key in ('LOCAL_DB_PASSWORD','APP_RUNTIME_DB_PASSWORD','GRAFANA_DB_PASSWORD','GRAFANA_ADMIN_PASSWORD','AUDIT_HMAC_KEY'):
        if not values.get(key): values[key]=secrets.token_urlsafe(30)
    values['DATABASE_URL']=f"postgresql+psycopg://app:{values['LOCAL_DB_PASSWORD']}@127.0.0.1:{ports['db']}/app"
    # The API connects as the non-owner runtime role: audit rows are append-only for it (migration 2).
    values['APP_DATABASE_URL']=f"postgresql+psycopg://app_runtime:{values['APP_RUNTIME_DB_PASSWORD']}@127.0.0.1:{ports['db']}/app"
    envfile=ROOT/'.env'
    with envfile.open('w') as out:
        os.chmod(envfile,0o600)
        for k,v in values.items(): out.write(k+'='+json.dumps(v or '')+'\n')
    env=os.environ.copy(); env.update({k:v or '' for k,v in values.items()})
    env['PATH']=str(VENV_BIN)+os.pathsep+env.get('PATH','')
    env.update({'LITELLM_TELEMETRY':'False','LITELLM_LOCAL_MODEL_COST_MAP':'True','DO_NOT_TRACK':'1','DISABLE_ERROR_LOGS':'True',
        'PRISMA_BINARY_CACHE_DIR':str(LOCAL/'prisma-cache'),'PRISMA_HOME_DIR':str(LOCAL/'prisma')})
    if sys.platform == 'darwin' and Path('/opt/homebrew/opt/libpq/lib').exists():
        env['DYLD_FALLBACK_LIBRARY_PATH']='/opt/homebrew/opt/libpq/lib'+(':'+env['DYLD_FALLBACK_LIBRARY_PATH'] if env.get('DYLD_FALLBACK_LIBRARY_PATH') else '')
    env['TZ']='GMT'
    env.update({'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'})  # Windows redirects child stdout to cp1252 otherwise (LiteLLM banner)
    compose=args.services=='compose'
    for name in ('api','app','gateway','collector','api_tls'):
        if compose and name=='collector': continue  # the collector container is managed by docker compose
        if port_open(ports[name]): raise SystemExit(f"Port {ports[name]} is already in use. Stop that process or set LOCAL_PORT_{name.upper()} in .env.")
    tls_cert,tls_key=ensure_tls()
    pg_started=False; pg_bin=None; pg_data=LOCAL/'postgres'
    children=[]; files=[]
    try:
        if compose:
            # PostgreSQL + observability stack (collector, Tempo, Prometheus, Grafana); all loopback-bound.
            run(['docker','compose','up','-d','postgres','tempo','otel-collector','prometheus','grafana'],env=env)
        elif not port_open(ports['db']):
            explicit=os.environ.get('PG_BIN')
            candidates=[Path(explicit)] if explicit else []
            if shutil.which('postgres'): candidates.append(Path(shutil.which('postgres')).parent)
            candidates.extend([Path('/opt/homebrew/opt/postgresql@14/bin'),Path('/opt/homebrew/opt/postgresql@16/bin'),Path('/usr/lib/postgresql/16/bin')])
            pg_bin=next((p for p in candidates if (p/'initdb').exists()),None)
            if not pg_bin: raise SystemExit('No local PostgreSQL tools. Set PG_BIN or start Docker and use --services compose. Nothing was installed system-wide.')
            # Relocate a broken Homebrew prefix into an ignored workspace tree.
            # PostgreSQL's supported relative-path lookup needs the compiled bin tail preserved.
            if sys.platform=='darwin' and (pg_bin/'pg_config').exists():
                paths=subprocess.check_output([str(pg_bin/'pg_config'),'--bindir','--sharedir','--pkglibdir'],env=env,text=True).splitlines()
                if len(paths)==3 and not Path(paths[1]).exists():
                    actual_share=pg_bin.parent/'share'/'postgresql@14'
                    actual_lib=pg_bin.parent/'lib'/'postgresql@14'
                    if actual_share.exists() and actual_lib.exists():
                        prefix=Path(os.path.commonpath(paths)); runtime=LOCAL/'pg-runtime'
                        target_bin=runtime/Path(paths[0]).relative_to(prefix); target_bin.mkdir(parents=True,exist_ok=True)
                        for name in ('postgres','initdb','pg_ctl','pg_config'):
                            if not (target_bin/name).exists(): shutil.copy2(pg_bin/name,target_bin/name)
                        for compiled,actual in ((paths[1],actual_share),(paths[2],actual_lib)):
                            target=runtime/Path(compiled).relative_to(prefix); target.parent.mkdir(parents=True,exist_ok=True)
                            if not target.exists(): target.symlink_to(actual,target_is_directory=True)
                        pg_bin=target_bin
            pwfile=LOCAL/'postgres-password'; pwfile.write_text(values['LOCAL_DB_PASSWORD']); os.chmod(pwfile,0o600)
            if not (pg_data/'PG_VERSION').exists():
                init_command=[str(pg_bin/'initdb'),'-D',str(pg_data),'-U','app','--auth-local=trust','--auth-host=scram-sha-256','--pwfile='+str(pwfile)]
                pg_share=pg_bin.parent/'share'/'postgresql@14'
                if (pg_share/'postgres.bki').exists(): init_command.extend(['-L',str(pg_share)])
                run(init_command,stdout=subprocess.DEVNULL,env=env)
            run([str(pg_bin/'pg_ctl'),'-D',str(pg_data),'-l',str(LOCAL/'postgres.log'),'-o',f"-h 127.0.0.1 -p {ports['db']} -k '{LOCAL}'",'-w','start'],env=env)
            pg_started=True
        import psycopg
        pgurl=values['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')
        parsed=urlsplit(pgurl)
        if parsed.hostname not in {'127.0.0.1','localhost','::1'}: raise SystemExit('DATABASE_URL must point to a local database.')
        adminurl=pgurl.rsplit('/',1)[0]+'/postgres'
        # A published container port accepts TCP before PostgreSQL is ready (first start runs initdb and restarts), so wait for a real login.
        for _ in range(120):
            try: psycopg.connect(adminurl,connect_timeout=3).close(); break
            except psycopg.OperationalError: time.sleep(1)
        else: raise SystemExit('Local PostgreSQL did not become available.')
        with psycopg.connect(adminurl,autocommit=True) as conn:
            for name in ('app','litellm'):
                if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                    conn.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        run([sys.executable,'-m','backend.app.bootstrap'],env=env)
        config={'model_list':[], 'general_settings':{'master_key':'os.environ/LITELLM_MASTER_KEY',
            'database_url':'os.environ/LITELLM_DATABASE_URL','disable_error_logs':True,
            'store_prompts_in_spend_logs':False,'store_model_in_db':True},
            'litellm_settings':{'telemetry':False,'set_verbose':False,'turn_off_message_logging':True,
                'num_retries':0,'request_timeout':75},'router_settings':{'num_retries':0}}
        # LiteLLM's open-source OTel callback (messages are not logged: turn_off_message_logging). Prometheus is Enterprise-only.
        if compose: config['litellm_settings']['callbacks']=['otel']
        if values.get('MODEL_ID') and values.get('MODEL_API_KEY'):
            params={'model':'os.environ/MODEL_ID','api_key':'os.environ/MODEL_API_KEY'}
            if values.get('MODEL_API_BASE'): params['api_base']='os.environ/MODEL_API_BASE'
            if values.get('MODEL_INPUT_PER_1M') and values.get('MODEL_OUTPUT_PER_1M'):
                params['input_cost_per_token']=float(values['MODEL_INPUT_PER_1M'])/1e6
                params['output_cost_per_token']=float(values['MODEL_OUTPUT_PER_1M'])/1e6
            # Provider-specific request fields (JSON object), e.g. {"reasoning_effort":"none"} to disable hidden reasoning.
            if values.get('MODEL_EXTRA_BODY'): params['extra_body']=json.loads(values['MODEL_EXTRA_BODY'])
            config['model_list']=[{'model_name':values['MODEL_ALIAS'],'litellm_params':params}]
        # EXTRA_MODELS: more aliases on the same (or another OpenAI-compatible) provider. The key is referenced by env var name,
        # never copied: {"alias","model","api_key_env"(default MODEL_API_KEY),"api_base"(default MODEL_API_BASE),"extra_body",
        # "input_per_1m","output_per_1m","label"}.
        for extra in json.loads(values.get('EXTRA_MODELS') or '[]'):
            key_env=extra.get('api_key_env') or 'MODEL_API_KEY'
            if not (isinstance(extra,dict) and extra.get('alias') and extra.get('model') and values.get(key_env)): continue
            params={'model':extra['model'],'api_key':'os.environ/'+key_env}
            if extra.get('api_base') or values.get('MODEL_API_BASE'): params['api_base']=extra.get('api_base') or values['MODEL_API_BASE']
            if extra.get('extra_body'): params['extra_body']=extra['extra_body']
            if extra.get('input_per_1m') and extra.get('output_per_1m'):
                params['input_cost_per_token']=float(extra['input_per_1m'])/1e6; params['output_cost_per_token']=float(extra['output_per_1m'])/1e6
            config['model_list'].append({'model_name':extra['alias'],'litellm_params':params})
        configfile=LOCAL/'litellm.yaml'; configfile.write_text(json.dumps(config)); os.chmod(configfile,0o600)
        env['LITELLM_DATABASE_URL']=pgurl.rsplit('/',1)[0]+'/litellm'
        schema=SITE_PACKAGES/'litellm/proxy/schema.prisma'
        # Prisma cache/client artifacts remain in this workspace; never in a personal config dir.
        env['DATABASE_URL']=env['LITELLM_DATABASE_URL']
        if not (LOCAL/'prisma-ready').exists():
            run([venv_exe('prisma'),'generate','--schema',str(schema)],env=env)
            (LOCAL/'prisma-ready').touch()
        run([venv_exe('prisma'),'db','push','--schema',str(schema),'--skip-generate'],env=env)
        env['DATABASE_URL']=values['DATABASE_URL']
        commands=[('collector',[sys.executable,'-m','uvicorn','backend.app.collector:app','--host','127.0.0.1','--port',str(ports['collector']),'--no-access-log']),
            ('litellm',[venv_exe('litellm'),'--config',str(configfile),'--host','127.0.0.1','--port',str(ports['gateway']),'--log_config',str(ROOT/'config/logging.json')]),
            ('api',[sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(ports['api']),'--no-access-log']),
            ('callback-tls',[sys.executable,'-m','uvicorn','backend.app.tls_callback:app','--host','127.0.0.1','--port',str(ports['api_tls']),
                '--ssl-certfile',str(tls_cert),'--ssl-keyfile',str(tls_key),'--no-access-log']),
            ('frontend',['npm.cmd' if WINDOWS else 'npm','run','dev','--','--strictPort'])]
        if compose: commands=[c for c in commands if c[0]!='collector']
        for name,command in commands:
            log=(LOCAL/(name+'.log')).open('a'); os.chmod(LOCAL/(name+'.log'),0o600); files.append(log)
            childenv=env.copy()
            if name=='litellm':
                childenv['DATABASE_URL']=env['LITELLM_DATABASE_URL']
                childenv.update({'OTEL_EXPORTER':'otlp_http','OTEL_SERVICE_NAME':'litellm','STORE_MODEL_IN_DB':'True'})
            children.append((name,subprocess.Popen(command,env=childenv,cwd=ROOT/'frontend' if name=='frontend' else ROOT,stdout=log,stderr=log,
                # Own process group + private stdin: a console Ctrl-C/Ctrl-Break or terminal input must not reach children; only this launcher stops them.
                stdin=subprocess.PIPE,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0)))
        print(f"Starting at {values['APP_ORIGIN']}. API: {values['API_ORIGIN']}/api/health",flush=True)
        print('Accounts: .local/demo-accounts.txt; safe local logs: .local/*.log. Ctrl-C stops native services.',flush=True)
        def stop(*_): raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop)
        while True:
            for name,child in children:
                if child.poll() is not None: raise SystemExit(f'{name} exited. Inspect .local/{name}.log.')
            time.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        for name,child in reversed(children):
            if WINDOWS: subprocess.run(['taskkill','/T','/F','/PID',str(child.pid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)  # npm.cmd spawns grandchildren
            else: child.terminate()
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for log in files: log.close()
        if pg_started and pg_bin: subprocess.run([str(pg_bin/'pg_ctl'),'-D',str(pg_data),'-m','fast','-w','stop'],stdout=subprocess.DEVNULL)
if __name__=='__main__': main()
