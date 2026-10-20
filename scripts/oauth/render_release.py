#!/usr/bin/env python3
"""Render independent auth/MCP release into .deploy; NEVER calls apply or reads Secrets.
Needs kubectl (Kustomize). Run from repository root with --settings JSON (public values).
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def origin(value):
    u = urlsplit(value)
    if any(c.isspace() for c in value) or '*' in value or u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ['', '/'] or u.port:
        raise ValueError('Use a fixed default-port HTTPS origin')
    return u.hostname


def validate(settings):
    auth = origin(settings['issuer'])
    if settings['issuer'] != 'https://' + auth:
        raise ValueError('Issuer must be an exact HTTPS origin without trailing slash')
    mcp = origin(settings['resource'].removesuffix('/mcp'))
    if settings['resource'] != 'https://' + mcp + '/mcp':
        raise ValueError('resource must be the exact HTTPS /mcp URL')
    for name in ['image_public_base', 'blog_public_base']:
        origin(settings[name])
    callback = urlsplit(settings['redirect_uri'])
    if callback.scheme != 'https' or not callback.hostname or '*' in settings['redirect_uri'] or any(c.isspace() for c in settings['redirect_uri']) or callback.username or callback.password or callback.query or callback.fragment:
        raise ValueError('Register the exact HTTPS ChatGPT redirect URI without wildcard')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', settings['username']):
        raise ValueError('Unsafe username')
    for name in ['backend_image', 'auth_image']:
        if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}', settings[name]):
            raise ValueError('Reviewed registry image digest required: ' + name)
    if not settings['subject_scopes'] or any(not isinstance(k, str) or not k or not isinstance(v, list)
            or not v or not set(v) <= {'autolog:read', 'autolog:write'} for k, v in settings['subject_scopes'].items()):
        raise ValueError('Explicit nonempty subject -> scope list required')
    if any('example.com' in str(v) or 'REPLACE' in str(v) or '.invalid' in str(v) for v in settings.values()):
        raise ValueError('Unresolved example value')
    return auth, mcp


def render(settings, output):
    auth, mcp = validate(settings)
    output = output.resolve()
    deploy = (ROOT / '.deploy').resolve()
    if not output.is_relative_to(deploy):
        raise ValueError('Output must be beneath the ignored .deploy directory')
    output.mkdir(parents=True, exist_ok=False)
    # Temporary overlay beneath repository to use normal Kustomize load restrictions.
    with tempfile.TemporaryDirectory(prefix='oauth-render-', dir=deploy) as tmp:
        parent = Path(tmp)
        for kind, base in [('auth', 'auth'), ('mcp', 'mcp')]:
            work = parent / kind
            work.mkdir()
            k = {'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization',
                 'resources': [str(ROOT / 'k8s' / base)]}
            if kind == 'auth':
                config = (ROOT / 'k8s/auth/configuration.yml').read_text()
                config = config.replace('auth.example.com', auth).replace('https://mcp.example.com/mcp', settings['resource'])
                config = config.replace('https://REPLACE-WITH-EXACT-CHATGPT-CALLBACK.invalid/callback', settings['redirect_uri'])
                config = config.replace('subject: user:owner', 'subject: ' + json.dumps('user:' + settings['username']))
                (work / 'configuration.yml').write_text(config)
                k['images'] = [{'name': 'ghcr.io/authelia/authelia', 'newName': settings['auth_image'].split('@')[0],
                                'digest': settings['auth_image'].split('@')[1]}]
                k['configMapGenerator'] = [{'name': 'autolog-auth-config', 'behavior': 'replace', 'files': ['configuration.yml']}]
            else:
                k['images'] = [{'name': 'autolog/backend', 'newName': settings['backend_image'].split('@')[0],
                                'digest': settings['backend_image'].split('@')[1]}]
                data = dict(AUTOLOG_MCP_RESOURCE=settings['resource'], AUTOLOG_MCP_ISSUER=settings['issuer'],
                            AUTOLOG_MCP_INTROSPECTION_URL=settings['issuer'] + '/api/oidc/introspection',
                            AUTOLOG_MCP_INTROSPECTION_CLIENT_ID='autolog-resource-server',
                            AUTOLOG_MCP_ALLOWED_SUBJECTS=json.dumps(list(settings['subject_scopes'])),
                            AUTOLOG_MCP_SUBJECT_SCOPES=json.dumps(settings['subject_scopes']),
                            AUTOLOG_MCP_BLOG_PUBLIC_BASE=settings['blog_public_base'],
                            AUTOLOG_MCP_IMAGE_PUBLIC_BASE=settings['image_public_base'])
                # Drop broad public HTTPS allow and replace it with DNS-aware Cilium
                # FQDN access to exactly this issuer. File uploads remain disabled.
                patch = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'autolog-mcp-config'}, 'data': data}
                (work / 'config.json').write_text(json.dumps(patch))
                k['patches'] = [{'path': 'config.json'}, {'target': {'group': 'networking.k8s.io', 'version': 'v1',
                    'kind': 'NetworkPolicy', 'name': 'autolog-mcp'},
                    'patch': json.dumps([{'op': 'remove', 'path': '/spec/egress/2'}])}]
                policy = {'apiVersion': 'cilium.io/v2', 'kind': 'CiliumNetworkPolicy',
                          'metadata': {'name': 'autolog-mcp-provider', 'namespace': 'lifelog'},
                          'spec': {'endpointSelector': {'matchLabels': {'app': 'autolog-mcp'}}, 'egress': [
                            {'toEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'kube-system', 'k8s:k8s-app': 'kube-dns'}}],
                             'toPorts': [{'ports': [{'port': '53', 'protocol': 'ANY'}], 'rules': {'dns': [{'matchPattern': '*'}]}}]},
                            {'toFQDNs': [{'matchName': auth}], 'toPorts': [{'ports': [{'port': '443', 'protocol': 'TCP'}]}]}]}}
                (work / 'provider.json').write_text(json.dumps(policy))
                k['resources'].append('provider.json')
            (work / 'kustomization.yaml').write_text(json.dumps(k))
            result = subprocess.run(['kubectl', 'kustomize', str(work)], capture_output=True, text=True, check=True)
            (output / (kind + '.yaml')).write_text(result.stdout)
    (output / 'settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    fragment = (ROOT / 'k8s/examples/cloudflared-auth-ingress.yaml.example').read_text().replace('auth.example.com', auth)
    fragment += (ROOT / 'k8s/examples/cloudflared-mcp-ingress.yaml.example').read_text().replace('mcp.example.com', mcp)
    (output / 'tunnel-fragment.yml').write_text(fragment)
    print('Rendered for review only: ' + str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    render(json.loads(Path(args.settings).read_text()), Path(args.output))
