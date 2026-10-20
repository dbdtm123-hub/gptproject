"""Offline tests for the acceptance verifier's trust boundaries (not live IdP evidence)."""
import importlib.util
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/oauth'))
from verify_connection import discovery, validate_claims, https_url  # noqa: E402
from render_release import validate  # noqa: E402


def claims():
    return {'active': True, 'iss': 'https://auth.homelab.test', 'aud': ['https://mcp.homelab.test/mcp'],
            'exp': time.time() + 300, 'sub': 'owner-uuid', 'scope': 'autolog:read autolog:write',
            'token_type': 'Bearer', 'token_use': 'access_token'}


def test_live_verifier_claim_contract():
    validate_claims(claims(), 'https://auth.homelab.test', 'https://mcp.homelab.test/mcp', 'owner-uuid', ['autolog:read'])


def test_authelia_opaque_v4_verifier_token_type_does_not_accept_refresh():
    data = claims()
    data.pop('token_type')
    data.pop('token_use')
    args = ('https://auth.homelab.test', 'https://mcp.homelab.test/mcp', 'owner-uuid', ['autolog:read'])
    with pytest.raises(ValueError):
        validate_claims(data, *args, token='authelia_at_test')
    validate_claims(data, *args, token='authelia_at_test', access_token_format='authelia_opaque_v4')
    for invalid in ['authelia_rt_test', 'eyJ.test.jwt', 'other-opaque']:
        with pytest.raises(ValueError):
            validate_claims(data, *args, token=invalid, access_token_format='authelia_opaque_v4')
    data['token_type'] = 'refresh_token'
    with pytest.raises(ValueError):
        validate_claims(data, *args, token='authelia_at_test', access_token_format='authelia_opaque_v4')


@pytest.mark.parametrize('change', [{'active': False}, {'iss': 'https://evil.test'}, {'aud': ['other']},
    {'exp': True}, {'exp': 0}, {'exp': float('inf')}, {'sub': []}, {'sub': 'other'}, {'scope': ''},
    {'token_type': 'DPoP'}, {'token_type': None}, {'token_use': 'refresh_token'}])
def test_live_verifier_rejects_claims(change):
    data = {**claims(), **change}
    with pytest.raises(ValueError):
        validate_claims(data, 'https://auth.homelab.test', 'https://mcp.homelab.test/mcp', 'owner-uuid', ['autolog:read'])


def test_discovery_rejects_secret_exfiltration_and_missing_s256():
    base = {'issuer': 'https://auth.homelab.test', 'authorization_endpoint': 'https://auth.homelab.test/authorize',
            'token_endpoint': 'https://auth.homelab.test/token', 'introspection_endpoint': 'https://auth.homelab.test/introspect',
            'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': ['client_secret_basic'],
            'grant_types_supported': ['authorization_code']}
    def run(data):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))) as client:
            return discovery(client, base['issuer'])
    assert run(base)['issuer'] == base['issuer']
    for data in [{**base, 'token_endpoint': 'https://evil.test/token'},
                 {**base, 'code_challenge_methods_supported': ['plain']}, {**base, 'issuer': 'https://other.test'}]:
        with pytest.raises(ValueError):
            run(data)


def settings():
    return {'issuer': 'https://auth.homelab.test', 'resource': 'https://mcp.homelab.test/mcp',
            'redirect_uri': 'https://chatgpt.com/private-exact-callback', 'username': 'owner',
            'subject_scopes': {'owner-uuid': ['autolog:read', 'autolog:write']},
            'blog_public_base': 'https://autolog.homelab.test', 'image_public_base': 'https://img.homelab.test',
            'backend_image': 'ghcr.io/owner/backend@sha256:' + 'a' * 64,
            'auth_image': 'ghcr.io/authelia/authelia@sha256:' + 'b' * 64,
            'auth_storage_class': 'authelia-local'}


def test_release_requires_reviewed_digests_and_owner_policy():
    assert validate(settings()) == ('auth.homelab.test', 'mcp.homelab.test')
    for change in [{'auth_image': 'ghcr.io/authelia/authelia:latest'}, {'subject_scopes': {}},
                   {'subject_scopes': {'owner': ['admin']}}, {'username': 'owner\nadmin'},
                   {'redirect_uri': 'https://*.example.com/callback'}, {'issuer': 'http://auth.homelab.test'},
                   {'resource': 'https://mcp.homelab.test/other'},
                   {'auth_storage_class': ''}, {'auth_storage_class': 'nfs-client'},
                   {'auth_storage_class': 'REPLACE_WITH_STORAGE_CLASS'}]:
        with pytest.raises(ValueError):
            validate({**settings(), **change})


@pytest.mark.parametrize('url', ['http://host.test', 'https://u:p@host.test', 'https://host.test/?token=bad', 'https://host.test/#f'])
def test_no_insecure_endpoints(url):
    with pytest.raises(ValueError):
        https_url(url)


def test_redirect_trailing_slash_is_preserved():
    assert https_url("https://client.homelab.test/callback/") == "https://client.homelab.test/callback/"


def test_auth_bootstrap_without_unavailable_subject_or_backend():
    bootstrap = {k: v for k, v in settings().items() if k in
                 {'issuer', 'resource', 'redirect_uri', 'username', 'auth_image', 'auth_storage_class'}}
    assert validate(bootstrap, stage='auth') == ('auth.homelab.test', 'mcp.homelab.test')
    with pytest.raises(KeyError):
        validate(bootstrap)
    with pytest.raises(ValueError):
        validate({**bootstrap, 'auth_image': 'ghcr.io/authelia/authelia:4.39.28'}, stage='auth')


def test_existing_auth_storage_is_reference_only():
    # Guard against accidentally adding pre-existing storage to apply/delete scope.
    resources = (ROOT / 'k8s/auth/kustomization.yaml').read_text()
    assert 'pvc.yaml' not in resources
    assert not (ROOT / 'k8s/auth/pvc.yaml').exists()
    deployment = (ROOT / 'k8s/auth/deployment.yaml').read_text()
    assert 'claimName: autolog-auth-data' in deployment
    assert 'kubernetes.io/hostname: k8s-worker01' in deployment


def test_mcp_can_render_without_auth_image_but_requires_subjects():
    values = settings()
    values.pop('auth_image')
    assert validate(values, stage='mcp') == ('auth.homelab.test', 'mcp.homelab.test')
    with pytest.raises(ValueError):
        validate({**values, 'subject_scopes': {}}, stage='mcp')


@pytest.mark.parametrize('stage', ['auth', 'mcp', 'all'])
@pytest.mark.parametrize('change', [
    {'redirect_uri': ''}, {'redirect_uri': 'http://client.test/callback'},
    {'redirect_uri': 'https://client.test/callback?secret=bad'},
    {'auth_storage_class': 'local-ssd'}, {'auth_storage_class': 'nfs-client'},
    {'auth_storage_class': ''}, {'issuer': 'http://auth.test'},
    {'resource': 'https://mcp.test/wrong'},
])
def test_every_stage_fails_closed_for_invalid_auth_settings(stage, change):
    with pytest.raises(ValueError):
        validate({**settings(), **change}, stage=stage)


@pytest.mark.parametrize('stage', ['auth', 'mcp', 'all'])
def test_every_stage_requires_explicit_callback(stage):
    values = settings()
    values.pop('redirect_uri')
    with pytest.raises(KeyError):
        validate(values, stage=stage)
