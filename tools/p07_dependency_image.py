"""Prepare/build reviewed historical Sphinx/Pylint overlays, never grade tests.

Only reviewed universal wheels for exact task-specific pins enter an offline build.
Build/probe requires an approved Linux worker; no target setup/import/test runs.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import io
import json
import platform
import re
import subprocess
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

PINS = {"docutils": "0.16", "pygments": "2.7.4"}
TASKS = {"sphinx-doc__sphinx-" + number for number in ("8721", "8474", "8506", "8713")}
PYLINT_PINS = {"pylint-dev__pylint-7080": {"astroid": "2.11.6", "tomlkit": "0.10.1"},
               "pylint-dev__pylint-7114": {"astroid": "2.12.2"}}
ASTROID_REQUIRES = {
    "2.11.6": ["lazy-object-proxy (>=1.4.0)", "wrapt (<2,>=1.11)", "setuptools (>=20.0)",
               'typed-ast (<2.0,>=1.4.0) ; implementation_name == "cpython" and python_version < "3.8"',
               'typing-extensions (>=3.10) ; python_version < "3.10"'],
    "2.12.2": ["lazy-object-proxy (>=1.4.0)", "wrapt (<2,>=1.11)",
               'typed-ast (<2.0,>=1.4.0) ; implementation_name == "cpython" and python_version < "3.8"',
               'typing-extensions (>=3.10) ; python_version < "3.10"'],
}
PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
LIMIT = 12 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def task_pins(task):
    if task in TASKS:
        return dict(PINS)
    if task in PYLINT_PINS:
        return dict(PYLINT_PINS[task])
    raise ValueError("task has no reviewed dependency overlay")


def image_prefix(task):
    task_pins(task)
    repository = "sphinx-doc_1776_sphinx-" if task in TASKS else "pylint-dev_1776_pylint-"
    return "swebench/sweb.eval.x86_64." + repository + task.rsplit("-", 1)[1]


def profile_filename(task):
    task_pins(task)
    return "p07-sphinx-environment-profile.json" if task in TASKS else "p07-pylint-environment-profile.json"


def selected_wheels(config, task):
    pins = task_pins(task)
    matches = [item for item in config["tasks"] if item["instance_id"] == task]
    if len(matches) != 1:
        raise ValueError("ambiguous profile task")
    declared = matches[0].get("pins", config.get("pins", {}))
    if {name.lower(): value for name, value in declared.items()} != pins:
        raise ValueError("profile base or dependency pins not admitted")
    records = [item for item in config["wheels"] if pins.get(item["name"].lower()) == item["version"]]
    if len(records) != len(pins) or {item["name"].lower() for item in records} != set(pins):
        raise ValueError("exact task-specific wheel overlay required")
    return matches[0], pins, records


def declared_requirements(record):
    declared = [value for value in record.get("requires_dist", []) or [] if value is not None]
    expected = ASTROID_REQUIRES.get(record.get("version"), []) if record.get("name", "").lower() == "astroid" else []
    if sorted(declared) != sorted(expected):
        raise ValueError("wheel requires undeclared runtime dependencies")
    return declared


def serialized_wheel(record):
    result = {key: record[key] for key in ("name", "version", "filename", "url", "sha256")}
    requires = declared_requirements(record)
    if requires:
        result["requires_dist"] = requires
    return result


def requirements_probe_source(wheels, *, strict):
    planned = {item["name"].lower(): declared_requirements(item) for item in wheels if declared_requirements(item)}
    return """import importlib.metadata as m,json
from packaging.markers import default_environment
from packaging.requirements import Requirement
planned=json.loads(PLANNED)
environment=default_environment()
environment['extra']=''
proof={'environment':environment,'packages':{}}
for name,expected in planned.items():
    actual=m.requires(name) or []
    if STRICT and sorted(actual)!=sorted(expected):
        raise ValueError('Installed wheel requirements differ from reviewed metadata')
    checks=[]
    for text in sorted(set(actual+expected)):
        requirement=Requirement(text)
        active=requirement.marker is None or requirement.marker.evaluate(environment)
        version=m.version(requirement.name) if active else None
        if active and (requirement.url or requirement.extras or not requirement.specifier.contains(version,prereleases=True)):
            raise ValueError('Unsatisfied runtime requirement: '+text)
        checks.append({'requirement':text,'active':active,'version':version})
    proof['packages'][name]={'installed_requires_dist':actual,'planned_requires_dist':expected,'checks':checks}
