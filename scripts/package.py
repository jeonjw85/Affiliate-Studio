"""Release an allowlisted, credential-free source archive and verify its bytes."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPLICIT = ['README.md', 'requirements.txt', 'requirements.lock', 'run.py', 'start.sh', 'Start.command', '.gitignore', 'package.json', 'package-lock.json', '.env.example', 'Dockerfile', 'compose.yaml', '.dockerignore']


def main():
    files = [ROOT / p for p in EXPLICIT]
    for folder in ('studio', 'tests', 'scripts'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.js', '.mjs', '.cjs', '.html', '.css'))
    assert all(p.is_file() for p in files), 'Release document/source missing'
    output = ROOT / 'dist'; output.mkdir(exist_ok=True)
    target = output / 'affiliate-studio-0.4.1.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(set(files)):
            z.write(p, 'affiliate-studio/' + p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        for p in sorted(set(files)):
            assert z.read('affiliate-studio/' + p.relative_to(ROOT).as_posix()) == p.read_bytes()
        assert not any('/data/' in x or ('/.env' in x and x != 'affiliate-studio/.env.example') or 'node_modules' in x or '/.venv/' in x for x in z.namelist())
        report = dict(path=str(target), entries=len(z.namelist()), bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest(), matches_source=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
