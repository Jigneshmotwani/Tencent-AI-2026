"""Loopback HTTPS shim for OAuth providers that require HTTPS redirect URLs (Slack).

It serves only GET /api/connections/<provider>/callback and immediately 303-redirects the browser, with the
same query string, to the plain-HTTP API callback on localhost, where state/session/owner checks run as usual.
No session, token exchange or data handling happens here (single API process semantics stay intact).
"""
from urllib.parse import urlencode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import settings

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1'])
HTTPS_PROVIDERS = {'slack'}


@app.get('/api/connections/{provider}/callback')
def callback(provider: str, request: Request):
    if provider not in HTTPS_PROVIDERS:
        raise HTTPException(404, 'not_found')
    query = urlencode(list(request.query_params.multi_items()))
    response = RedirectResponse(settings().api_origin.rstrip('/') + f'/api/connections/{provider}/callback?' + query, status_code=303)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/health')
def health():
    return {'status': 'ok'}
