#!/usr/bin/env python3
"""Interactive authorization-code/PKCE acceptance check. Never prints tokens.

Uses actual browser login (including MFA/consent), then actual token/introspection
and read-only MCP requests. No application articles or existing tokens are changed.
Client secrets are read with getpass, not command-line arguments or chat messages.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time
from getpass import getpass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def https_url(value):
    u = urlsplit(value)
    require(not any(c.isspace() for c in value) and '*' not in value and u.scheme == 'https' and u.hostname and not u.username and not u.password
            and not u.query and not u.fragment, 'Expected fixed HTTPS URL without query/fragment/userinfo')
    return value


def discovery(client, issuer):
    doc = client.get(issuer + '/.well-known/openid-configuration')
    require(doc.status_code == 200, 'Discovery HTTP failure')
    data = doc.json()
    require(data.get('issuer') == issuer, 'Discovery issuer mismatch')
    require('S256' in data.get('code_challenge_methods_supported', []), 'Discovery must advertise S256')
    require('authorization_code' in data.get('grant_types_supported', []), 'Authorization code missing')
    require('client_secret_basic' in data.get('token_endpoint_auth_methods_supported', []), 'Basic client auth missing')
    for key in ['authorization_endpoint', 'token_endpoint', 'introspection_endpoint']:
        value = https_url(data.get(key, ''))
        # This deployment intentionally has all credential endpoints at one issuer origin.
        require(urlsplit(value).netloc == urlsplit(issuer).netloc, 'Unexpected credential endpoint origin')
    return data


def validate_claims(claims, issuer, resource, subject, scopes, *, token='', access_token_format='rfc7662'):
    require(isinstance(claims, dict), 'Introspection is not a JSON object')
    aud = claims.get('aud', [])
    if isinstance(aud, str):
        aud = [aud]
    exp = claims.get('exp')
    require(claims.get('active') is True, 'Token inactive')
    require(claims.get('iss') == issuer, 'Wrong issuer')
    require(isinstance(aud, list) and resource in aud, 'Wrong audience')
    require(type(exp) in [int, float] and time.time() < exp < float('inf'), 'Token expired or invalid expiry')
    require(isinstance(claims.get('sub'), str) and claims['sub'] == subject, 'Wrong subject')
    require(isinstance(claims.get('scope'), str) and set(scopes) <= set(claims['scope'].split()), 'Missing scopes')
    standard_access = (isinstance(claims.get('token_type'), str)
                       and claims['token_type'].lower() == 'bearer'
                       and claims.get('token_use', 'access_token') == 'access_token')
    authelia_access = (access_token_format == 'authelia_opaque_v4'
                       and 'token_type' not in claims and 'token_use' not in claims
                       and token.startswith('authelia_at_'))
    require(standard_access or authelia_access, 'Not a verified bearer access token')


def begin(args, client):
    issuer, resource = https_url(args.issuer).rstrip('/'), https_url(args.resource)
    redirect = https_url(args.redirect_uri)
    doc = discovery(client, issuer)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    values = dict(issuer=issuer, resource=resource, redirect_uri=redirect, client_id=args.client_id,
                  verifier=verifier, state=state, created=time.time())
    path = Path(args.state_file)
    require(path.parent.is_dir(), 'Create a private state directory first')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(values, stream)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = dict(response_type='code', client_id=args.client_id, redirect_uri=redirect, resource=resource,
                  scope='openid offline_access autolog:read autolog:write', state=state,
                  code_challenge=challenge, code_challenge_method='S256')
    print('Open in your own browser, authenticate and consent. Keep the resulting callback private:')
    print(doc['authorization_endpoint'] + '?' + urlencode(params))
    print('Then run --finish with the same state file. No tokens were issued by this script yet.')


def finish(args, client):
    path = Path(args.state_file)
    require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, 'State file must be private (0600)')
    values = json.loads(path.read_text())
    require(0 <= time.time() - values['created'] <= 600, 'Authorization attempt expired; begin again')
    doc = discovery(client, values['issuer'])
    callback = urlsplit(getpass('Paste full private callback URL (hidden): '))
    expected = urlsplit(values['redirect_uri'])
    require((callback.scheme, callback.netloc, callback.path) == (expected.scheme, expected.netloc, expected.path)
            and not callback.fragment, 'Callback URI mismatch')
    params = parse_qs(callback.query)
    require(params.get('state') == [values['state']] and 'error' not in params and len(params.get('code', [])) == 1,
            'Callback state/code invalid')
    if 'iss' in params:
        require(params['iss'] == [values['issuer']], 'Authorization response issuer mismatch')
    secret = getpass('ChatGPT OAuth client secret (hidden): ')
    rs_id = args.introspection_client_id
    rs_secret = getpass('Separate resource-server introspection secret (hidden): ')
    subject = args.subject or getpass('Expected owner subject (hidden, from verified IdP identity): ')
    token = client.post(doc['token_endpoint'], auth=(values['client_id'], secret),
                        data=dict(grant_type='authorization_code', code=params['code'][0],
                                  redirect_uri=values['redirect_uri'], resource=values['resource'], code_verifier=values['verifier']))
    require(token.status_code == 200, 'Token exchange failed; no HTTP body printed')
    path.unlink()  # The authorization code must never be retried.
    tokens = token.json()
    require(tokens.get('token_type', '').lower() == 'bearer' and tokens.get('access_token'), 'Missing bearer access token')
    response = client.post(doc['introspection_endpoint'], auth=(rs_id, rs_secret),
                           data={'token': tokens['access_token'], 'token_type_hint': 'access_token'})
    require(response.status_code == 200, 'Introspection failed')
    validate_claims(response.json(), values['issuer'], values['resource'], subject, ['autolog:read', 'autolog:write'],
                    token=tokens['access_token'], access_token_format=args.access_token_format)
    print('PASS real login, code+PKCE exchange, separate-client introspection, issuer/audience/expiry/subject/scopes')
    for ident, method, params in [(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                    'clientInfo': {'name': 'autolog-acceptance', 'version': '1'}}),
                                  (2, 'tools/list', {})]:
        response = client.post(values['resource'], headers={'Authorization': 'Bearer ' + tokens['access_token'],
                               'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'},
                               json={'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        require(response.status_code == 200 and 'result' in response.json(), 'MCP ' + method + ' failed')
        if method == 'tools/list':
            require(len(response.json()['result'].get('tools', [])) == 11, 'Expected 11 tools')
        print('PASS MCP ' + method)
    if tokens.get('refresh_token'):
        refreshed = client.post(doc['token_endpoint'], auth=(values['client_id'], secret),
                                data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'],
                                      'resource': values['resource']})
        require(refreshed.status_code == 200 and refreshed.json().get('access_token'), 'Refresh failed')
        response = client.post(doc['introspection_endpoint'], auth=(rs_id, rs_secret),
                               data={'token': refreshed.json()['access_token'], 'token_type_hint': 'access_token'})
        require(response.status_code == 200, 'Refreshed-token introspection failed')
        validate_claims(response.json(), values['issuer'], values['resource'], subject, ['autolog:read', 'autolog:write'],
                        token=refreshed.json()['access_token'], access_token_format=args.access_token_format)
        print('PASS refresh and renewed claims')
    else:
        raise ValueError('No refresh token; fix offline_access policy')
    print('OAuth/MCP HTTP acceptance passed. ChatGPT UI connection is a SEPARATE required gate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--begin', action='store_true')
    mode.add_argument('--finish', action='store_true')
    parser.add_argument('--state-file', required=True)
    parser.add_argument('--issuer')
    parser.add_argument('--resource')
    parser.add_argument('--redirect-uri')
    parser.add_argument('--client-id', default='autolog-chatgpt')
    parser.add_argument('--introspection-client-id', default='autolog-resource-server')
    parser.add_argument('--subject')
    parser.add_argument('--access-token-format', choices=['rfc7662', 'authelia_opaque_v4'], default='rfc7662',
                        help='Opt in only for the tested Authelia v4 opaque access token format.')
    args = parser.parse_args()
    if args.begin and not all([args.issuer, args.resource, args.redirect_uri]):
        parser.error('--begin requires --issuer, --resource, --redirect-uri')
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            (begin if args.begin else finish)(args, client)
    except (ValueError, OSError, httpx.HTTPError, KeyError):
        # Neither exceptions nor URLs/bodies are dumped; both can contain codes/tokens.
        raise SystemExit('FAIL OAuth acceptance; check fixed endpoints/configuration privately. No tokens printed.') from None


if __name__ == '__main__':
    main()
