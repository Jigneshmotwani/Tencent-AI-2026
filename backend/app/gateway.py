"""Only model boundary. All assistant inference goes through the local LiteLLM proxy."""
import json, time
from urllib.parse import urlparse
import httpx
from .config import settings
class ModelError(Exception):
    def __init__(self, code): self.code = code; super().__init__(code)
def model_aliases() -> list[str]:
    """Configured LiteLLM aliases (primary + verified extra models). The platform policy narrows this list."""
    cfg = settings()
    aliases = [cfg.model_alias] if cfg.model_id and cfg.model_api_key else []
    if cfg.extra_models:
        try: aliases += [m['alias'] for m in json.loads(cfg.extra_models) if m.get('alias')]
        except (ValueError, TypeError, KeyError): pass
    return aliases

class Completion:
    def __init__(self, text, usage, cost, latency_ms):
        self.text, self.usage, self.cost, self.latency_ms = text, usage, cost, latency_ms

class ModelGateway:
    async def complete(self, messages, attribution, *, model_alias=None, max_tokens=None, api_key=None):
        """Non-streaming convenience for workflow nodes; uses the same streamed path so usage/cost are captured.
        attribution: {user_id, username?, run_id, trace_id, workflow_id?, workflow_name?, workflow_version?, node_id?, node_type?}
        api_key: optional per-workflow LiteLLM virtual key (budget enforcement in the gateway). Raises ModelError
        ('budget_exceeded' when LiteLLM rejects for budget, plus the stream() codes)."""
        from .telemetry import record_model_call
        text, usage, cost = '', {}, None
        started = time.monotonic(); outcome = 'ok'
        try:
            async for kind, data in self.stream(messages, attribution, model_alias=model_alias, max_tokens=max_tokens, api_key=api_key):
                if kind == 'delta': text += data
                elif kind == 'usage': usage = {**usage, **{k: v for k, v in data.items() if v is not None}}
                elif kind == 'cost': cost = data.get('cost'); usage = {**usage, **data}
        except ModelError as exc:
            outcome = exc.code; raise
        finally:
            record_model_call(user=attribution.get('username') or attribution.get('user_id'), workflow=attribution.get('workflow_name'),
                model=model_alias or settings().model_alias, prompt_tokens=usage.get('prompt_tokens'), completion_tokens=usage.get('completion_tokens'),
                cost_usd=cost, latency_ms=(time.monotonic()-started)*1000, outcome=outcome, node_type=attribution.get('node_type',''))
        if not text.strip(): raise ModelError('empty_model_response')
        return Completion(text, usage, cost, (time.monotonic()-started)*1000)

    async def stream(self, messages, attribution, *, model_alias=None, max_tokens=None, api_key=None):
        cfg = settings()
        if not cfg.model_id or not cfg.model_api_key: raise ModelError('model_not_configured')
        if urlparse(cfg.litellm_url).hostname not in {'127.0.0.1','localhost','::1'}:
            raise ModelError('gateway_must_be_local')
        # Model registry (iteration 3): env + enabled admin models; disabled models are refused here, before the gateway.
        from .governance.models import default_alias, enabled_aliases
        alias = model_alias or default_alias()
        if alias not in enabled_aliases(): raise ModelError('model_not_allowed')
        headers = {'Authorization':f'Bearer {api_key or cfg.litellm_master_key}'}
        from opentelemetry.propagate import inject
        inject(headers)  # W3C traceparent so LiteLLM's spans can join the app trace
        # Only safe, scalar attribution reaches the gateway's spend metadata (no text).
        metadata = {k: v for k, v in attribution.items() if isinstance(v, (str, int, float)) and k != 'username'}
        payload = {'model':alias, 'messages':messages,'stream':True,
            'stream_options':{'include_usage':True}, 'max_tokens':min(max_tokens or cfg.max_output_tokens, 4096),
            'user':attribution['user_id'], 'metadata':metadata}
        request_ref = None; started=time.monotonic(); tokens={}; provider_ref=None; truncated=False; stream_cost=None; cost_sent=False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=5), trust_env=False) as client:
                async with client.stream('POST', cfg.litellm_url+'/v1/chat/completions',headers=headers,json=payload) as response:
                    if response.status_code >= 400:
                        body = (await response.aread())[:4000].decode('utf-8', 'replace').lower()
                        # LiteLLM virtual-key budget rejection (ExceededBudget / budget_exceeded); never echo the body.
                        if 'budget' in body: raise ModelError('budget_exceeded')
                        raise ModelError('provider_unavailable' if response.status_code >= 500 else 'provider_request_rejected')
                    request_ref = response.headers.get('x-litellm-call-id')
                    async for line in response.aiter_lines():
                        if not line.startswith('data:'): continue
                        value = line[5:].strip()
                        if value == '[DONE]': break
                        try: part = json.loads(value)
                        except ValueError: continue
                        if 'error' in part: raise ModelError('provider_unavailable')
                        provider_ref=provider_ref or part.get('id'); request_ref=request_ref or provider_ref
                        usage = part.get('usage')
                        if usage:
                            tokens={k:usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                            # LiteLLM reports its computed cost in the final usage chunk; spend-log rows are written later in batches.
                            if isinstance(usage.get('cost'),(int,float)) and usage['cost']>0: stream_cost=float(usage['cost'])
                            yield 'usage', {**tokens,'status':'finalized','cost':None,'currency':None,'cost_basis':None,
                                'provider_request_ref':provider_ref,'gateway_request_ref':request_ref,'cost_status':'unknown',
                                'model_provider':cfg.model_id.split('/',1)[0] if '/' in cfg.model_id else 'configured provider',
                                'model_latency_ms':(time.monotonic()-started)*1000}
                        for choice in part.get('choices',[]):
                            if choice.get('finish_reason')=='length': truncated=True
                            delta = choice.get('delta',{}).get('content')
                            if delta: yield 'delta', delta
        except ModelError: raise
        except (httpx.HTTPError, ValueError): raise ModelError('provider_unavailable')
        yield 'usage', {**tokens,'status':'finalized' if tokens else 'unknown',
            'provider_request_ref':provider_ref,'gateway_request_ref':request_ref,
            'model_provider':cfg.model_id.split('/',1)[0] if '/' in cfg.model_id else 'configured provider',
            'model_latency_ms':(time.monotonic()-started)*1000}
        # Metering lookup is independent of inference success. Only accept a known priced row.
        accounting_ref=provider_ref or request_ref
        if accounting_ref:
            try:
                async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                    response = await client.get(cfg.litellm_url+'/spend/logs',headers={'Authorization':f'Bearer {cfg.litellm_master_key}'},params={'request_id':accounting_ref})
                    if response.is_success:
                        rows = response.json()
                        if isinstance(rows,dict): rows = rows.get('data',[])
                        if isinstance(rows,list):
                            match = next((r for r in rows if r.get('request_id') == accounting_ref), None)
                            # Unknown pricing can appear as zero in gateway accounting. Do not promote it.
                            if match and match.get('spend') is not None and float(match['spend'])>0:
                                cost_sent=True
                                yield 'cost', {'cost':float(match['spend']),'currency':'USD',
                                    'cost_status':'estimated','cost_basis':'configured provider-published per-1M rates via LiteLLM spend log' if cfg.model_input_per_1m and cfg.model_output_per_1m else 'LiteLLM 1.104.2 proxy spend log bundled pricing map'}
            except (httpx.HTTPError, ValueError, TypeError): pass
        if stream_cost is not None and tokens and not cost_sent:
            basis='configured provider-published per-1M rates via LiteLLM streamed usage' if cfg.model_input_per_1m and cfg.model_output_per_1m else 'LiteLLM proxy bundled pricing map via streamed usage'
            yield 'cost', {'cost':stream_cost,'currency':'USD','cost_status':'estimated','cost_basis':basis}
        # A cut-off answer (token cap hit, e.g. by hidden reasoning) must not be presented as complete.
        if truncated: raise ModelError('output_truncated')
