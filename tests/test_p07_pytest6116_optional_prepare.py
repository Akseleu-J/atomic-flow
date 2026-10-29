"""Candidate wheels are bytes-only, target-bound and fail closed on new plugins."""
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_optional_prepare as optional  # noqa: E402


def row(name, version, requires=(), requires_python=None):
    return {'name': name, 'version': version, 'requires_dist': list(requires),
            'requires_python': requires_python}


def test_closure_rejects_missing_incompatible_and_duplicate_distributions():
    rows = [row('sample', '1.0', ['dep>=1,<2', 'ignored; python_version < "3.8"']), row('dep', '1.4')]
    assert optional.closure(rows) == [{'from': 'sample', 'requirement': 'dep<2,>=1', 'resolved': '1.4'}]
    with pytest.raises(ValueError, match='unresolved active dependency'):
        optional.closure(rows[:1])
    with pytest.raises(ValueError, match='unresolved active dependency'):
        optional.closure([rows[0], row('dep', '2.0')])
    with pytest.raises(ValueError, match='unresolved active dependency'):
        optional.closure([row('sample', '1', ['dep[extra]>=1']), row('dep', '1.4')])
    with pytest.raises(ValueError, match='duplicate distribution'):
        optional.closure([row('sample', '1'), row('SAMPLE', '1')])
    with pytest.raises(ValueError, match='excludes target Python'):
        optional.closure([row('sample', '1', requires_python='>=3.9')])


def test_target_wheel_tags_reject_wrong_python_and_architecture():
    optional.validate_tags({'name': 'MarkupSafe', 'version': '2.0.1',
                            'filename': 'MarkupSafe-2.0.1-cp38-cp38-manylinux2010_x86_64.whl'})
    for filename in ('MarkupSafe-2.0.1-cp39-cp39-manylinux2010_x86_64.whl',
                     'MarkupSafe-2.0.1-cp38-cp38-manylinux2010_aarch64.whl'):
        with pytest.raises(ValueError, match='target compatibility'):
            optional.validate_tags({'name': 'MarkupSafe', 'version': '2.0.1', 'filename': filename})


def test_existing_baseline_plugin_is_reported_but_new_plugin_is_refused(tmp_path):
    wheel = tmp_path / 'candidate.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('pkg-1.dist-info/entry_points.txt', '[pytest11]\nplug = pkg:plugin\n')
        archive.writestr('pkg-1.data/scripts/tool', 'script')
    paths, scripts, plugins = optional.inspect_data_paths(wheel, allow_pytest11=True)
    assert paths == ['pkg-1.data/scripts/tool']
    assert not scripts and plugins == {'plug': 'pkg:plugin'}
    with pytest.raises(ValueError, match='pytest11 plugin'):
        optional.inspect_data_paths(wheel)


def test_existing_directory_or_modified_base_fails_before_network(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='fresh output'):
        optional.prepare(tmp_path)
    base = tmp_path / 'base.json'
    profile = tmp_path / 'profile.json'
    base.write_text('{}')
    profile.write_text(json.dumps({'task_id': 'pytest-dev__pytest-6116', 'python_version': '3.8.20',
                                   'base_profile_sha256': '0' * 64}))
    monkeypatch.setattr(optional, 'BASE', base)
    monkeypatch.setattr(optional, 'PROFILE', profile)
    with pytest.raises(ValueError, match='byte-bound base'):
        optional.prepare(tmp_path / 'result')


def test_pexpect_candidate_extends_only_the_fixed_optional_wheels():
    plain = json.loads(optional.PROFILE.read_bytes())
    combined = json.loads(optional.PEXPECT_PROFILE.read_bytes())
    base = json.loads(optional.PEXPECT_BASE.read_bytes())
    assert combined['wheels'] == plain['wheels']
    assert len(base['wheels']) == 13
    assert {item['name'] for item in base['wheels'][-2:]} == {'pexpect', 'ptyprocess'}
