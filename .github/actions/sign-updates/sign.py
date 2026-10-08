"""Sign final update assets. Run only in the credential-isolated signer, never execute app code."""
import hashlib
import json
import os
from pathlib import Path
import re
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SAFE = re.compile(r'^[a-zA-Z0-9_.-]+$')
TARGET = re.compile(r'^(macos-appkit|windows-winui|linux-gtk)-(aarch64|x86_64)$')


def asset(root, name):
    if not isinstance(name, str) or not SAFE.fullmatch(name) or name in ('.', '..'):
        raise ValueError('unsafe asset name')
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'missing regular asset: {name}')
    size = path.stat().st_size
    if not 0 < size <= 1024**3:
        raise ValueError('asset size outside supported range')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(name=name, size=size, sha256=digest)


def sign_updates(source, destination, seed, public, app_id, tag):
    if not seed.strip():
        raise ValueError('DAY_UPDATE_PRIVATE_KEY is missing from the signing environment')
    if not public.strip() or not SAFE.fullmatch(app_id):
        raise ValueError('update-public-key and update-application-id must be configured')
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed.strip()))
    actual = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    if actual != public.strip().lower():
        raise ValueError('private key does not match the public key embedded in the application')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?', tag):
        raise ValueError('not a supported release tag')
    seen = set()
    destination.mkdir(parents=True, exist_ok=True)
    for template in sorted(source.rglob('update-*.unsigned.json')):
        if template.is_symlink():
            raise ValueError('symlink metadata template')
        data = json.loads(template.read_text())
        target = data['target']
        if not TARGET.fullmatch(target) or target in seen:
            raise ValueError('invalid or duplicate target')
        seen.add(target)
        if data['application_id'] != app_id or data['version'] != tag[1:]:
            raise ValueError('application identity or version does not match release')
        if type(data['build']) is not int or not 0 < data['build'] < 2**53:
            raise ValueError('invalid monotonic build number')
        package = asset(template.parent, data['archive'])
        suffix = '.zip' if target.startswith('macos-') else '.exe' if target.startswith('windows-') else '.appimage'
        if not package['name'].endswith(suffix):
            raise ValueError('wrong artifact format')
        helper = None
        if not target.startswith('macos-'):
            helper = asset(template.parent, data['helper']['name'])
        release = dict(schema=2, application_id=app_id, version=data['version'], build=data['build'],
                       archive=package['name'], size=package['size'], sha256=package['sha256'],
                       target=target, tag=tag, helper=helper)
        encoded = json.dumps(release, sort_keys=True, separators=(',', ':')).encode()
        name = f'update-{target}.json'
        (destination / name).write_bytes(encoded)
        (destination / (name + '.sig')).write_text(key.sign(encoded).hex() + '\n')
    if not seen:
        raise ValueError('no update templates found')
    return seen


if __name__ == '__main__':
    sign_updates(Path(os.environ['UPDATE_ASSETS']), Path(os.environ['UPDATE_OUTPUT']),
                 os.environ['UPDATE_PRIVATE_KEY'], os.environ['UPDATE_PUBLIC_KEY'],
                 os.environ['UPDATE_APP_ID'], os.environ['UPDATE_TAG'])
