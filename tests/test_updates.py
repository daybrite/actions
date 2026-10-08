"""Updater signing uses final payload bytes and rejects mismatched identities and credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

path = Path(__file__).resolve().parents[1] / '.github/actions/sign-updates/sign.py'
spec = importlib.util.spec_from_file_location('sign_updates', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class UpdateSigningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.assets = self.root / 'assets'; self.assets.mkdir()
        self.output = self.root / 'signed'
        self.seed = '2a' * 32  # Synthetic fixture, never used as a release key.
        self.key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self.seed))
        self.public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        (self.assets / 'app.appimage').write_bytes(b'final package bytes')
        (self.assets / 'helper').write_bytes(b'helper bytes')
        self.record = dict(target='linux-gtk-x86_64', application_id='test.app', version='1.2.3', build=9,
                           archive='app.appimage', helper={'name': 'helper'})
        self.template = self.assets / 'update-linux-gtk-x86_64.unsigned.json'

    def sign(self, public=None):
        self.template.write_text(json.dumps(self.record))
        return module.sign_updates(self.assets, self.output, self.seed, public or self.public, 'test.app', 'v1.2.3')

    def test_exact_bytes_and_final_hash(self):
        self.sign()
        data = self.output / 'update-linux-gtk-x86_64.json'
        self.key.public_key().verify(bytes.fromhex(data.with_suffix('.json.sig').read_text()), data.read_bytes())
        release = json.loads(data.read_text())
        self.assertEqual(release['size'], len(b'final package bytes'))
        self.assertEqual(release['helper']['size'], len(b'helper bytes'))
        (self.assets / 'app.appimage').write_bytes(b'changed after notarization')
        self.sign()
        self.assertNotEqual(release['sha256'], json.loads(data.read_text())['sha256'])

    def test_wrong_key_identity_and_version_are_rejected(self):
        with self.assertRaises(ValueError): self.sign('00' * 32)
        for field in ('version', 'application_id'):
            before = self.record[field]; self.record[field] = 'wrong'
            with self.assertRaises(ValueError): self.sign()
            self.record[field] = before

    def test_traversal_symlink_and_missing_helper_are_rejected(self):
        self.record['archive'] = '../escape.appimage'
        with self.assertRaises(ValueError): self.sign()
        self.record['archive'] = 'app.appimage'
        (self.assets / 'helper').unlink()
        with self.assertRaises(ValueError): self.sign()
        (self.assets / 'helper').symlink_to(self.assets / 'app.appimage')
        with self.assertRaises(ValueError): self.sign()
