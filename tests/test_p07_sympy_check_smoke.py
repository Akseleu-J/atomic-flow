import sys
from pathlib import Path

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
