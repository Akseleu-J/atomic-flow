"""Package only reviewed source/test paths; never include .git, secrets or virtualenvs."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts" / "colab"
    output.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in ("pyproject.toml", "README.md", ".dockerignore")]
    for directory in ("src", "tests"):
        files.extend(path for path in (root / directory).rglob("*") if path.is_file()
                     and path.suffix in {".py", ".json", ".yaml"} and "__pycache__" not in path.parts)
    files.extend(root.glob("test*v3*.py"))
    files.extend(root / "tools" / name for name in ("colab_validate.py", "Dockerfile.checks"))
    files = sorted(set(files))
    manifest = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "includes_uncommitted_changes": True,
                "sha256": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    bundle = output / "atomic-flow-working-tree.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr("bundle-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (output / "atomic-flow-working-tree.zip.sha256").write_text(checksum + "\n")
    cells = []

    def cell(kind, text):
        if kind == "code":
            ast.parse(text)
        cells.append({"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True),
                      **({"execution_count": None, "outputs": []} if kind == "code" else {})})

    cell("markdown", "# atomic-flow: CPU checks and container capability probe\n"
         "Upload the accompanying working-tree ZIP. No GPU, Drive mount, API tokens or paid models. "
         "Docker is installed from Ubuntu packages; if the standard daemon is unsupported, isolation is UNAVAILABLE.")
    cell("code", '''from google.colab import files
uploaded = files.upload()  # select atomic-flow-working-tree.zip
''')
    cell("code", f'''import hashlib, io, json, pathlib, zipfile
payload = uploaded['atomic-flow-working-tree.zip']
assert hashlib.sha256(payload).hexdigest() == {checksum!r}, 'Wrong bundle version'
root = pathlib.Path('/content/atomic-flow-validation')
root.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    for name in z.namelist():
        assert (root / name).resolve().is_relative_to(root.resolve()), name
    z.extractall(root)
manifest = json.loads((root / 'bundle-manifest.json').read_text())
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
print('Source hashes verified:', len(manifest['sha256']))
''')
    cell("code", '''import subprocess, sys
subprocess.run([sys.executable, '-m', 'pip', 'install', '.[dev]'], cwd=root, check=True)
''')
    cell("code", '''# This installs Docker if absent and probes the standard daemon; allow several minutes.
result = subprocess.run([sys.executable, 'tools/colab_validate.py', '--install-docker'], cwd=root)
print((root / 'colab-results/results.json').read_text())
assert result.returncode == 0, 'Check validation logs'
''')
    cell("code", '''files.download(str(root / 'colab-results.zip'))
''')
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "colab": {"name": "atomic-flow-validation.ipynb"}}, "cells": cells}
    (output / "atomic-flow-validation.ipynb").write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")
    # Self-contained copy avoids a second browser upload inside the runtime.
    notebook["cells"][1]["source"] = ["import base64\nfrom google.colab import files\n", "uploaded = {'atomic-flow-working-tree.zip': base64.b64decode("
        + repr(base64.b64encode(bundle.read_bytes()).decode("ascii")) + ")}\n"]
    notebook["metadata"]["colab"]["name"] = "atomic-flow-colab-oneclick.ipynb"
    (output / "atomic-flow-colab-oneclick.ipynb").write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bundle: {len(files)} files; SHA256={checksum}")


if __name__ == "__main__":
    main()
