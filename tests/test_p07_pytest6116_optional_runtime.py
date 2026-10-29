"""Optional image inventory is audited without executing wheel code on the host."""
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_py38_repro import (  # noqa: E402
    FIXED_SCHEME,
    MARKUPSAFE_PROBE,
    SCHEME_PROBE,
    audit_relocated_wheels,
    run,
)
from test_p07_pytest6116_wheel_install_inventory import wheel  # noqa: E402


def fake_installed_image(tmp_path):
    raw, item = wheel({}, '[console_scripts]\nexample = example:main\n')
    wheelhouse = tmp_path / 'wheelhouse'
    wheelhouse.mkdir()
    (wheelhouse / item['filename']).write_bytes(raw)
    image = tmp_path / 'image'
    site = image / 'usr/local/lib/python3.8/site-packages'
    (site / 'example').mkdir(parents=True)
    (site / 'example/__init__.py').write_bytes(b'value = 1\n')
    info = site / 'example-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_bytes(b'Name: example\nVersion: 1.0\n\n')
    (info / 'WHEEL').write_bytes(b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n')
    (info / 'entry_points.txt').write_bytes(b'[console_scripts]\nexample = example:main\n')
    (info / 'RECORD').write_bytes(b'generated')
    (info / 'INSTALLER').write_bytes(b'pip\n')
    scripts = image / 'usr/local/bin'
    scripts.mkdir(parents=True)
    (scripts / 'example').write_bytes(b'generated wrapper, not publisher bytes')

    def command(args):
        assert args[1] == 'cp'
        source = image / args[2].split(':', 1)[1].lstrip('/')
        destination = Path(args[3])
        if not source.exists():
            return SimpleNamespace(returncode=1)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copyfile(source, destination)
        return SimpleNamespace(returncode=0)

    return wheelhouse, {'wheels': [item]}, image, command


def test_relocated_inventory_verifies_publisher_bytes_but_not_generated_wrappers(tmp_path):
    wheelhouse, profile, _, command = fake_installed_image(tmp_path)
    verified, actual, extras, wrappers = audit_relocated_wheels(command, 'a' * 64, wheelhouse, profile,
                                                                tmp_path / 'extracted')
    assert verified['publisher_files_verified'] == 4
    assert verified['ordinary_acceptance'] is False
    assert len(actual) == 7
    assert len(extras) == 2
    assert wrappers['/usr/local/bin/example']['content_verified'] is False


def test_relocated_inventory_fails_on_unknown_or_modified_installed_files(tmp_path):
    wheelhouse, profile, image, command = fake_installed_image(tmp_path)
    (image / 'usr/local/lib/python3.8/site-packages/example/unexpected.py').write_text('unexpected')
    with pytest.raises(ValueError, match='unexpected file'):
        audit_relocated_wheels(command, 'a' * 64, wheelhouse, profile, tmp_path / 'extracted1')
    (image / 'usr/local/lib/python3.8/site-packages/example/unexpected.py').unlink()
    (image / 'usr/local/lib/python3.8/site-packages/example/__init__.py').write_text('tampered')
    with pytest.raises(ValueError, match='publisher file differs'):
        audit_relocated_wheels(command, 'a' * 64, wheelhouse, profile, tmp_path / 'extracted2')


def test_optional_requires_fixed_execution_mode_and_native_probe_is_separate(tmp_path):
    with pytest.raises(ValueError, match='requires the full'):
        run(tmp_path / 'no-full', with_optional=True)
    with pytest.raises(ValueError, match='requires pexpect'):
        run(tmp_path / 'no-source', full_public=True, with_optional=True)
    assert FIXED_SCHEME['pip'] == '23.0.1'
    assert FIXED_SCHEME['scripts'] == '/usr/local/bin'
    compile(SCHEME_PROBE, '<scheme>', 'exec')
    compile(MARKUPSAFE_PROBE, '<native>', 'exec')
