"""LiteLLM gateway config shared by the local launcher (localstart) and the remote one (remotestart)."""
import json

def build(values, *, otel=False):
    """`values` holds the env settings (MODEL_*, EXTRA_MODELS). The result is JSON-serialisable (YAML-compatible)."""
    config={'model_list':[], 'general_settings':{'master_key':'os.environ/LITELLM_MASTER_KEY',
        'database_url':'os.environ/LITELLM_DATABASE_URL','disable_error_logs':True,
        'store_prompts_in_spend_logs':False,'store_model_in_db':True},
        'litellm_settings':{'telemetry':False,'set_verbose':False,'turn_off_message_logging':True,
            'num_retries':0,'request_timeout':75},'router_settings':{'num_retries':0}}
    # LiteLLM's open-source OTel callback (messages are not logged: turn_off_message_logging). Prometheus is Enterprise-only.
    if otel: config['litellm_settings']['callbacks']=['otel']
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
    return config
