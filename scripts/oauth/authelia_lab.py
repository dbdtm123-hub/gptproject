#!/usr/bin/env python3
"""Disposable real Authelia OAuth lab. Never reads kubeconfig or production secrets.

Run with backend dependencies: backend/.venv/bin/python scripts/oauth/authelia_lab.py
Needs Docker, OpenSSL and auth.autolog.test -> 127.0.0.1 on the isolated test host.
Only generated credentials and a temporary SQLite DB are used. No token is logged.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app import mcp_auth, mcp_server, mcp_tools  # noqa: E402
from app.mcp_config import MCPSettings  # noqa: E402

IMAGE = 'ghcr.io/authelia/authelia:4.39.28'
ISSUER = 'https://auth.autolog.test:19091'
RESOURCE = 'https://mcp.autolog.test/mcp'
CALLBACK = 'https://client.autolog.test/callback'


def check(ok, label):
    if not ok:
        raise RuntimeError(label)
    print('PASS ' + label, flush=True)


def digest(password):
    """Authelia's supported passlib PBKDF2-SHA512 format; no plaintext config."""
    salt = secrets.token_bytes(16)
    encode = lambda v: base64.b64encode(v).decode().rstrip('=').replace('+', '.')
    derived = hashlib.pbkdf2_hmac('sha512', password.encode(), salt, 310000)
    return '$pbkdf2-sha512$310000$' + encode(salt) + '$' + encode(derived)


def fixture(directory):
    directory.chmod(0o700)
    for name in ['data', 'secrets']:
        (directory / name).mkdir(mode=0o700)
    secret_dir = directory / 'secrets'
    credentials = {k: secrets.token_urlsafe(48) for k in ['owner', 'other', 'chatgpt', 'introspection']}
    for name in ['session', 'storage', 'reset', 'hmac']:
        (secret_dir / name).write_text(secrets.token_urlsafe(64))
    for name in ['chatgpt', 'introspection']:
        (secret_dir / (name + '.hash')).write_text(digest(credentials[name]))
    users = {'users': {name: {'displayname': name, 'password': digest(credentials[name]),
                              'email': name + '@autolog.test', 'groups': []}
                       for name in ['owner', 'other']}}
    # JSON is a YAML subset; avoids an extra YAML dependency.
    (secret_dir / 'users.yml').write_text(json.dumps(users))
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                    '-out', str(secret_dir / 'oidc.pem')], check=True, capture_output=True)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=auth.autolog.test', '-addext', 'subjectAltName=DNS:auth.autolog.test',
                    '-keyout', str(secret_dir / 'tls.key'), '-out', str(secret_dir / 'tls.crt')],
                   check=True, capture_output=True)
    config = (ROOT / 'k8s/auth/configuration.yml').read_text()
    config = config.replace('tcp://:9091/', 'tcp://127.0.0.1:19091/')
    config = config.replace('  disable_healthcheck: true',
                            '  disable_healthcheck: true\n  tls:\n    key: /secrets/tls.key\n    certificate: /secrets/tls.crt')
    config = config.replace('auth.example.com', 'auth.autolog.test')
    config = config.replace('authelia_url: https://auth.autolog.test', 'authelia_url: ' + ISSUER)
    config = config.replace('https://mcp.example.com/mcp', RESOURCE)
    config = config.replace('https://REPLACE-WITH-EXACT-CHATGPT-CALLBACK.invalid/callback', CALLBACK)
    # ONLY this isolated fixture removes second factor. Production remains two_factor.
    config = config.replace('policy: two_factor', 'policy: one_factor')
    config = config.replace('access_token: 5m', 'access_token: 30s')
    # Let the second lab user obtain a token, then prove MCP's subject deny.
    config = config.replace('subject: user:owner', 'subject: [user:owner, user:other]')
    (directory / 'configuration.yml').write_text(config)
    return credentials