print(json.dumps(proof))
""".replace("PLANNED", repr(json.dumps(planned))).replace("STRICT", repr(strict))


def validate_requirements_proof(proof, wheels, *, strict):
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    planned = {item["name"].lower(): declared_requirements(item) for item in wheels if declared_requirements(item)}
    environment = proof.get("environment", {})
    if (set(environment) != set(default_environment()) | {"extra"}
            or not all(isinstance(value, str) for value in environment.values())
            or environment["extra"] != "" or environment["python_version"] != "3.9"
            or not environment["python_full_version"].startswith("3.9.")
            or environment["implementation_name"] != "cpython" or environment["sys_platform"] != "linux"
            or set(proof.get("packages", {})) != set(planned)):
        raise ValueError("runtime requirement proof has an unexpected target environment")
    for name, expected in planned.items():
        observed = proof["packages"][name]
        actual = observed.get("installed_requires_dist", [])
        checks = observed.get("checks", [])
        if (observed.get("planned_requires_dist") != expected or not isinstance(actual, list)
                or strict and sorted(actual) != sorted(expected)
                or len(checks) != len(set(actual + expected))
                or {item.get("requirement") for item in checks} != set(actual + expected)):
            raise ValueError("runtime requirement proof differs from reviewed metadata")
        for item in checks:
            requirement = Requirement(item["requirement"])
            active = requirement.marker is None or requirement.marker.evaluate(environment)
            if (item.get("active") is not active or not active and item.get("version") is not None
                    or active and (requirement.url or requirement.extras or not isinstance(item.get("version"), str)
                                   or not requirement.specifier.contains(item["version"], prereleases=True))):
                raise ValueError("runtime dependency requirement is unsatisfied or unproven")


def checked_url(url, host):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != host or parsed.port not in {None, 443}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("unreviewed dependency source URL")


def fetch_bytes(url):
    host = urllib.parse.urlsplit(url).hostname
    if host not in {"pypi.org", "files.pythonhosted.org"}:
        raise ValueError("dependency source host not admitted")
    checked_url(url, host)
    with urllib.request.urlopen(url, timeout=60) as response:
        checked_url(response.geturl(), host)
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("dependency download exceeds bound")
    return raw


def validate_wheel(record, raw):
    name = record.get("name", "").lower()
    version = record.get("version")
    filename = record.get("filename", "")
    allowed = {**{key: {value} for key, value in PINS.items()}, "astroid": set(ASTROID_REQUIRES), "tomlkit": {"0.10.1"}}
    if (version not in allowed.get(name, set()) or not re.fullmatch(
            r"[A-Za-z0-9_.]+-[0-9.]+-(?:py2\.)?py3-none-any\.whl", filename)
            or Path(filename).name != filename or len(raw) > LIMIT
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", ""))
            or sha(raw) != record["sha256"]):
        raise ValueError("unreviewed wheel identity or byte digest")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > 40 * 1024 * 1024:
            raise ValueError("wheel archive exceeds bound")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe wheel member")
        metadata = [item for item in entries if item.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 1024 * 1024:
            raise ValueError("ambiguous wheel metadata")
        message = email.message_from_bytes(archive.read(metadata[0]))
        if (message.get("Name", "").lower() != name or message.get("Version") != version
                or sorted(message.get_all("Requires-Dist", [])) != sorted(declared_requirements(record))):
            raise ValueError("wheel metadata differs or requires unreviewed dependencies")
    return name


def dockerfile_for(image):
    return (f"FROM {image}\nUSER root\nCOPY wheels/ /opt/p07-dependency-overlay/wheels/\n"
            "COPY requirements.lock /opt/p07-dependency-overlay/requirements.lock\n"
            f"RUN --network=none {PYTHON} -I -m pip install --no-index --no-deps --no-compile "
            "--require-hashes --find-links=/opt/p07-dependency-overlay/wheels "
            "-r /opt/p07-dependency-overlay/requirements.lock\n")


def requirements_lock(wheels):
    return "".join(f"{item['name'].lower()}=={item['version']} --hash=sha256:{item['sha256']}\n"
                   for item in sorted(wheels, key=lambda item: item["name"].lower()))


def prepare(profile, task, output, *, fetch=fetch_bytes):
    raw_profile = profile.read_bytes().replace(b"\r\n", b"\n")
    config = json.loads(raw_profile)
    if task not in TASKS | set(PYLINT_PINS) or output.exists():
        raise ValueError("reviewed exact task and fresh output required")
    selected, pins, records = selected_wheels(config, task)
    image = selected["base_image_digest"]
    expected = image_prefix(task)
    if (not re.fullmatch(re.escape(expected) + r"@sha256:[0-9a-f]{64}", image)
            or not re.fullmatch(r"[0-9a-f]{40}", selected["base_commit"])):
        raise ValueError("profile base or dependency pins not admitted")
    wheels, payloads, seen = [], {}, set()
    for record in records:
        name = record["name"].lower()
        checked_url(record["url"], "files.pythonhosted.org")
        checked_url(record["primary_metadata_url"], "pypi.org")
        if (record["primary_metadata_url"] != "https://pypi.org/pypi/" + record["name"] + "/" + record["version"] + "/json"
                or urllib.parse.unquote(urllib.parse.urlsplit(record["url"]).path.rsplit("/", 1)[-1]) != record["filename"]):
            raise ValueError("primary metadata URL or filename mismatch")
        published = json.loads(fetch(record["primary_metadata_url"]))
        matching = [item for item in published["urls"] if item["filename"] == record["filename"]]
        if (len(matching) != 1 or matching[0]["url"] != record["url"]
                or matching[0]["digests"]["sha256"] != record["sha256"]):
            raise ValueError("primary PyPI wheel identity mismatch")
        wheel = fetch(record["url"])
        if validate_wheel(record, wheel) != name or name in seen:
            raise ValueError("duplicate wheel dependency")
        seen.add(name)
        wheels.append(serialized_wheel(record))
        payloads[record["filename"]] = wheel
    if seen != set(pins):
        raise ValueError("exact reviewed task-specific wheel overlay required")
    output.mkdir(parents=True)
    (output / "wheels").mkdir()
    for name, wheel in payloads.items():
        (output / "wheels" / name).write_bytes(wheel)
    requirements = requirements_lock(wheels)
    dockerfile = dockerfile_for(image)
    (output / "requirements.lock").write_bytes(requirements.encode())
    (output / "Dockerfile").write_bytes(dockerfile.encode())
    receipt = {"status": "prepared_not_built", "task": task, "base_image": image,
               "base_commit": selected["base_commit"], "profile_sha256": sha(raw_profile), "wheels": wheels,
               "requirements_sha256": sha(requirements.encode()), "dockerfile_sha256": sha(dockerfile.encode()),
               "model_calls": 0, "target_setup_or_tests_executed": False, "calibrated": False}
    save(output / "preparation.json", receipt)
    return receipt


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    prepared = json.loads((context / "preparation.json").read_text())
    pins = task_pins(prepared["task"])
    expected = image_prefix(prepared["task"])
    if (not re.fullmatch(re.escape(expected) + r"@sha256:[0-9a-f]{64}", prepared["base_image"])
            or not re.fullmatch(r"[0-9a-f]{64}", prepared["profile_sha256"])
            or not re.fullmatch(r"[0-9a-f]{40}", prepared["base_commit"])
            or (context / "Dockerfile").read_bytes() != dockerfile_for(prepared["base_image"]).encode()
            or (context / "requirements.lock").read_bytes() != requirements_lock(prepared["wheels"]).encode()
            or sha((context / "Dockerfile").read_bytes()) != prepared["dockerfile_sha256"]
            or sha((context / "requirements.lock").read_bytes()) != prepared["requirements_sha256"]):
        raise ValueError("prepared context differs")
    if {item["name"].lower(): item["version"] for item in prepared["wheels"]} != pins or len(prepared["wheels"]) != len(pins):
        raise ValueError("exact task-specific wheel overlay required")
    for item in prepared["wheels"]:
        if Path(item["filename"]).name != item["filename"] or "\\" in item["filename"]:
            raise ValueError("unsafe wheel filename")
        validate_wheel(item, (context / "wheels" / item["filename"]).read_bytes())
    output.mkdir(parents=True)
    report = {**prepared, "status": "build_or_probe_failed", "provider_credentials": "none"}
    active = []

    def command(args, *, timeout=60, log=None):
        if log:
            with (output / log).open("wb") as stream:
                process = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            if process.returncode:
                raise RuntimeError("trusted dependency build failed; see " + log)
            return ""
        process = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        if process.returncode:
            raise RuntimeError("dependency probe command failed: " + args[0])
        return process.stdout.decode().strip()

    def probe(image, *, strict):
        name = "p07-dependencies-" + uuid.uuid4().hex[:12]
        active.append(name)  # Includes uncertain create outcomes for unconditional cleanup.
        command(["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "--memory", "6g", "--cpus", "2", "--pids-limit", "256",
                 "--entrypoint", "/bin/bash", image, "-c", "sleep infinity"])
        host = json.loads(command(["docker", "inspect", name]))[0]
        options = host["HostConfig"]
        if (options["NetworkMode"] != "none" or host["Mounts"] or options.get("Binds") or options["Privileged"]
                or options["CapDrop"] != ["ALL"] or "no-new-privileges" not in options["SecurityOpt"]
                or options["Memory"] != 6 * 1024**3 or options["NanoCpus"] != 2_000_000_000 or options["PidsLimit"] != 256):
            raise ValueError("dependency probe isolation mismatch")
        command(["docker", "start", name])
        head = command(["docker", "exec", name, "git", "-C", "/testbed", "rev-parse", "HEAD"])
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ValueError("invalid image source HEAD")
        command(["docker", "exec", name, "git", "-C", "/testbed", "cat-file", "-e", prepared["base_commit"] + "^{commit}"])
        result = {"head": head,
                "diff": command(["docker", "exec", name, "git", "-C", "/testbed", "diff", "--binary", "HEAD"]),
                "status": command(["docker", "exec", name, "git", "-C", "/testbed", "status", "--porcelain", "--untracked-files=all"]),
                "pip_freeze": command(["docker", "exec", name, PYTHON, "-I", "-m", "pip", "freeze"]),
                "overlay_versions": json.loads(command(["docker", "exec", name, PYTHON, "-I", "-c",
                    "import importlib.metadata,json;print(json.dumps({n:importlib.metadata.version(n) for n in " + repr(tuple(sorted(pins))) + "}))"]))}
        if any(declared_requirements(item) for item in prepared["wheels"]):
            result["runtime_requirements"] = json.loads(command(["docker", "exec", name, PYTHON, "-I", "-c",
                requirements_probe_source(prepared["wheels"], strict=strict)]))
            validate_requirements_proof(result["runtime_requirements"], prepared["wheels"], strict=strict)
        return result

    try:
        command(["docker", "pull", prepared["base_image"]], timeout=600, log="base-pull.log")
        base = json.loads(command(["docker", "image", "inspect", prepared["base_image"]]))[0]
        report["base_image_inspect"] = base
        report["before"] = probe(prepared["base_image"], strict=False)
        tag = "p07-dependencies:" + uuid.uuid4().hex
        command(["docker", "build", "--network", "none", "--tag", tag,
                 "--label", "atomic.p07.dependencies.base=" + prepared["base_image"],
                 "--label", "atomic.p07.dependencies.profile=" + prepared["profile_sha256"], str(context)], timeout=900, log="build.log")
        image = json.loads(command(["docker", "image", "inspect", tag]))[0]
        image_id = image["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("immutable overlay image unavailable")
        report.update(image_id=image_id, image_inspect=image, after=probe(image_id, strict=True))
        if any(report["before"][key] != report["after"][key] for key in ("head", "diff", "status")):
            raise ValueError("dependency overlay altered target source")
        if report["after"]["overlay_versions"] != pins:
            raise ValueError("overlay package version mismatch")
        before = set(report["before"]["pip_freeze"].splitlines())
        after = set(report["after"]["pip_freeze"].splitlines())
        allowed = re.compile(r"^(" + "|".join(map(re.escape, pins)) + r")==", re.IGNORECASE)
        if any(not allowed.match(line) for line in before ^ after):
            raise ValueError("overlay altered undeclared target dependency")
        report["status"] = "dependency_overlay_built_source_unchanged"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        report["cleanup"] = []
        for name in active:
            try:
                cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
                okay = cleanup.returncode == 0 or b"No such container" in cleanup.stderr
                report["cleanup"].append({"container": name, "exit": cleanup.returncode, "confirmed": okay})
            except (OSError, subprocess.SubprocessError) as exc:
                report["cleanup"].append({"container": name, "confirmed": False, "error_type": type(exc).__name__})
        if not all(item["confirmed"] for item in report["cleanup"]):
            report["status"] = "cleanup_uncertain"
        report["cleanup_exit"] = 0 if all(item["confirmed"] for item in report["cleanup"]) else None
        save(output / "dependency-image.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    make = sub.add_parser("prepare")
    make.add_argument("--profile", type=Path, required=True)
    make.add_argument("--task", required=True)
    make.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("build")
    run.add_argument("--context", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    result = prepare(**args) if action == "prepare" else build(**args)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "dependency_overlay_built_source_unchanged"} else 1)
