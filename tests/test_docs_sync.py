"""README and .env.example must describe the settings the code actually reads."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def code_env_vars():
    names = set()
    for path in (ROOT / 'studio').glob('*.py'):
        source = path.read_text()
        names |= set(re.findall(r"environ(?:\.get)?\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]", source))
        names |= set(re.findall(r"_env\(environ,\s*['\"]([A-Z][A-Z0-9_]+)['\"]", source))
    return names


def test_documented_settings_cover_code_environment():
    documented = (ROOT / '.env.example').read_text()
    missing = sorted(name for name in code_env_vars()
                     if name not in documented and name != 'STUDIO_DATA_DIR')
    assert missing == [], f'.env.example 누락: {missing}'


def test_readme_mentions_user_facing_image_and_automation_settings():
    readme = (ROOT / 'README.md').read_text()
    for token in ('IMAGE_MODE', 'IMAGE_BASE_URL', 'IMAGE_MODEL', 'IMAGE_API_KEY',
                  'IMAGE_DAILY_LIMIT', 'IMAGE_TIMEOUT_SECONDS',
                  'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_ALLOWED_USER_ID'):
        assert token in readme, token


def test_readme_length_limit_matches_writing_policy():
    from studio.writing_policy import LENGTHS
    readme = (ROOT / 'README.md').read_text()
    limit = max(LENGTHS.values())
    assert f'{limit:,}' in readme or str(limit) in readme, \
        '최대 글 길이 안내가 실제 제한과 다름'
    assert '1,200~2,000자' not in readme or max(LENGTHS.values()) <= 2000


def test_readme_lists_the_automation_test_commands():
    readme = (ROOT / 'README.md').read_text()
    assert 'tests/automation-dom.cjs' in readme
    assert 'tests/e2e_automation.mjs' in readme


def test_readme_does_not_claim_images_are_unsupported():
    readme = (ROOT / 'README.md').read_text()
    assert '이미지 수집·업로드는 하지 않음' not in readme