def authorize(client, discovery, *, scope='openid offline_access autolog:read autolog:write',
              resource=RESOURCE, challenge_method='S256', username='owner', password=None):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(32)
    params = dict(response_type='code', client_id='autolog-chatgpt', redirect_uri=CALLBACK,
                  scope=scope, state=state, resource=resource,
                  code_challenge=challenge, code_challenge_method=challenge_method)
    if challenge_method is None:
        params.pop('code_challenge')
        params.pop('code_challenge_method')
    response = client.get(discovery['authorization_endpoint'], params=params)
    location = response.headers.get('location', '')
    query = parse_qs(urlsplit(location).query)
    if 'error' in query or response.status_code >= 400:
        return None, verifier
    flow_id = query.get('flow_id', [None])[0]
    check(bool(flow_id), 'authorization creates a consent/login flow')
    if password is not None:
        login = client.post(ISSUER + '/api/firstfactor', json={'username': username, 'password': password,
                            'keepMeLoggedIn': False, 'flow': 'openid_connect', 'flowID': flow_id})
        check(login.status_code == 200 and login.json().get('status') == 'OK', 'real password login')
    consent = client.post(ISSUER + '/api/oidc/consent', json={'flow_id': flow_id,
                          'client_id': 'autolog-chatgpt', 'consent': True, 'pre_configure': False})
    check(consent.status_code == 200 and consent.json().get('status') == 'OK', 'explicit consent')
    target = consent.json()['data']['redirect_uri']
    check(target.startswith(ISSUER + '/'), 'consent resumes at fixed issuer')
    response = client.get(target)
    callback = response.headers.get('location', '')
    check(callback.startswith(CALLBACK + '?'), 'exact registered redirect')
    values = parse_qs(urlsplit(callback).query)
    check(values.get('state') == [state], 'state roundtrip')
    return values.get('code', [None])[0], verifier


