"""Fixed Python2.7 private ABI and wheel primitives; no target execution on host."""
from __future__ import annotations

import email
import io
import json
import zipfile
from pathlib import Path, PurePosixPath

import p07_requests3362_legacy_image as common
from p07_python27_runtime_probe import validate_observed
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
PROFILE_V2_NAME = "benchmarks/stage4/p07-requests863-python27-profile.json"
PROFILE_V2 = ROOT / PROFILE_V2_NAME
PREFIX = "/opt/p07-python27"
LAUNCHER = "/opt/p07-runtime/python27"
DONOR = "library/python@sha256:9d8f2f351785f0024cb4508931a33e08e4885ee88050ebfff683247846e0d8c8"
BASE = "swebench/sweb.eval.x86_64.psf_1776_requests-863@sha256:4dcddfdcbc2bcba96327de327f411c671e695366f4e7d09781f64c1758053b18"
LIMIT = 4 * 1024**2
PLATFORMS = ["manylinux1_x86_64", "linux_x86_64"]
TAGS = set(cpython_tags((2, 7), ["cp27mu"], PLATFORMS)) | set(compatible_tags((2, 7), "cp27", PLATFORMS))
MARKERS = {**common.MARKERS, "python_version": "2.7", "python_full_version": "2.7.13", "implementation_version": "2.7.13"}
sha = common.sha
save = common.save
requirements = common.requirements
command_failure = common.command_failure
STDIN_BOOTSTRAP = "import sys; exec(compile(sys.stdin.read(), '<p07-fixed-host-bootstrap>', 'exec'))"
PRIVATE_PATHS = [PREFIX + "/lib/python27.zip", PREFIX + "/lib/python2.7", PREFIX + "/lib/python2.7/lib-dynload", PREFIX + "/lib/python2.7/site-packages"]
_BOOT = "import sys\nsys.path[:]=PATHS\nimport runpy\nargs=sys.argv[1:]\nif len(args)<2:\n    raise RuntimeError('fixed host launcher mode required')\nif args[0]=='-c':\n    sys.argv=['-c']\n    exec(compile(args[1],'<p07-host>','exec'))\nelif args[0]=='-m' and args[1]=='pip':\n    sys.argv=args[1:]\n    runpy.run_module('pip',run_name='__main__')\nelse:\n    raise RuntimeError('unreviewed host launcher mode')\n"
_BOOT = _BOOT.replace("PATHS", repr(PRIVATE_PATHS))
LAUNCHER_SOURCE = "#!/bin/sh\nexec /opt/p07-donor/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 --library-path /opt/p07-python27/lib:/opt/p07-donor/lib/x86_64-linux-gnu:/opt/p07-donor/usr/lib/x86_64-linux-gnu /opt/p07-python27/bin/python2.7 -E -s -S -B -c '" + _BOOT.replace("'", "'\"'\"'") + "' \"$@\"\n"
PROBE = """import collections,ctypes,ssl,sqlite3,bz2,zlib,json,sys,sysconfig
class SyntheticConnectionError(IOError):pass
e=SyntheticConnectionError('p07-message-contract')
probes={name:{'status':'imported','path':module.__file__} for name,module in [('ssl',ssl),('ctypes',ctypes),('sqlite3',sqlite3),('bz2',bz2),('zlib',zlib)]}
probes['exception_message']={'status':'resolved','matches':e.message=='p07-message-contract','value':e.message}
probes['ssl']['sslv3_context_constructed']=ssl.SSLContext(ssl.PROTOCOL_SSLv3).protocol==ssl.PROTOCOL_SSLv3
probes['collections_mapping']={'status':'resolved','module':collections.Mapping.__module__}
print(json.dumps({'version_info':list(sys.version_info[:3]),'executable':sys.executable,'prefix':sys.prefix,'unicode_size':sysconfig.get_config_var('Py_UNICODE_SIZE'),'probes':probes,'mapped_libraries':sorted(set(line.split()[-1] for line in open('/proc/self/maps') if '/opt/p07-' in line))},sort_keys=True))
"""


def validate_probe(observed):
    copy = {**observed, "prefix": "/usr/local", "executable": "/usr/local/bin/python2.7"}
    return (observed.get("prefix") == PREFIX and observed.get("executable") == PREFIX + "/bin/python2.7"
            and validate_observed(copy, profile="python2713-wheezy-sslv3") and bool(observed.get("mapped_libraries"))
            and all(observed["probes"][name].get("path", "").startswith(PREFIX + "/")
                    for name in ("ssl", "ctypes", "sqlite3", "bz2", "zlib")))


def dockerfile():
    return (f"FROM {DONOR} AS donor\nFROM {BASE}\nUSER root\n"
            "COPY --from=donor /usr/local/ /opt/p07-python27/\n"
            "COPY --from=donor /lib/x86_64-linux-gnu/ /opt/p07-donor/lib/x86_64-linux-gnu/\n"
            "COPY --from=donor /usr/lib/x86_64-linux-gnu/ /opt/p07-donor/usr/lib/x86_64-linux-gnu/\n"
            "COPY python27 /opt/p07-runtime/python27\nRUN --network=none chmod 755 /opt/p07-runtime/python27\n")


