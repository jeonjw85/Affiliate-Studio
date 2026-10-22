"""Release contents and local-only documentation boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT_FILES = (
    'README.md', 'requirements.txt', 'requirements.lock', 'run.py',
    'start.sh', 'Start.command', '.gitignore', 'package.json', 'package-lock.json',
    '.env.example', 'Dockerfile', 'compose.yaml', '.dockerignore',
)
LOCAL_ONLY_FILES = (
    'PLAN.md', 'VERIFICATION.md', 'AGENTS.md', 'CLAUDE.md', 'GEMINI.md',
    'BACKEND_TDD.md', 'UI_TDD.md', 'FIX_VERIFY.md',
    '.hermes/plans/local.md', '.claude/notes.md', '.codex/notes.md',
    '.env', '.env.production', '.env.backup',
)


@pytest.mark.parametrize('local_notes_present', [False, True])
def test_release_contains_only_public_sources(tmp_path, local_notes_present):
    project = tmp_path / 'project'
    project.mkdir()
    for name in PUBLIC_ROOT_FILES:
        shutil.copy2(ROOT / name, project / name)
    for name in ('scripts/package.py', 'studio/__init__.py'):
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    if local_notes_present:
        for name in LOCAL_ONLY_FILES:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('Internal test fixture, not release documentation.\n')
    result = subprocess.run(
        [sys.executable, str(project / 'scripts/package.py')],
        cwd=project, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    with zipfile.ZipFile(report['path']) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        expected = {
            'affiliate-studio/' + name
            for name in (*PUBLIC_ROOT_FILES, 'scripts/package.py', 'studio/__init__.py')
        }
        assert names == expected
        assert report['entries'] == len(expected)
        for name in names:
            relative = name.removeprefix('affiliate-studio/')
            assert archive.read(name) == (project / relative).read_bytes()


def test_gitignore_excludes_local_notes_but_allows_readme(tmp_path):
    shutil.copy2(ROOT / '.gitignore', tmp_path / '.gitignore')
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True, capture_output=True)
    result = subprocess.run(
        ['git', '-C', str(tmp_path), 'check-ignore', '--stdin'],
        input='\n'.join((*LOCAL_ONLY_FILES, 'README.md', '.env.example')) + '\n',
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(LOCAL_ONLY_FILES)