def run_checks(directory, credentials):
    context = ssl.create_default_context(cafile=str(directory / 'secrets/tls.crt'))
    with httpx.Client(verify=context, timeout=10, follow_redirects=False, trust_env=False) as browser:
        discovery = browser.get(ISSUER + '/.well-known/openid-configuration').json()
        check(discovery['issuer'] == ISSUER, 'OIDC exact issuer')
        oauth = browser.get(ISSUER + '/.well-known/oauth-authorization-server').json()
        check(oauth['issuer'] == ISSUER, 'OAuth discovery')
        check('S256' in discovery['code_challenge_methods_supported'], 'PKCE S256 advertised')
        for key in ['authorization_endpoint', 'token_endpoint', 'introspection_endpoint', 'revocation_endpoint']:
            check(discovery[key].startswith(ISSUER + '/'), 'fixed HTTPS ' + key)
        code, verifier = authorize(browser, discovery, password=credentials['owner'])
        check(bool(code), 'authorization code issued')
        token_args = dict(grant_type='authorization_code', code=code, redirect_uri=CALLBACK,
                          code_verifier=verifier, resource=RESOURCE)
        client_auth = ('autolog-chatgpt', credentials['chatgpt'])
        # Wrong verifier must fail without consuming a valid authorization.
        bad_code, bad_verifier = authorize(browser, discovery)
        denied = browser.post(discovery['token_endpoint'], auth=client_auth,
                              data={**token_args, 'code': bad_code, 'code_verifier': secrets.token_urlsafe(48)})
        check(denied.status_code == 400, 'wrong PKCE verifier denied')
        issued = browser.post(discovery['token_endpoint'], auth=client_auth, data=token_args)
        check(issued.status_code == 200, 'code + S256 token exchange')
        tokens = issued.json()
        check(tokens.get('token_type', '').lower() == 'bearer' and bool(tokens.get('refresh_token')), 'bearer and refresh issued')
        check(browser.post(discovery['token_endpoint'], auth=client_auth, data=token_args).status_code == 400, 'code replay denied')
        auth = ('autolog-resource-server', credentials['introspection'])
        introspect = lambda token: browser.post(discovery['introspection_endpoint'], auth=auth,
                                                data={'token': token, 'token_type_hint': 'access_token'})
        response = introspect(tokens['access_token'])
        check(response.status_code == 200, 'separate Basic introspection client')
        claims = response.json()
        aud = claims.get('aud', [])
        check(claims.get('active') is True and claims.get('iss') == ISSUER and RESOURCE in aud
              and claims.get('exp', 0) > time.time() and isinstance(claims.get('sub'), str)
              and {'autolog:read', 'autolog:write'} <= set(claims.get('scope', '').split()), 'strict MCP claims contract')
        check(introspect('invalid-' + secrets.token_urlsafe(32)).json().get('active') is False, 'invalid token inactive')
        check(browser.post(discovery['introspection_endpoint'], auth=(auth[0], 'incorrect'),
                           data={'token': tokens['access_token']}).status_code in [400, 401], 'incorrect introspection credential denied')
        settings = MCPSettings(resource=RESOURCE, issuer=ISSUER, introspection_url=discovery['introspection_endpoint'],
                               introspection_client_id=auth[0], introspection_client_secret=auth[1],
                               allowed_subjects=[claims['sub']], subject_scopes={claims['sub']: ['autolog:read', 'autolog:write']},
                               backend_token='lab-only-never-used')
        # Use real network introspection with certificate verification in the actual MCP app.
        real_client = httpx.AsyncClient
        original_auth_client = mcp_auth.httpx.AsyncClient
        def auth_client(**kwargs):
            return real_client(verify=context, trust_env=False, **kwargs)
        for module in [mcp_auth, mcp_server, mcp_tools]:
            module.get_mcp_settings = lambda: settings

        async def call(token, method='tools/list', params=None):
            # httpx module is shared: create ASGI client before temporarily replacing factory.
            async with real_client(transport=httpx.ASGITransport(app=mcp_server.app), base_url=RESOURCE.removesuffix('/mcp')) as client:
                mcp_auth.httpx.AsyncClient = auth_client
                try:
                    return await client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
                                             headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'})
                finally:
                    mcp_auth.httpx.AsyncClient = original_auth_client
        check(asyncio.run(call(tokens['access_token'], 'initialize')).status_code == 200, 'real token MCP initialize')
        listed = asyncio.run(call(tokens['access_token']))
        check(listed.status_code == 200 and len(listed.json()['result']['tools']) == 11, 'real token MCP tools/list 11')
        settings.allowed_subjects = ['unrelated-user']
        check(asyncio.run(call(tokens['access_token'])).status_code == 401, 'MCP wrong subject denied')
        settings.allowed_subjects = [claims['sub']]
        settings.resource = 'https://mcp.autolog.test/other'
        check(asyncio.run(call(tokens['access_token'])).status_code == 401, 'MCP wrong audience denied')
        settings.resource = RESOURCE
        settings.subject_scopes = {claims['sub']: []}
        check(asyncio.run(call(tokens['access_token'])).status_code == 403, 'MCP subject scope ceiling')
        settings.subject_scopes = {claims['sub']: ['autolog:read', 'autolog:write']}
        check(asyncio.run(call(tokens['refresh_token'])).status_code == 401, 'MCP refresh token denied as bearer')
        read_code, read_verifier = authorize(browser, discovery, scope='openid autolog:read')
        read_issue = browser.post(discovery['token_endpoint'], auth=client_auth,
                                  data={**token_args, 'code': read_code, 'code_verifier': read_verifier})
        check(read_issue.status_code == 200, 'read-only scope token issued')
        read_token = read_issue.json()['access_token']
        check('autolog:write' not in introspect(read_token).json().get('scope', '').split(), 'no implicit write scope')
        check(asyncio.run(call(read_token, 'tools/call', {'name': 'create_article', 'arguments': {}})).status_code == 403,
              'real read-only token cannot write')
        refreshed = browser.post(discovery['token_endpoint'], auth=client_auth,
                                 data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'resource': RESOURCE})
        check(refreshed.status_code == 200, 'refresh grant succeeds')
        revoke = browser.post(discovery['revocation_endpoint'], auth=client_auth,
                              data={'token': refreshed.json()['access_token'], 'token_type_hint': 'access_token'})
        check(revoke.status_code == 200 and introspect(refreshed.json()['access_token']).json().get('active') is False, 'revocation effective')
        code_bad, _ = authorize(browser, discovery, resource='https://unrelated.autolog.test/mcp')
        check(code_bad is None, 'unregistered resource denied')
        plain, _ = authorize(browser, discovery, challenge_method='plain')
        check(plain is None, 'plain PKCE denied')
        missing, _ = authorize(browser, discovery, challenge_method=None)
        check(missing is None, 'missing PKCE denied')
        unauthorized_scope, _ = authorize(browser, discovery, scope='openid autolog:admin')
        check(unauthorized_scope is None, 'unregistered scope denied')
        expiry_code, expiry_verifier = authorize(browser, discovery)
        expiry_issue = browser.post(discovery['token_endpoint'], auth=client_auth,
                                     data={**token_args, 'code': expiry_code, 'code_verifier': expiry_verifier})
        check(expiry_issue.status_code == 200, 'separate expiry test token issued')
        expiry_token = expiry_issue.json()['access_token']
        expires = introspect(expiry_token).json()['exp']
    # A separate real user may authenticate in the lab but cannot use owner's MCP.
    with httpx.Client(verify=context, timeout=10, follow_redirects=False, trust_env=False) as other:
        code, verifier = authorize(other, discovery, username='other', password=credentials['other'])
        issued = other.post(discovery['token_endpoint'], auth=client_auth,
                            data={**token_args, 'code': code, 'code_verifier': verifier})
        check(issued.status_code == 200, 'second lab user token issued')
        check(asyncio.run(call(issued.json()['access_token'])).status_code == 401, 'second real user MCP denied')
    time.sleep(max(0, expires - time.time() + 1))
    check(asyncio.run(call(expiry_token)).status_code == 401, 'real expired access token MCP denied')
    print('REAL_AUTHELIA_OAUTH_PASS (not a ChatGPT connection or production deployment)')


