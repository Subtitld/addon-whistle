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
WORKFLOW = (REPO_ROOT / '.github' / 'workflows' / 'release.yml').read_text(encoding='utf-8')


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


def test_the_add_on_has_no_runtime_dependencies():
    """Standard library and ffmpeg only: nothing here is a licence to track."""
    block = re.search(r'^dependencies = \[(.*?)\]', PYPROJECT, re.M | re.S).group(1)
    assert re.findall(r'"([^"]+)"', block) == []



def test_the_release_builds_every_platform_the_manifest_declares():
    built = re.findall(r'^\s+platform_label:\s*(\S+)\s*$', WORKFLOW, re.M)
    assert built, 'no build matrix in release.yml'
    assert {label.split('-')[0] for label in built} == set(MANIFEST['platforms'])
    assert set(built) <= set(FETCH.PLATFORMS), 'a built platform has no pinned engine'
    published = re.search(r'for label in ([^;]+); do', WORKFLOW).group(1).split()
    assert sorted(published) == sorted(built), 'the release job checks other platforms than it builds'


def test_the_release_names_archives_the_way_the_catalog_reads_them():
    """The catalog parses `<id>-<version>-<platform>.zip` plus a `.sha256` sidecar."""
    assert f'archive="{MANIFEST["id"]}-${{version}}-${{{{ matrix.platform_label }}}}.zip"' in WORKFLOW
    assert '${ARCHIVE_NAME}.sha256' in WORKFLOW


def test_the_release_proves_the_installed_binary_and_refuses_skips():
    assert 'WHISTLE_TEST_BINARY' in WORKFLOW and 'tests/test_installed.py' in WORKFLOW
    assert WORKFLOW.count('skipped') >= 2
    assert 'tools/assemble_licences.py' in WORKFLOW
    assert WORKFLOW.index('tools/fetch_assets.py --platform ${{ matrix.platform_label }}') < WORKFLOW.index('pyinstaller pyinstaller.spec')


def test_every_licence_rule_names_a_notice_that_is_committed_or_harvested():
    """Rules for libraries the spec excludes may point at notices not committed;
    the tool then fails the build, which is the point. The rest must exist."""
    tool = _load_tool('assemble_licences')
    harvested = {'python/LICENSE.txt', 'pyinstaller/COPYING.txt'}
    excluded = {'OpenSSL', 'bzip2', 'XZ Utils', 'zstd', 'mpdecimal'}
    for _pattern, notice, component in tool.RULES:
        if component in excluded or notice in harvested:
            continue
        assert (REPO_ROOT / 'THIRD_PARTY_LICENSES' / notice).is_file(), notice
