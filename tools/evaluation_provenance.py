"""Secret-free source/runtime metadata for future local development pilots."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path


def source_state(root):
    root = Path(root).resolve()
    paths = set((root / 'src').rglob('*.py'))
    paths.update((root / 'tools').glob('evaluation*.py'))
    paths.add(root / 'pyproject.toml')
    files = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError('pilot source manifest requires regular source files')
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    # Only local read-only Git operations; never copy environment or Git config.
    commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    changed = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no',
                              '--', 'src', 'tools', 'pyproject.toml'],
                             capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()
    return {'git_head': commit, 'tracked_runtime_dirty': bool(changed),
            'source_manifest_sha256': identity, 'source_files_sha256': files}


def runtime_state():
    packages = sorted({(item.metadata['Name'], item.version)
                       for item in importlib.metadata.distributions() if item.metadata['Name']})
    return {'python_version': sys.version, 'implementation': platform.python_implementation(),
            'platform': platform.platform(), 'packages': [dict(name=name, version=version) for name, version in packages],
            'environment_values_recorded': False}
