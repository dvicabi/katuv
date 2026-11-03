"""Windows script regression tests run against disposable installation directories."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows installer contract')
ROOT = Path(__file__).resolve().parents[1]


def powershell(script, *args):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(script), *map(str, args)], capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=40, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def installation(tmp_path):
    root = tmp_path / 'מחשב לבדיקה'
    root.mkdir()
    for name in ('Start-Katuv.ps1', 'Katuv.Common.ps1', 'Uninstall-Katuv.ps1'):
        shutil.copy2(ROOT/'packaging'/name, root/name)
    runtime = root/'runtime-1.2.0'
    runtime.mkdir()
    (runtime/'retained.txt').write_text('old installation')
    (runtime/'release-version.txt').write_text('1.2.0')
    (root/'active-runtime.txt').write_text(str(runtime), encoding='utf-8-sig')
    (root/'installation-id.txt').write_text('expected-id')
    (root/'projects').mkdir()
    (root/'projects'/'important.txt').write_text('keep my work')
    return root, runtime


def test_atomic_replacement_works_in_windows_powershell_51(tmp_path):
    root, _ = installation(tmp_path)
    script = root/'replace.ps1'
    script.write_text("""$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
$target = Join-Path $PSScriptRoot 'active-runtime.txt'
Set-KatuvText $target 'new runtime'
if ((Get-Content -LiteralPath $target -Raw -Encoding UTF8) -ne 'new runtime') { throw 'Text mismatch' }
Set-KatuvBytes $target ([byte[]](1, 2, 3))
if ([BitConverter]::ToString([IO.File]::ReadAllBytes($target)) -ne '01-02-03') { throw 'Bytes mismatch' }
""", encoding='utf-8-sig')
    result = powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_bad_package_does_not_modify_active_runtime_or_projects(tmp_path):
    root, runtime = installation(tmp_path)
    payload = tmp_path/'payload'; payload.mkdir()
    for name in ('Install-Katuv.ps1', 'Katuv.Common.ps1'):
        shutil.copy2(ROOT/'packaging'/name, payload/name)
    (payload/'release.json').write_text(json.dumps(dict(version='1.3.0', wheel='autocaption-1.3.0-py3-none-any.whl',
        files={'missing.whl':'0'*64})))
    before = (root/'active-runtime.txt').read_bytes()
    result = powershell(payload/'Install-Katuv.ps1', '-InstallRoot', root, '-NoLaunch', '-NoShortcuts')
    assert result.returncode != 0
    assert (root/'active-runtime.txt').read_bytes() == before
    assert (runtime/'retained.txt').read_text() == 'old installation'
    assert (root/'projects'/'important.txt').read_text() == 'keep my work'
    assert len(list(root.glob('runtime-*'))) == 1


def test_windows_powershell_ignores_incompatible_inherited_module_paths(tmp_path):
    root, _ = installation(tmp_path)
    script = root/'modules.ps1'
    script.write_text("""$ErrorActionPreference = 'Stop'
$env:PSModulePath = 'C:\\not-a-module-directory'
Remove-Module Microsoft.PowerShell.Utility -ErrorAction SilentlyContinue
. (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
$hash = Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'Katuv.Common.ps1') -Algorithm SHA256
if ($hash.Hash.Length -ne 64) { throw 'Built-in hashing unavailable' }
Get-Command Get-CimInstance,Expand-Archive,Get-AuthenticodeSignature -ErrorAction Stop | Out-Null
""", encoding='utf-8-sig')
    result = powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_managed_python_child_belongs_to_its_venv_parent(tmp_path):
    root, _ = installation(tmp_path)
    script = root/'process-tree.ps1'
    script.write_text("""$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Katuv.Common.ps1')
function Get-CimInstance {
    [pscustomobject]@{ProcessId=101;ParentProcessId=1;ExecutablePath=(Join-Path $PSScriptRoot 'runtime-1.3.0-a\\Scripts\\python.exe');CommandLine='python -m autocaption.web --port 8771'}
    [pscustomobject]@{ProcessId=102;ParentProcessId=101;ExecutablePath=(Join-Path $PSScriptRoot 'python\\cpython-3.11\\python.exe');CommandLine='python -m autocaption.web --port 8771'}
    [pscustomobject]@{ProcessId=103;ParentProcessId=999;ExecutablePath=(Join-Path $PSScriptRoot 'python\\cpython-3.11\\python.exe');CommandLine='python -m autocaption.web --port 8772'}
}
$servers = @(Get-KatuvServers $PSScriptRoot)
if ($servers.Count -ne 3) { throw 'Missing process' }
if ($servers[0].KatuvRuntimeName -ne 'runtime-1.3.0-a' -or $servers[1].KatuvRuntimeName -ne 'runtime-1.3.0-a') { throw 'Venv ownership lost across Windows Python launcher' }
if ($servers[2].KatuvRuntimeName) { throw 'Unrelated base Python process attributed to installation' }
""", encoding='utf-8-sig')
    result = powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr


def test_launcher_rejects_foreign_installation_even_when_app_name_matches(tmp_path):
    root, _ = installation(tmp_path)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(dict(app='katuv', app_version='1.2.0', runtime_id='runtime-1.2.0',
                                  installation_id='different-installation')).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = powershell(root/'Start-Katuv.ps1', '-NoBrowser', '-ConsoleErrors', '-Port', server.server_port)
        assert result.returncode != 0
        # The dummy runtime contains no Python: no launch should have been attempted.
        assert '-Port' in result.stdout
        assert not (root/'server.err.log').exists()
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_uninstall_keeps_projects_caches_and_unrelated_files(tmp_path):
    root, runtime = installation(tmp_path)
    (root/'cache').mkdir(); (root/'cache'/'weights.bin').write_bytes(b'cached')
    outside = tmp_path/'keep.txt'; outside.write_text('outside')
    result = powershell(root/'Uninstall-Katuv.ps1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert not runtime.exists()
    assert not (root/'active-runtime.txt').exists()
    assert (root/'projects'/'important.txt').read_text() == 'keep my work'
    assert (root/'cache'/'weights.bin').read_bytes() == b'cached'
    assert outside.read_text() == 'outside'


def test_uninstall_refuses_runtime_pointer_outside_installation(tmp_path):
    root, _ = installation(tmp_path)
    outside = tmp_path/'runtime-9.9.9'; outside.mkdir()
    keep = outside/'keep.txt'; keep.write_text('do not delete')
    (root/'active-runtime.txt').write_text(str(outside), encoding='utf-8-sig')
    result = powershell(root/'Uninstall-Katuv.ps1')
    assert result.returncode != 0
    assert keep.read_text() == 'do not delete'
    assert (root/'active-runtime.txt').exists()


def test_public_release_gate_rejects_unsigned_candidates(tmp_path):
    result = powershell(ROOT/'scripts/verify_public_release.ps1', '-Installer', tmp_path/'UNSIGNED.exe',
        '-Bundle', tmp_path/'UNSIGNED.zip', '-Acceptance', tmp_path/'acceptance.json',
        '-PublisherThumbprint', 'a'*40)
    assert result.returncode != 0
    assert 'unsigned' in (result.stdout + result.stderr).lower()
