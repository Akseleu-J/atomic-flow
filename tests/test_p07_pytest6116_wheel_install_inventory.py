"""Relocation expectations are bounded, byte-exact and do not admit wrappers."""
import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_wheel_install_inventory import (  # noqa: E402
    merge_plans,
    publisher_wheel_plan,
    verify_publisher_bytes,
)


def wheel(extra, entry_points=''):
    info = 'example-1.0.dist-info'
    files = {'example/__init__.py': b'value = 1\n',
             info + '/METADATA': b'Name: example\nVersion: 1.0\n\n',
             info + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n',
             **extra}
    if entry_points:
        files[info + '/entry_points.txt'] = entry_points.encode()
    rows = []
    for name, data in files.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        rows.append((name, 'sha256=' + encoded, str(len(data))))
    rows.append((info + '/RECORD', '', ''))
    record = io.StringIO(newline='')
    csv.writer(record).writerows(rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(info + '/RECORD', record.getvalue())
    raw = out.getvalue()
    return raw, {'name': 'example', 'version': '1.0', 'filename': 'example-1.0-py3-none-any.whl',
                 'sha256': hashlib.sha256(raw).hexdigest()}


def test_data_and_script_relocation_and_shebang_are_byte_exact():
    paths = {'example-1.0.data/data/man/tool.1': b'manpage\n',
             'example-1.0.data/scripts/sample': b'#!python\nprint(1)\n',
             'example-1.0.data/scripts/shell': b'#!/bin/sh\necho yes\n'}
    raw, item = wheel(paths,
                      '[console_scripts]\nentry = example:main\n')
    plan = merge_plans([publisher_wheel_plan(raw, item, allowed_relocations=frozenset(paths))])
    files = plan['publisher_files']
    assert files['/usr/local/man/tool.1']['sha256'] == hashlib.sha256(b'manpage\n').hexdigest()
    script = files['/usr/local/bin/sample']
    assert script['kind'] == 'wheel_script_shebang_rewritten'
    assert script['sha256'] == hashlib.sha256(b'#!/usr/local/bin/python\nprint(1)\n').hexdigest()
    assert files['/usr/local/bin/shell']['sha256'] == hashlib.sha256(b'#!/bin/sh\necho yes\n').hexdigest()
    assert plan['generated_console_scripts']['/usr/local/bin/entry']['expected_sha256'] is None
    assert plan['generated_scripts_bytes_verified'] is False


@pytest.mark.parametrize('member', ['example-1.0.data/headers/file.h', 'example-1.0.data/scripts/a/b',
                                    'else-1.0.data/data/file', '../escape'])
def test_unexpected_or_escaping_paths_are_refused(member):
    raw, item = wheel({member: b'content'})
    with pytest.raises(ValueError):
        publisher_wheel_plan(raw, item)


def test_console_wrapper_collision_and_cross_wheel_collision_are_refused():
    raw, item = wheel({'example-1.0.data/scripts/entry': b'#!python\n'},
                      '[console_scripts]\nentry = example:main\n')
    with pytest.raises(ValueError, match='overlaps'):
        publisher_wheel_plan(raw, item, allowed_relocations=frozenset({'example-1.0.data/scripts/entry'}))
    raw, item = wheel({})
    plan = publisher_wheel_plan(raw, item)
    with pytest.raises(ValueError, match='collision'):
        merge_plans([plan, plan])


@pytest.mark.parametrize('header', [b'#!pythonw\n', b'#!python -x\n', b'#!python\r\n'])
def test_python_shebang_variants_are_rejected(header):
    member = 'example-1.0.data/scripts/tool'
    raw, item = wheel({member: header + b'print(1)\n'})
    with pytest.raises(ValueError, match='shebang variant'):
        publisher_wheel_plan(raw, item, allowed_relocations=frozenset({member}))


@pytest.mark.parametrize('name', ['python', 'python3', 'python3.8'])
def test_scripts_cannot_overwrite_python_interpreter(name):
    member = 'example-1.0.data/scripts/' + name
    raw, item = wheel({member: b'#!python\n'})
    with pytest.raises(ValueError, match='interpreter'):
        publisher_wheel_plan(raw, item, allowed_relocations=frozenset({member}))


def test_pip_filtered_wrapper_alias_is_rejected():
    member = 'example-1.0.data/scripts/entry-script.py'
    raw, item = wheel({member: b'#!python\n'}, '[console_scripts]\nentry = example:main\n')
    with pytest.raises(ValueError, match='overlaps'):
        publisher_wheel_plan(raw, item, allowed_relocations=frozenset({member}))


def test_actual_publisher_bytes_are_required_without_promoting_generated_wrappers():
    raw, item = wheel({})
    plan = merge_plans([publisher_wheel_plan(raw, item)])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        observed = {name: archive.read(row['wheel_member']) for name, row in plan['publisher_files'].items()}
    assert verify_publisher_bytes(plan, observed)['ordinary_acceptance'] is False
    observed['/usr/local/lib/python3.8/site-packages/example/__init__.py'] = b'tampered'
    with pytest.raises(ValueError, match='differs'):
        verify_publisher_bytes(plan, observed)
    observed.pop('/usr/local/lib/python3.8/site-packages/example/__init__.py')
    with pytest.raises(ValueError, match='inventory'):
        verify_publisher_bytes(plan, observed)
