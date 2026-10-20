#!/usr/bin/env python3
"""Generate NEW local auth secrets; never invokes Kubernetes or modifies existing files.

The populated output stays outside Git. Only secret-keys.txt entries belong in the
Authelia Secret. The two *.secret files are separate client credentials, not config.
"""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from authelia_lab import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--username', default='owner')
    parser.add_argument('--email', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', args.username):
        parser.error('Unsafe username')
    password = getpass('New IdP owner password (hidden, >=16 characters): ')
    if len(password) < 16 or password != getpass('Repeat password: '):
        raise SystemExit('Password too short or mismatch')
    os.umask(0o077)
    directory = Path(args.directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    def write(name, value):
        with (directory / name).open('x') as stream:
            stream.write(value)
    for name in ['session', 'storage', 'reset', 'hmac']:
        write(name, secrets.token_urlsafe(64))
    for name in ['chatgpt', 'introspection']:
        value = secrets.token_urlsafe(48)
        write(name + '.secret', value)
        write(name + '.hash', digest(value))
    write('users.yml', json.dumps({'users': {args.username: {'displayname': 'AutoLog Owner', 'password': digest(password),
          'email': args.email, 'groups': ['autolog-owner']}}}, indent=2) + '\n')
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                    '-out', str(directory / 'oidc.pem')], check=True, capture_output=True)
    print('New local files created with private permissions. No values printed; no Kubernetes access.')
    print('Back up storage encryption key and signing/HMAC keys with the matching database.')


if __name__ == '__main__':
    main()
