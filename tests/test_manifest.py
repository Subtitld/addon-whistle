"""Manifest <-> code guards.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The host reads `display_name`, `languages`, `config_schema`, `tasks`,
`executable`, `protocol`, `startup_timeout_sec` and `platforms` straight from
manifest.json, never from the running add-on. These tests keep the manifest
and the code from drifting apart.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import whistle_addon  # noqa: E402
from whistle_addon import __main__ as addon  # noqa: E402
from whistle_addon import engine  # noqa: E402

MANIFEST = json.loads((REPO_ROOT / 'manifest.json').read_text(encoding='utf-8'))
FIELDS = {f['key']: f for f in MANIFEST['config_schema']['fields']}
SOURCE = (REPO_ROOT / 'whistle_addon' / '__main__.py').read_text(encoding='utf-8')
PYPROJECT = (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FETCH = _load_tool('fetch_assets')


def test_identity():
    assert MANIFEST['id'] == 'whistle' == addon.ADDON_ID == whistle_addon.ADDON_ID
    assert MANIFEST['display_name'] == 'Whistle (offline)'
    assert MANIFEST['tasks'] == ['asr.transcribe']
    assert MANIFEST['protocol'] == 1 == addon.PROTOCOL_VERSION
    assert MANIFEST['manifest_version'] == 1


def test_all_version_sites_agree():
    pyproject_version = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, re.M).group(1)
    versions = {
        'manifest.json': MANIFEST['version'],
        'pyproject.toml': pyproject_version,
        '__init__.__version__': whistle_addon.__version__,
        '__main__.ADDON_VERSION': addon.ADDON_VERSION,
    }
    assert len(set(versions.values())) == 1, versions


def test_languages_match_the_engine():
    assert MANIFEST['languages'] == list(engine.LANGUAGES)
    assert len(set(MANIFEST['languages'])) == len(MANIFEST['languages']) == 7


def test_the_model_is_bundled_and_pinned():
    (model,) = MANIFEST['models']
    assert model['file'] == addon.WEIGHTS_FILE == 'whistle.cact'
    assert model['bundled'] is True
    assert model['license'] == 'Apache-2.0'
    assert round(FETCH.WEIGHTS_SIZE / 1_000_000) == model['size_mb']


def test_every_manifest_platform_has_a_pinned_engine():
    prefixes = {key.split('-')[0] for key in FETCH.PLATFORMS}
    assert set(MANIFEST['platforms']) <= prefixes, (MANIFEST['platforms'], sorted(FETCH.PLATFORMS))
    for key, pin in FETCH.PLATFORMS.items():
        assert re.fullmatch(r'[0-9a-f]{64}', pin['sha256']), key
        assert pin['member'].endswith('/' + pin['library']), key


def test_every_config_field_is_actually_read():
    """A declared field whose env var is never read is a dead Configure box."""
    prefix = MANIFEST['id'].upper() + '_'
    for field in MANIFEST['config_schema']['fields']:
        env_name = prefix + field['key'].upper()
        assert f"'{env_name}'" in SOURCE, (
            f"config field {field['key']!r} declares {env_name}, but nothing reads it")


def test_config_fields_use_renderable_types():
    """The Configure dialog renders bool, select, file and string; anything else is a text box."""
    allowed = {'bool', 'select', 'int', 'file', 'string', 'secret', 'password'}
    for field in MANIFEST['config_schema']['fields']:
        assert field['type'] in allowed, field


def test_no_int_fields_because_the_dialog_cannot_render_them():
    assert all(f['type'] != 'int' for f in MANIFEST['config_schema']['fields'])


def test_help_key_is_help_not_description():
    """`description` is ignored by the renderer; only `help` shows."""
    for field in MANIFEST['config_schema']['fields']:
        assert 'description' not in field, field
        assert field.get('help'), field


def test_keywords_is_a_free_text_field():
    assert FIELDS['keywords']['type'] == 'string'
    assert FIELDS['keywords']['default'] == ''


def test_no_module_of_the_add_on_loads_the_package_or_the_network():
    """Telemetry and runtime downloads are what this add-on was built to avoid."""
    banned = re.compile(r'^\s*(import|from)\s+(needle|urllib|http|socket|requests|huggingface_hub|ssl)\b', re.M)
    for path in (REPO_ROOT / 'whistle_addon').glob('*.py'):
        assert not banned.search(path.read_text(encoding='utf-8')), path.name


def test_the_runtime_dependencies_are_numpy_only():
    block = re.search(r'^dependencies = \[(.*?)\]', PYPROJECT, re.M | re.S).group(1)
    assert re.findall(r'"([^"]+)"', block) == ['numpy>=1.24']

