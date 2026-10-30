import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sympy_check_smoke as smoke  # noqa:E402


@pytest.mark.parametrize('statement', ['from .release import __version__', 'from sympy.release import __version__'])
def test_version_admission_reads_only_a_literal_and_never_imports_package(tmp_path, statement):
    package = tmp_path / 'sympy'
    package.mkdir()
    (package / '__init__.py').write_text("raise RuntimeError('never import on host')\n" + statement + '\n')
    (package / 'release.py').write_text('__version__ = "1.7.dev"\n')
    assert smoke.read_version(tmp_path) == ('1.7.dev', 'sympy/release.py')
    (package / 'release.py').write_text('__version__ = __import__("os").getcwd()\n')
    with pytest.raises(ValueError, match='literal'):
        smoke.read_version(tmp_path)


@pytest.mark.parametrize('failure', ['callback', 'temporary_cleanup'])
def test_failure_after_qualified_status_cannot_leave_success(tmp_path, monkeypatch, failure):
    image_id = 'sha256:' + 'c' * 64
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        data = b''
        if args[:2] == ['git', 'rev-parse']:
            data = ('a' * 40).encode()
        elif args[:3] == ['docker', 'image', 'inspect']:
            data = json.dumps([{'Id': image_id, 'RepoDigests': [smoke.IMAGE],
                                'Os': 'linux', 'Architecture': 'amd64'}]).encode()
        elif args[:2] == ['docker', 'inspect']:
            data = json.dumps([{'Image': image_id, 'Mounts': [], 'Config': {'User': '65534:65534'},
                'State': {'Running': False}, 'HostConfig': {'ReadonlyRootfs': True, 'NetworkMode': 'none',
                'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'], 'Memory': 1024**3,
                'NanoCpus': 2_000_000_000, 'PidsLimit': 128}}]).encode()
        elif args[:2] == ['docker', 'cp']:
            root = Path(args[-1])
            for relative in smoke.SYMPY_RUNNER_PATHS:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# synthetic bytes, never executed\n')
            (root / 'sympy/__init__.py').write_text('__version__ = "1.7.dev"\n')
        return SimpleNamespace(returncode=0, stdout=data, stderr=b'')

    monkeypatch.setattr(smoke.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(smoke.subprocess, 'run', run)
    real_temporary = smoke.tempfile.TemporaryDirectory

    @contextmanager
    def temporary(**kwargs):
        with real_temporary(**kwargs) as directory:
            yield directory
        if failure == 'temporary_cleanup':
            raise OSError('synthetic cleanup error after successful callback')

    monkeypatch.setattr(smoke.tempfile, 'TemporaryDirectory', temporary)

    def callback(workspace, identity, calibration, report, output):
        report['status'] = 'scripted_full_source_native_sympy_task_journey_qualified'
        if failure == 'callback':
            raise ValueError('synthetic callback error after qualified status')

    report = smoke.smoke(tmp_path / 'output', host_callback=callback)
    assert report['status'] == 'not_qualified' and report['error_type'] in {'ValueError', 'OSError'}
    assert report['source_container_cleanup_confirmed'] is True
    assert any(args[:3] == ['docker', 'rm', '-f'] for args in calls)