def main():
    for command in ['docker', 'openssl']:
        if not shutil.which(command):
            raise SystemExit('Required tool missing: ' + command)
    container = 'autolog-oauth-lab-' + secrets.token_hex(6)
    with tempfile.TemporaryDirectory(prefix='autolog-oauth-') as temp:
        directory = Path(temp)
        credentials = fixture(directory)
        mounts = ['--mount', f'type=bind,src={directory / "configuration.yml"},dst=/config/configuration.yml,readonly',
                  '--mount', f'type=bind,src={directory / "secrets"},dst=/secrets,readonly',
                  '--mount', f'type=bind,src={directory / "data"},dst=/data']
        args = ['docker', 'run', '--rm', '--network', 'host', '--user', f'{os.getuid()}:{os.getgid()}',
                '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,size=32m',
                *mounts, '--entrypoint', 'authelia', IMAGE]
        stage = 'container configuration validation'
        try:
            subprocess.run([*args, 'validate-config', '--config', '/config/configuration.yml',
                            '--config.experimental.filters=template'], check=True, capture_output=True)
            stage = 'container launch'
            subprocess.run([*args[:3], '-d', '--name', container, *args[3:], '--config', '/config/configuration.yml',
                            '--config.experimental.filters=template'], check=True, capture_output=True)
            stage = 'provider startup and OAuth checks'
            context = ssl.create_default_context(cafile=str(directory / 'secrets/tls.crt'))
            for _ in range(60):
                try:
                    with httpx.Client(verify=context, timeout=1, trust_env=False) as client:
                        if client.get(ISSUER + '/api/health').status_code == 200:
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Authelia lab startup failed')
            run_checks(directory, credentials)
            # Point-in-time sample, not an idle/load benchmark.
            stage = 'one-shot container resource sample'
            stats = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{.CPUPerc}} {{.MemUsage}}', container],
                                   capture_output=True, text=True, check=True)
            print('LAB_SAMPLE ' + stats.stdout.strip())
        except (httpx.HTTPError, ValueError, KeyError, RuntimeError):
            raise SystemExit('Authelia lab failed; last PASS label identifies progress. No HTTP body or URL printed.') from None
        except subprocess.CalledProcessError as error:
            # Only this disposable lab generates its own credentials. Print narrow
            # validation diagnostics after redacting generated passwords and keys.
            # Never print HTTP responses, access tokens, or entire provider logs.
            if stage == 'container configuration validation':
                stderr = ((error.stdout or b'') + b'\n' + (error.stderr or b'')).decode('utf-8', errors='replace')
                for generated in credentials.values():
                    stderr = stderr.replace(generated, '<REDACTED>')
                for filename in ['session', 'storage', 'reset', 'hmac', 'chatgpt.hash', 'introspection.hash']:
                    stderr = stderr.replace((directory / 'secrets' / filename).read_text(), '<REDACTED>')
                shown = 0
                for line in stderr.splitlines():
                    if (line.lstrip().startswith('- ') or any(word in line.lower() for word in ('error', 'invalid', 'unknown', 'failed', 'configuration', 'level=', ' msg=', ' field='))):
                        # Cap diagnostics and avoid multiline/private-key output.
                        if '-----BEGIN' in line or '-----END' in line:
                            continue
                        print('LAB_VALIDATION_DIAG ' + line[:400], flush=True)
                        shown += 1
                        if shown >= 12:
                            break
            raise SystemExit(f'Authelia command failed during {stage} (exit {error.returncode}); '
                             'full details withheld to protect test credentials') from error
        finally:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)


if __name__ == '__main__':
    main()
