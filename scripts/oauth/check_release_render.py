#!/usr/bin/env python3
"""Offline Kustomize integration check using synthetic public values; no cluster calls.
Requires kubectl and PyYAML. Never generates credentials or fetches images.
"""
import json
from pathlib import Path
import tempfile

import yaml

from render_release import ROOT, render


def main():
    settings = {
        'issuer': 'https://auth.myhomlab.win',
        'resource': 'https://mcp.myhomlab.win/mcp',
        'redirect_uri': 'https://client.homelab.test/exact-callback',
        'username': 'owner', 'auth_storage_class': 'authelia-local',
        'auth_image': 'ghcr.io/authelia/authelia@sha256:' + 'b' * 64,
        'backend_image': 'ghcr.io/test/backend@sha256:' + 'a' * 64,
        'subject_scopes': {'verified-test-sub': ['autolog:read', 'autolog:write']},
        'blog_public_base': 'https://blog.homelab.test',
        'image_public_base': 'https://img.homelab.test',
    }
    deploy = ROOT / '.deploy'
    deploy.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='oauth-check-', dir=deploy) as directory:
        outputs = {}
        for stage in ['auth', 'mcp', 'all']:
            values = dict(settings)
            if stage == 'auth':
                for key in ['subject_scopes', 'backend_image', 'blog_public_base', 'image_public_base']:
                    values.pop(key)
            if stage == 'mcp':
                values.pop('auth_image')
            output = Path(directory) / stage
            render(values, output, stage)
            expected = {'auth.yaml', 'mcp.yaml'} if stage == 'all' else {stage + '.yaml'}
            assert {p.name for p in output.glob('*.yaml')} == expected
            fragment = (output / 'tunnel-fragment.yml').read_text()
            assert ('hostname: auth.myhomlab.win' in fragment) == (stage != 'mcp')
            assert ('hostname: mcp.myhomlab.win' in fragment) == (stage != 'auth')
            for file in expected:
                documents = list(yaml.safe_load_all((output / file).read_text()))
                assert all(d['metadata']['namespace'] == 'lifelog' for d in documents)
                assert all(d['kind'] not in {'PersistentVolume', 'PersistentVolumeClaim', 'StorageClass', 'Secret'} for d in documents)
                outputs[stage, file] = documents
        auth = outputs['auth', 'auth.yaml']
        deployment = next(d for d in auth if d['kind'] == 'Deployment')
        pod = deployment['spec']['template']['spec']
        assert deployment['metadata']['name'] == 'autolog-auth'
        assert pod['nodeSelector'] == {'kubernetes.io/hostname': 'k8s-worker01'}
        assert next(v for v in pod['volumes'] if v['name'] == 'data')['persistentVolumeClaim']['claimName'] == 'autolog-auth-data'
        assert pod['containers'][0]['image'] == settings['auth_image']
        config = next(d for d in auth if d['kind'] == 'ConfigMap')['data']['configuration.yml']
        for value in [settings['issuer'], settings['resource'], settings['redirect_uri'], 'policy: two_factor', 'enforce_pkce: always', 'pkce_challenge_method: S256', 'authorization_signed_response_alg: RS256']:
            assert value in config
        assert 'REPLACE' not in config and 'example.com' not in config
        mcp = outputs['mcp', 'mcp.yaml']
        deployment = next(d for d in mcp if d['kind'] == 'Deployment')
        assert deployment['metadata']['name'] == 'autolog-mcp'
        assert deployment['spec']['template']['spec']['containers'][0]['image'] == settings['backend_image']
        config = next(d for d in mcp if d['kind'] == 'ConfigMap')['data']
        assert config['AUTOLOG_MCP_RESOURCE'] == settings['resource']
        assert config['AUTOLOG_MCP_ISSUER'] == settings['issuer']
        assert config['AUTOLOG_MCP_ACCESS_TOKEN_FORMAT'] == 'authelia_opaque_v4'
        assert json.loads(config['AUTOLOG_MCP_SUBJECT_SCOPES']) == settings['subject_scopes']
        policy = next(d for d in mcp if d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'autolog-mcp')
        assert not any('ipBlock' in peer for rule in policy['spec']['egress'] for peer in rule.get('to', []))
        provider = next(d for d in mcp if d['kind'] == 'CiliumNetworkPolicy')
        assert provider['spec']['egress'][1]['toFQDNs'] == [{'matchName': 'auth.myhomlab.win'}]
        assert outputs['all', 'auth.yaml'] == auth
        assert outputs['all', 'mcp.yaml'] == mcp
    print('PASS: real Kustomize auth/mcp/all rendering; existing storage reference only; OAuth/network/image assertions')


if __name__ == '__main__':
    main()
