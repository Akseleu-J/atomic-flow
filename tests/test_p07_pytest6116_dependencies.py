"""Publisher wheel identity and RECORD are independently enforced."""
import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from p07_pytest6116_derived_collection import validate_installed  # noqa: E402
from p07_pytest6116_prepare_dependencies import inspect_wheel  # noqa: E402


def wheel_bytes(*, change=False, unsafe=False):
    metadata = b"Name: sample\nVersion: 1.0\nRequires-Python: >=3.8\n\n"
    files = {"sample/__init__.py": b"value = 1\n", "sample-1.0.dist-info/METADATA": metadata}
    if unsafe:
        files["../escape.py"] = b"bad"
    rows = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        rows.append((name, "sha256=" + digest, str(len(data))))
    rows.append(("sample-1.0.dist-info/RECORD", "", ""))
    record = io.StringIO(newline="")
    csv.writer(record).writerows(rows)
    if change:
        files["sample/__init__.py"] = b"changed after RECORD"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("sample-1.0.dist-info/RECORD", record.getvalue())
    return output.getvalue()


def item(raw):
    return {"name": "sample", "version": "1.0", "filename": "sample-1.0-py3-none-any.whl",
            "sha256": hashlib.sha256(raw).hexdigest()}


def test_wheel_requires_matching_publisher_digest_and_complete_record():
    raw = wheel_bytes()
    assert inspect_wheel(raw, item(raw))["files"] == 3
    with pytest.raises(ValueError, match="publisher digest"):
        inspect_wheel(raw, {**item(raw), "sha256": "0" * 64})
    corrupted = wheel_bytes(change=True)
    with pytest.raises(ValueError, match="RECORD"):
        inspect_wheel(corrupted, item(corrupted))
    escaped = wheel_bytes(unsafe=True)
    with pytest.raises(ValueError, match="path or size"):
        inspect_wheel(escaped, item(escaped))


def test_installed_inventory_must_preserve_wheel_files_and_only_fixed_metadata():
    expected = {"sample/__init__.py": "a" * 64, "sample-1.0.dist-info/METADATA": "b" * 64}
    valid = {**expected, "sample-1.0.dist-info/INSTALLER": "c" * 64,
             "sample-1.0.dist-info/RECORD": "d" * 64}
    assert validate_installed(expected, valid) == ["sample-1.0.dist-info/INSTALLER", "sample-1.0.dist-info/RECORD"]
    with pytest.raises(ValueError, match="publisher wheel bytes"):
        validate_installed(expected, {**valid, "sample/__init__.py": "e" * 64})
    with pytest.raises(ValueError, match="unexpected installed"):
        validate_installed(expected, {**valid, "sample/extra.py": "f" * 64})
