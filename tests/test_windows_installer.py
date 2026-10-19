"""Run the real NSIS setup script with fixture downloads and no machine installation."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ACTION = yaml.safe_load((Path(__file__).parents[1] /
                         '.github/actions/setup-day-deps/action.yml').read_text())
INSTALL = next(s['run'] for s in ACTION['runs']['steps']
               if s.get('name') == 'NSIS (windows pack)')
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')
PAYLOAD = b'verified installer fixture'

MOCKS = r'''
$ErrorActionPreference = 'Stop'
$script:installed = $env:FIXTURE_MODE -eq 'already-installed'
function curl.exe {
    $args -join ' ' | Set-Content -LiteralPath $env:DOWNLOAD_LOG
    $destination = $args[[Array]::IndexOf($args, '-o') + 1]
    $payload = if ($env:FIXTURE_MODE -eq 'bad-checksum') { 'invalid bytes' } else { 'verified installer fixture' }
    [IO.File]::WriteAllBytes($destination, [Text.Encoding]::UTF8.GetBytes($payload))
    $global:LASTEXITCODE = if ($env:FIXTURE_MODE -eq 'download-failed') { 22 } else { 0 }
}
function choco { throw 'Chocolatey must not be contacted' }
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    # Refuse to simulate execution of anything except the verified cached bytes.
    if ((Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash -ne $env:NSIS_SHA256) {
        throw 'Attempted to execute unverified bytes'
    }
    $FilePath | Set-Content -LiteralPath $env:INSTALL_LOG
    $script:installed = $env:FIXTURE_MODE -ne 'missing-executable'
    [pscustomobject]@{ ExitCode = $(if ($env:FIXTURE_MODE -eq 'installer-failed') { 7 } else { 0 }) }
}
function Get-ChildItem {
    param($Path, [switch]$Recurse, $Filter, $ErrorAction)
    if ($script:installed) { [pscustomobject]@{ FullName = $env:FAKE_MAKENSIS } }
}
'''


@unittest.skipUnless(POWERSHELL, 'PowerShell is required to execute the installer script')
class WindowsInstallerTests(unittest.TestCase):
    def run_installer(self, cached=None, mode='normal'):
        with tempfile.TemporaryDirectory(prefix='nsis fixture ') as directory:
            root = Path(directory)
            setup = root / 'day-installers/nsis-3.13-setup.exe'
            setup.parent.mkdir()
            if cached is not None:
                setup.write_bytes(cached)
            (root / 'NSIS').mkdir()
            script = root / 'run.ps1'
            script.write_text(MOCKS + '\n' + INSTALL, encoding='utf-8')
            env = dict(os.environ, NSIS_INSTALLER=str(setup), NSIS_VERSION='3.13',
                       NSIS_SHA256=hashlib.sha256(PAYLOAD).hexdigest(),
                       ProgramFiles=str(root), FIXTURE_MODE=mode,
                       FAKE_MAKENSIS=str(root / 'NSIS/makensis.exe'),
                       GITHUB_ENV=str(root / 'env'), DOWNLOAD_LOG=str(root / 'download'),
                       INSTALL_LOG=str(root / 'install'))
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive',
                                     '-File', str(script)], env=env, capture_output=True, text=True)
            return dict(result=result, downloaded=(root / 'download').exists(),
                        installed=(root / 'install').exists(),
                        exported=(root / 'env').read_text(encoding='utf-8-sig')
                        if (root / 'env').exists() else '',
                        cached=setup.read_bytes() if setup.exists() else None,
                        partial=Path(str(setup) + '.download').exists())

    def assert_success(self, outcome):
        self.assertEqual(outcome['result'].returncode, 0, outcome['result'].stderr)
        self.assertIn('DAY_MAKENSIS=', outcome['exported'])
        self.assertEqual(outcome['cached'], PAYLOAD)
        self.assertFalse(outcome['partial'])

    def test_cache_hit_installs_without_network(self):
        outcome = self.run_installer(cached=PAYLOAD)
        self.assert_success(outcome)
        self.assertFalse(outcome['downloaded'])
        self.assertTrue(outcome['installed'])

    def test_cache_miss_downloads_and_installs(self):
        outcome = self.run_installer()
        self.assert_success(outcome)
        self.assertTrue(outcome['downloaded'])
        self.assertTrue(outcome['installed'])

    def test_corrupt_cache_is_replaced_before_execution(self):
        outcome = self.run_installer(cached=b'corrupt cached bytes')
        self.assert_success(outcome)
        self.assertTrue(outcome['downloaded'])
        self.assertTrue(outcome['installed'])

    def test_invalid_downloads_are_never_installed_or_cached(self):
        for mode in ('bad-checksum', 'download-failed'):
            with self.subTest(mode=mode):
                outcome = self.run_installer(mode=mode)
                self.assertNotEqual(outcome['result'].returncode, 0)
                self.assertTrue(outcome['downloaded'])
                self.assertFalse(outcome['installed'])
                self.assertFalse(outcome['exported'])
                self.assertIsNone(outcome['cached'])
                self.assertFalse(outcome['partial'])

    def test_failed_install_does_not_export_success(self):
        for mode in ('installer-failed', 'missing-executable'):
            with self.subTest(mode=mode):
                outcome = self.run_installer(cached=PAYLOAD, mode=mode)
                self.assertNotEqual(outcome['result'].returncode, 0)
                self.assertTrue(outcome['installed'])
                self.assertFalse(outcome['exported'])

    def test_existing_install_needs_no_network_or_reinstall(self):
        outcome = self.run_installer(cached=PAYLOAD, mode='already-installed')
        self.assert_success(outcome)
        self.assertFalse(outcome['downloaded'])
        self.assertFalse(outcome['installed'])


if __name__ == '__main__':
    unittest.main()
