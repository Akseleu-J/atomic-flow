"""Audit pinned NumPy publisher bytes only; do not import target code or admit runtime."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import stat
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-numpy-v1.json'
BASE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-optional-pexpect-v1.json'
WHEEL_SHA256 = '4aa48afdce4660b0076a00d80afa54e8a97cd49f457d68a4342d188a09451c1a'
BASE_SHA256 = '82a142fdd6e1ac6c5aee6b59cef0e415fa171f66be3b6f6edc8afaf935f2072d'
SIZE = 15749422
FILENAME = 'numpy-1.21.6-cp38-cp38-manylinux_2_12_x86_64.manylinux2010_x86_64.whl'
URL = 'https://files.pythonhosted.org/packages/d5/43/e88bb1fb7d040ae8e0e06e749341b13f57701aab11fe9d71c99af6202c5c/' + FILENAME


def inspect(raw: bytes):
    """Strict byte, ZIP, RECORD and metadata checks on the single fixed wheel."""
    if type(raw) is not bytes or len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != WHEEL_SHA256:
        raise ValueError('publisher wheel size or digest differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
        infos = wheel.infolist()
        names = [x.filename for x in infos if not x.is_dir()]
        if len(infos) > 10000 or len(set(names)) != len(names) or sum(x.file_size for x in infos) > 100_000_000:
            raise ValueError('excessive or duplicate ZIP members')
        for item in infos:
            name = item.filename.rstrip('/')
            parts = name.split('/')
            mode = item.external_attr >> 16
            if (not name or '\\' in name or PurePosixPath(name).is_absolute()
                    or any(part in ('', '.', '..') for part in parts) or stat.S_ISLNK(mode)
                    or item.file_size > 40_000_000 or '.data/' in item.filename):
                raise ValueError('unsafe or unsupported ZIP member')
        meta = 'numpy-1.21.6.dist-info/METADATA'
        record = 'numpy-1.21.6.dist-info/RECORD'
        headers = BytesParser().parsebytes(wheel.read(meta), headersonly=True)
        if (headers['Name'].lower() != 'numpy' or headers['Version'] != '1.21.6'
                or Version('3.8.20') not in SpecifierSet(headers['Requires-Python'])
                or headers.get_all('Requires-Dist', [])):
            raise ValueError('numpy metadata or dependency closure differs')
        records = list(csv.reader(io.StringIO(wheel.read(record).decode('utf8'))))
        if len(records) != len(names) or {r[0] for r in records if len(r) == 3} != set(names):
            raise ValueError('wheel RECORD coverage differs')
        expected = {}
        for row in records:
            if len(row) != 3:
                raise ValueError('malformed RECORD')
            name, digest, size = row
            if name == record:
                if digest or size:
                    raise ValueError('RECORD self entry differs')
                continue
            content = wheel.read(name)
            want = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip('=')
            if digest != want or size != str(len(content)):
                raise ValueError('RECORD member mismatch')
            expected['/usr/local/lib/python3.8/site-packages/' + name] = hashlib.sha256(content).hexdigest()
        wheel_meta = wheel.read('numpy-1.21.6.dist-info/WHEEL').decode('utf8')
        if 'Tag: cp38-cp38-manylinux_2_12_x86_64' not in wheel_meta or 'Root-Is-Purelib: false' not in wheel_meta:
            raise ValueError('wheel Python/ABI/platform tags differ')
        entries = wheel.read('numpy-1.21.6.dist-info/entry_points.txt').decode('utf8')
        if '[pytest11]' in entries:
            raise ValueError('unexpected pytest plugin')
        return {'publisher_files': expected, 'publisher_file_count': len(expected),
                'native_files': sorted(n for n in expected if n.endswith('.so') or '.so.' in n),
                'entry_points': entries, 'active_requires_dist': [], 'target_imports': False,
                'generated_scripts_content_verified': False, 'ordinary_acceptance': False}


def prepare(output: Path):
    if output.exists():
        raise ValueError('fresh directory required')
    profile_raw, base_raw = PROFILE.read_bytes(), BASE.read_bytes()
    profile = json.loads(profile_raw)
    if (hashlib.sha256(base_raw).hexdigest() != BASE_SHA256 or profile.get('base_optional_profile_sha256') != BASE_SHA256
            or profile.get('task_id') != 'pytest-dev__pytest-6116' or profile.get('python_version') != '3.8.20'
            or profile.get('wheel') != {'name': 'numpy', 'version': '1.21.6', 'filename': FILENAME,
                                        'sha256': WHEEL_SHA256, 'size': SIZE, 'url': URL}):
        raise ValueError('fixed wheel and base profile required')
    request = urllib.request.Request(URL, headers={'User-Agent': 'atomic-flow-p07-numpy-audit/1'})
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read(SIZE + 1)
    result = inspect(raw)
    output.mkdir(parents=True)
    (output / FILENAME).write_bytes(raw)
    receipt = {'status': 'prospective_wheel_verified_no_execution', 'profile_sha256': hashlib.sha256(profile_raw).hexdigest(),
               'base_optional_profile_sha256': BASE_SHA256, 'wheel_sha256': WHEEL_SHA256,
               **result, 'provider_calls': 0,
               'limitations': ['No Docker install, installed file extraction, shared-library or NumPy ABI execution.',
                               'Generated console scripts and pip RECORD remain unverified.',
                               'No claims about optional-profile transitive closure, runtime acceptance or test skips.']}
    (output / 'preparation.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='utf8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    print(json.dumps({'status': prepare(parser.parse_args().output)['status']}))