def frozen_profile(path):
    raw = Path(path).read_bytes().replace(b"\r\n", b"\n")
    if Path(path).resolve() != PROFILE_V2.resolve():
        raise ValueError("fixed Python2 profile only")
    config = json.loads(raw)
    if config['requested_runtime']['version'] != '2.7.13' or config['requested_runtime']['donor_image'] != DONOR:
        raise ValueError('fixed donor profile differs')
    return raw, config


def validate_installed_inventory(profile, files):
    expected = profile['installed_inventory']
    actual = sha(json.dumps(files,sort_keys=True,separators=(',',':')).encode())
    if len(files) != expected['count'] or actual != expected['sha256']:
        raise ValueError('complete Python2 wheel byte inventory mismatch')


def inventory_source(files):
    code = """import hashlib,json,os,sys
expected=json.loads(EXPECTED)
root='/opt/p07-python27/lib/python2.7/site-packages/'
observed={}
for relative,digest in sorted(expected.items()):
    path=root+relative
    if os.path.islink(path) or not os.path.realpath(path).startswith(root):raise RuntimeError('inventory path differs')
    actual=hashlib.sha256(open(path,'rb').read()).hexdigest()
    if actual!=digest:raise RuntimeError('inventory bytes differ')
    observed[relative]=actual
print(json.dumps({'files':observed,'prefix':sys.prefix,'version_info':list(sys.version_info[:3]),'target_or_plugins_imported':False},sort_keys=True))
"""
    return code.replace("EXPECTED", repr(json.dumps(files,sort_keys=True)))


def run_inventory(name, source, runner):
    return runner(['docker','exec','-i',name,LAUNCHER,'-c',STDIN_BOOTSTRAP],60,input_bytes=source.encode())

def verified_wheel(record, raw):
    if sha(raw) != record["sha256"] or len(raw) != record["bytes"] or len(raw) > LIMIT:
        raise ValueError("wheel bytes differ from reviewed lock")
    name, version, _, tags = parse_wheel_filename(record["filename"])
    if (canonicalize_name(record["name"]) != name or str(version) != record["version"] or not tags & TAGS):
        raise ValueError("wheel name, version or CPython2.7/Linux ABI mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(entries) > 4000 or len(names) != len(set(names)) or sum(item.file_size for item in entries) > 32 * 1024**2:
            raise ValueError("wheel archive bounds or duplicate member mismatch")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe wheel path")
        metadata = [item for item in entries if item.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 1024**2:
            raise ValueError("ambiguous wheel metadata")
        metadata_raw = archive.read(metadata[0])
        message = email.message_from_bytes(metadata_raw)
        requirements = message.get_all("Requires-Dist", [])
        if (canonicalize_name(message.get("Name", "")) != name or message.get("Version") != record["version"]
                or requirements != record["requires_dist"] or message.get("Requires-Python") != record["requires_python"]):
            raise ValueError("actual wheel METADATA differs from reviewed profile")
        if message.get("Requires-Python") and not SpecifierSet(message["Requires-Python"]).contains("2.7.13"):
            raise ValueError("dependency refuses fixed interpreter")
        inventory = {}
        for item in entries:
            if item.is_dir() or ".data/" in item.filename:
                continue
            if ".dist-info/" in item.filename and item.filename != metadata[0].filename:
                continue
            inventory[item.filename] = sha(archive.read(item))
        return {"name": record["name"], "version": record["version"], "filename": record["filename"],
                "sha256": record["sha256"], "metadata_sha256": sha(metadata_raw),
                "requires_dist": requirements, "installed_files": inventory}


def closure(profile, verified):
    pins = {canonicalize_name(name): value for name, value in profile["pins"].items()}
    if (len(pins) != 18 or len(verified) != len(pins) or len(profile["wheels"]) != len(pins)
            or {canonicalize_name(item["name"]): item["version"] for item in verified} != pins):
        raise ValueError("wheel closure identity/cardinality mismatch")
    edges = []
    for item in verified:
        for declared in item["requires_dist"]:
            requirement = Requirement(declared)
            if requirement.marker is not None and not requirement.marker.evaluate(MARKERS):
                continue
            key = canonicalize_name(requirement.name)
            if requirement.url or requirement.extras or key not in pins or not requirement.specifier.contains(pins[key]):
                raise ValueError("active dependency outside fixed closed wheel set")
            edges.append((canonicalize_name(item["name"]), key, str(requirement.specifier)))
    expected = [(canonicalize_name(edge["from"]), canonicalize_name(Requirement(edge["requires"]).name),
                 str(Requirement(edge["requires"]).specifier)) for edge in profile["closure"]["active_edges"]]
    if sorted(edges) != sorted(expected):
        raise ValueError("active dependency closure differs from reviewed profile")
    return sorted(edges)
