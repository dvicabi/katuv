"""Build a private-data-free Windows distribution from an explicit allowlist."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output=None, *, exe=False, iscc=None, signed=False):
    output = Path(output or ROOT/'dist')
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if signed and (dirty or not os.environ.get('KATUV_SIGN_CERT_SHA1')):
        raise ValueError('Signed releases require a clean checkout and KATUV_SIGN_CERT_SHA1. Unsigned builds are internal candidates only.')
    if exe:
        iscc = iscc or os.environ.get('KATUV_ISCC') or shutil.which('ISCC.exe')
        if not iscc:
            raise ValueError('Inno Setup 7.1.1 compiler missing. Set KATUV_ISCC or use --iscc; the Windows CI builds isolated candidates.')
    with tempfile.TemporaryDirectory(prefix='katuv-release-') as temporary:
        temp = Path(temporary)
        # Build from fresh allowed inputs so removed modules cannot survive in a
        # previous setuptools build/lib directory or enter the next release.
        source = temp/'source'
        package = source/'autocaption'
        package.mkdir(parents=True)
        for name in ('pyproject.toml', 'README.md', 'LICENSE'):
            shutil.copy2(ROOT/name, source/name)
        for path in (ROOT/'autocaption').glob('*.py'):
            shutil.copy2(path, package/path.name)
        for directory in ('fonts', 'web_static', 'assets'):
            (package/directory).mkdir()
            for path in (ROOT/'autocaption'/directory).iterdir():
                if path.is_file():
                    shutil.copy2(path, package/directory/path.name)
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(temp), str(source)], check=True)
        wheel = next(temp.glob('autocaption-*.whl'))
        version = wheel.name.split('-')[1]
        staging = temp/f'Katuv-{version}-Windows'
        staging.mkdir()
        shutil.copy2(wheel, staging/wheel.name)
        for name in ('Install.cmd', 'Install-Katuv.ps1', 'Start-Katuv.ps1', 'Katuv.Common.ps1',
                     'Uninstall-Katuv.ps1', 'requirements-windows.txt', 'START-HERE.html'):
            shutil.copy2(ROOT/'packaging'/name, staging/name)
        for source, name in [('LICENSE','LICENSE.txt'), ('THIRD_PARTY.md','THIRD_PARTY.md'), ('autocaption/assets/downloads.json','downloads.json')]:
            shutil.copy2(ROOT/source, staging/name)
        # Windows PowerShell 5.1 needs BOM to read Hebrew source correctly.
        for script in staging.glob('*.ps1'):
            script.write_text(script.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
            if signed:
                subprocess.run(['powershell.exe', '-NoProfile', '-File', str(ROOT/'scripts/sign_file.ps1'),
                                '-Path', str(script)], check=True)
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(staging.iterdir())}
        (staging/'release.json').write_text(json.dumps(dict(version=version, wheel=wheel.name, files=files,
            source_commit=commit, source_dirty=dirty, signed=signed,
            publisher_thumbprint=os.environ.get('KATUV_SIGN_CERT_SHA1') if signed else None), indent=2), encoding='utf-8')
        suffix = '' if signed else '-UNSIGNED'
        target = output/(staging.name+suffix+'.zip')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for path in staging.iterdir():
                bundle.write(path, staging.name+'/'+path.name)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n', encoding='ascii')
        from check_release import check
        check(target)
        print(target)
        if exe:
            command = [str(iscc), f'/DReleaseVersion={version}', f'/DPayloadDir={staging}',
                       f'/DOutputDir={output}']
            if signed:
                signer = ROOT/'scripts/sign_file.ps1'
                command.extend(['/DSignedRelease',
                    '/Skatuv=powershell.exe -NoProfile -File $q'+str(signer)+'$q -Path $f'])
            command.append(str(ROOT/'packaging/Katuv.iss'))
            subprocess.run(command, check=True)
            installer = output/(staging.name+suffix+'.exe')
            digest = hashlib.sha256(installer.read_bytes()).hexdigest()
            installer.with_suffix('.exe.sha256').write_text(digest+'  '+installer.name+'\n', encoding='ascii')
            print(installer)
        return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--exe', action='store_true')
    parser.add_argument('--iscc')
    parser.add_argument('--signed', action='store_true')
    args = parser.parse_args()
    build(args.output, exe=args.exe, iscc=args.iscc, signed=args.signed)
