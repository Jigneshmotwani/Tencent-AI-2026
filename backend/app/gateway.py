"""Only model boundary. All assistant inference goes through the local LiteLLM proxy."""
import json, time
from urllib.parse import urlparse
import httpx
from .config import settings
class ModelError(Exception):
    def __init__(self, code): self.code = code; super().__init__(code)
class ModelGateway:
    async def stream(self, messages, attribution):
        cfg = settings()
        if not cfg.model_id or not cfg.model_api_key: raise ModelError('model_not_configured')
        if urlparse(cfg.litellm_url).hostname not in {'127.0.0.1','localhost','::1'}:
            raise ModelError('gateway_must_be_local')
        headers = {'Authorization':f'Bearer {cfg.litellm_master_key}'}
        payload = {'model':cfg.model_alias, 'messages':messages,'stream':True,
            'stream_options':{'include_usage':True}, 'max_tokens':cfg.max_output_tokens,
            'user':attribution['user_id'], 'metadata':attribution}
        request_ref = None; started=time.monotonic(); tokens={}; provider_ref=None; truncated=False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=5), trust_env=False) as client:
                async with client.stream('POST', cfg.litellm_url+'/v1/chat/completions',headers=headers,json=payload) as response:
                    if response.status_code >= 400:
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
                    response = await client.get(cfg.litellm_url+'/spend/logs',headers=headers,params={'request_id':accounting_ref})
                    if response.is_success:
                        rows = response.json()
                        if isinstance(rows,dict): rows = rows.get('data',[])
                        if isinstance(rows,list):
                            match = next((r for r in rows if r.get('request_id') == accounting_ref), None)
                            # Unknown pricing can appear as zero in gateway accounting. Do not promote it.
                            if match and match.get('spend') is not None and float(match['spend'])>0:
                                yield 'cost', {'cost':float(match['spend']),'currency':'USD',
                                    'cost_status':'estimated','cost_basis':'configured provider-published per-1M rates via LiteLLM spend log' if cfg.model_input_per_1m and cfg.model_output_per_1m else 'LiteLLM 1.104.2 proxy spend log bundled pricing map'}
            except (httpx.HTTPError, ValueError, TypeError): pass
        # A cut-off answer (token cap hit, e.g. by hidden reasoning) must not be presented as complete.
        if truncated: raise ModelError('output_truncated')
