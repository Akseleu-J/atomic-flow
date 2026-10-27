"""Own-base private runtime and DNS test certificates for the fixed Requests863 task.

No target source, setup, plugin or test is imported or executed during provisioning.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

import p07_requests863_python27_support as legacy
import p07_requests863_python27_support as transplant
from p07_sympy13971_image import fetch

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "benchmarks/stage4/p07-requests863-python27-service-profile-v4.json"
PROFILE_SHA = "f1e8a383ad133e5f4ee753ac45c530f197ccc26b8e227ddb5b76ae8577f6657e"
TASK_IDS = ("psf__requests-863",)
CERT_ROOT = "/opt/p07-function-fixture"
CERT_NAMES = ("cacert.pem", "cert.pem", "key.pem")
RECEIPT = "requests863-python27-runtime.json"
DONOR = transplant.DONOR
STDIN_BOOTSTRAP = legacy.STDIN_BOOTSTRAP
sha = legacy.sha
save = legacy.save
run_inventory = legacy.run_inventory
inventory_source = legacy.inventory_source
command_failure = legacy.command_failure


def utcnow():
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def create_certificates(now=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = now or utcnow()

    def ca(common_name):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .sign(key, hashes.SHA256()))
        return key, cert

    ca_key, ca_cert = ca("atomic-flow P07 public test CA")
    _, wrong_ca = ca("atomic-flow P07 unrelated public test CA")
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "httpbin.org")]))
        .issuer_name(ca_cert.subject).public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=14))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("httpbin.org"), x509.DNSName("github.com")]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    return {"cacert.pem": ca_cert.public_bytes(pem), "cert.pem": leaf.public_bytes(pem),
            "key.pem": leaf_key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL,
                                               serialization.NoEncryption()),
            "wrongca.pem": wrong_ca.public_bytes(pem)}


def validate_certificates(files, now=None):
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if set(files) != {*CERT_NAMES, "wrongca.pem"}:
        raise ValueError("exact disclosed three-file certificate overlay and unrelated public CA required")
    now = now or utcnow()
    certs = {name: x509.load_pem_x509_certificate(files[name]) for name in ("cacert.pem", "cert.pem", "wrongca.pem")}
    ca, leaf, wrong = (certs[name] for name in ("cacert.pem", "cert.pem", "wrongca.pem"))
    key = serialization.load_pem_private_key(files["key.pem"], password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048 or key.public_key().public_numbers().e != 65537:
        raise ValueError("fixed RSA test-fixture key policy mismatch")
    encoding, form = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(encoding, form) != leaf.public_key().public_bytes(encoding, form):
        raise ValueError("server certificate and public test-fixture key differ")
    metadata = {}
    for name, cert in certs.items():
        before, after = cert.not_valid_before_utc, cert.not_valid_after_utc
        maximum = 15 if name == "cert.pem" else 31
        if not before <= now < after or after - before > dt.timedelta(days=maximum):
            raise ValueError("certificate validity outside current bounded test profile")
        if cert.signature_hash_algorithm.name != "sha256" or not isinstance(cert.public_key(), rsa.RSAPublicKey):
            raise ValueError("certificate signature/key policy mismatch")
        if cert.public_key().key_size != 2048:
            raise ValueError("certificate RSA size differs")
        metadata[name] = {"sha256": sha(files[name]), "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(), "serial": str(cert.serial_number),
            "not_before": before.isoformat(), "not_after": after.isoformat(),
            "extensions": sorted(extension.oid.dotted_string for extension in cert.extensions),
            "public_key_sha256": sha(cert.public_key().public_bytes(encoding, form))}
    for cert in (ca, wrong):
        constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        expected_usage = x509.KeyUsage(False, False, False, False, False, True, True, False, False)
        if (cert.subject != cert.issuer or not constraints.ca or constraints.path_length != 0 or usage != expected_usage
                or not cert.extensions.get_extension_for_class(x509.BasicConstraints).critical
                or not cert.extensions.get_extension_for_class(x509.KeyUsage).critical
                or {ext.oid for ext in cert.extensions} != {x509.ExtensionOID.BASIC_CONSTRAINTS, x509.ExtensionOID.KEY_USAGE}):
            raise ValueError("test CA constraints mismatch")
        try:
            cert.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
        except InvalidSignature as exc:
            raise ValueError("test CA signature invalid") from exc
    if leaf.issuer != ca.subject or ca.public_key().public_numbers() == wrong.public_key().public_numbers():
        raise ValueError("unrelated CA or leaf issuer mismatch")
    try:
        ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), leaf.signature_hash_algorithm)
    except InvalidSignature as exc:
        raise ValueError("server certificate signature invalid") from exc
    if (leaf.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "httpbin.org")])
            or leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or leaf.extensions.get_extension_for_class(x509.KeyUsage).value != x509.KeyUsage(
                True, False, True, False, False, False, False, False, False)
            or not leaf.extensions.get_extension_for_class(x509.BasicConstraints).critical
            or not leaf.extensions.get_extension_for_class(x509.KeyUsage).critical
            or leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).critical
            or {ext.oid for ext in leaf.extensions} != {x509.ExtensionOID.BASIC_CONSTRAINTS,
                x509.ExtensionOID.KEY_USAGE, x509.ExtensionOID.EXTENDED_KEY_USAGE, x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME}
            or list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.SERVER_AUTH]
            or list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != [x509.DNSName("httpbin.org"), x509.DNSName("github.com")]
            or leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).critical):
        raise ValueError("DNS-only server certificate profile mismatch")
    metadata["key.pem"] = {"sha256": sha(files["key.pem"]), "purpose": "public benchmark fixture only",
                           "public_key_sha256": sha(key.public_key().public_bytes(encoding, form))}
    return metadata


def frozen_task(task):
    if task not in TASK_IDS:
        raise ValueError("only four reviewed function-service tasks admitted")
    raw = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    if sha(raw) != PROFILE_SHA:
        raise ValueError("reviewed function service profile bytes differ")
    profile = json.loads(raw)
    target = next(item for item in profile["tasks"] if item["instance_id"] == task)
    wire = target["service"]["historical_wire_contracts"]
    if (wire["source_path"] != "tools/p07_requests863_wire_contracts.py"
            or wire["source_sha256"] != sha((ROOT / wire["source_path"]).read_bytes().replace(b"\r\n", b"\n"))):
        raise ValueError("historical wire fixture differs from frozen profile")
    dep_raw, dependency = legacy.frozen_profile(legacy.PROFILE_V2)
    binding = profile["source_bindings"]["legacy_wheel_profile"]
    if (binding["path"] != legacy.PROFILE_V2_NAME or binding["git_lf_sha256"] != sha(dep_raw)
            or profile["runtime"]["donor_image"] != DONOR or profile["runtime"]["wheel_count"] != 18
            or target["service"]["application"] != "httpbin==0.5.0 publisher wheel in fixed18-wheel lock"):
        raise ValueError("fixed dependency/donor/service binding differs")
    return raw, target, dep_raw, dependency


def dockerfile(target):
    return (transplant.dockerfile().replace(transplant.BASE, target["official_base_image"])
            + "COPY wheels/ /opt/p07-requests-wheels/\n"
            + "COPY requirements.lock /opt/p07-requests-wheels/requirements.lock\n"
            + "COPY certificates/ /opt/p07-function-fixture/\n"
            + "RUN --network=none chmod 755 /opt/p07-function-fixture && chmod 644 /opt/p07-function-fixture/*.pem\n"
            + "RUN --network=none " + transplant.LAUNCHER
            + " -m pip install --no-index --no-deps --no-compile --ignore-installed --require-hashes"
            + " --find-links=/opt/p07-requests-wheels -r /opt/p07-requests-wheels/requirements.lock\n")


def certificate_source(files):
    if set(files) != {CERT_ROOT + "/" + name for name in CERT_NAMES}:
        raise ValueError("exact three fixed certificate paths required")
    return '''import hashlib,json,os
expected=json.loads(EXPECTED)
for filename,digest in expected.items():
    if os.path.islink(filename) or os.path.realpath(filename)!=filename or hashlib.sha256(open(filename,'rb').read()).hexdigest()!=digest:
        raise RuntimeError('fixed DNS certificate bytes differ')
print(json.dumps({'certificate_files':expected,'target_imported':False},sort_keys=True))
'''.replace("EXPECTED",repr(json.dumps(files,sort_keys=True)))


def prepare(task, output, *, downloader=fetch):
    if output.exists():
        raise ValueError("fresh function runtime context required")
    profile_raw, target, dep_raw, config = frozen_task(task)
    wheels, downloads, files = [], [], {}
    for record in config["wheels"]:
        publication = json.loads(downloader(record["primary_metadata_url"]))
        selected = [item for item in publication["urls"] if item["filename"] == record["filename"]]
        if (len(selected) != 1 or selected[0]["url"] != record["url"]
                or selected[0]["digests"]["sha256"] != record["sha256"]):
            raise ValueError("publisher wheel identity differs")
        raw = downloader(record["url"])
        wheel = legacy.verified_wheel(record, raw)
        if files.keys() & wheel["installed_files"].keys():
            raise ValueError("overlapping publisher wheel paths")
        files.update(wheel["installed_files"])
        wheels.append(wheel)
        downloads.append((record["filename"], raw))
    edges = legacy.closure(config, wheels)
    legacy.validate_installed_inventory(config, files)
    certs = create_certificates()
    metadata = validate_certificates(certs)
    certificate_files = {CERT_ROOT + "/" + name: sha(certs[name]) for name in CERT_NAMES}
    output.mkdir(parents=True)
    (output / "wheels").mkdir()
    (output / "certificates").mkdir()
    for filename, raw in downloads:
        (output / "wheels" / filename).write_bytes(raw)
    for name in CERT_NAMES:
        (output / "certificates" / name).write_bytes(certs[name])
    (output / "wrongca.pem").write_bytes(certs["wrongca.pem"])
    (output / "requirements.lock").write_bytes(legacy.requirements(config).encode())
    (output / "Dockerfile").write_bytes(dockerfile(target).encode())
    (output / "python27").write_bytes(transplant.LAUNCHER_SOURCE.encode())
    report = {"status": "prepared_not_built", "task": task, "task_record": target,
        "base_image": target["official_base_image"], "base_commit": target["base_commit"], "donor_image": DONOR,
        "profile_sha256": sha(profile_raw), "service_profile_sha256": sha(profile_raw),
        "dependency_profile_sha256": sha(dep_raw), "dependency_profile_path": legacy.PROFILE_V2_NAME,
        "wheels": wheels, "installed_files": files, "active_edges": edges, "certificate_files": certificate_files,
        "certificates_metadata": metadata, "wrong_ca_sha256": sha(certs["wrongca.pem"]),
        "dockerfile_sha256": sha(dockerfile(target).encode()), "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode()),
        "requirements_sha256": sha(legacy.requirements(config).encode()), "probe_sha256": sha(transplant.PROBE.encode()),
        "model_calls": 0, "target_setup_or_tests_executed": False, "target_or_plugins_imported": False, "calibrated": False}
    save(output / "preparation.json", report)
    return report


def validate_context(context):
    prepared = json.loads((context / "preparation.json").read_bytes())
    profile_raw, target, dep_raw, config = frozen_task(prepared["task"])
    wheels, files = [], {}
    for record in config["wheels"]:
        wheel = legacy.verified_wheel(record, (context / "wheels" / record["filename"]).read_bytes())
        if files.keys() & wheel["installed_files"].keys():
            raise ValueError("overlapping dependency inventory")
        files.update(wheel["installed_files"])
        wheels.append(wheel)
    edges = legacy.closure(config, wheels)
    legacy.validate_installed_inventory(config, files)
    certs = {name: (context / "certificates" / name).read_bytes() for name in CERT_NAMES}
    certs["wrongca.pem"] = (context / "wrongca.pem").read_bytes()
    metadata = validate_certificates(certs)
    expected = {"status": "prepared_not_built", "task_record": target, "base_image": target["official_base_image"],
        "base_commit": target["base_commit"], "donor_image": DONOR, "profile_sha256": sha(profile_raw),
        "service_profile_sha256": sha(profile_raw), "dependency_profile_sha256": sha(dep_raw),
        "dependency_profile_path": legacy.PROFILE_V2_NAME, "wheels": wheels, "installed_files": files,
        "active_edges": [list(edge) for edge in edges], "certificates_metadata": metadata,
        "certificate_files": {CERT_ROOT + "/" + name: sha(certs[name]) for name in CERT_NAMES},
        "wrong_ca_sha256": sha(certs["wrongca.pem"]), "dockerfile_sha256": sha(dockerfile(target).encode()),
        "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode()),
        "requirements_sha256": sha(legacy.requirements(config).encode()), "probe_sha256": sha(transplant.PROBE.encode()),
        "model_calls": 0, "target_setup_or_tests_executed": False, "target_or_plugins_imported": False, "calibrated": False}
    if (any(prepared.get(key) != value for key, value in expected.items())
            or (context / "Dockerfile").read_bytes() != dockerfile(target).encode()
            or (context / "python27").read_bytes() != transplant.LAUNCHER_SOURCE.encode()
            or (context / "requirements.lock").read_bytes() != legacy.requirements(config).encode()):
        raise ValueError("prepared own-base function runtime context changed")
    return prepared


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("approved Linux Docker worker and fresh output required")
    prepared = validate_context(context)
    BASE = prepared["base_image"]
    COMMIT = prepared["base_commit"]
    target = prepared["task_record"]
    if ((context / "Dockerfile").read_bytes() != dockerfile(target).encode()
            or (context / "python27").read_bytes() != transplant.LAUNCHER_SOURCE.encode()):
        raise ValueError("transplant context differs from fixed reviewed bytes")
    output.mkdir(parents=True)
    (output / "certificates").mkdir()
    for cert_name in CERT_NAMES:
        (output / "certificates" / cert_name).write_bytes((context / "certificates" / cert_name).read_bytes())
    (output / "wrongca.pem").write_bytes((context / "wrongca.pem").read_bytes())
    report = {**prepared, "status": "runtime_unavailable", "base_image": BASE, "donor_image": DONOR,
              "base_commit": COMMIT, "prefix": transplant.PREFIX, "launcher": transplant.LAUNCHER,
              "model_calls": 0, "provider_credentials": "none", "target_setup_or_tests_executed": False,
              "calibrated": False, "probe_sha256": sha(transplant.PROBE.encode()),
              "dockerfile_sha256": sha(dockerfile(target).encode()), "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode())}
    containers = []

    def execute(args, timeout=60, *, input_bytes=None):
        clean = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=clean)

    def checked(args, timeout=60):
        process = execute(args, timeout)
        if process.returncode:
            report["command_error"] = command_failure(args, process)
            raise RuntimeError("transplant command failed: " + args[1])
        return process.stdout.decode().strip()

    def inspect_image(image):
        value = json.loads(checked(["docker", "image", "inspect", image]))[0]
        if (value.get("Architecture") != "amd64" or value.get("Os") != "linux"
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("Id", ""))):
            raise ValueError("transplant image identity/platform mismatch")
        return value

    def create(image):
        name = "p07-transplant-" + uuid.uuid4().hex[:24]
        containers.append(name)
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
                 "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
                 "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image, "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (config.get("Image") != image or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                or host.get("NetworkMode") != "none" or not host.get("ReadonlyRootfs")
                or host.get("CapDrop") != ["ALL"] or "no-new-privileges" not in host.get("SecurityOpt", [])
                or host.get("Memory") != 1024**3 or host.get("NanoCpus") != 2_000_000_000
                or host.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"):
            raise ValueError("transplant probe container isolation mismatch")
        checked(["docker", "start", name])
        return name

    def source(name):
        # Git may reject a non-root probe reading a root-owned checkout unless
        # that one exact path is explicitly safe. No hooks/config are executed.
        git = ["docker", "exec", name, "git", "-c", "safe.directory=/testbed", "-C", "/testbed"]
        checked([*git, "cat-file", "-e", COMMIT + "^{commit}"])
        return {"head": checked([*git, "rev-parse", "HEAD"]),
                "diff": checked([*git, "diff", "--binary", "HEAD"]),
                "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}

    try:
        for key, image in (("base", BASE), ("donor", DONOR)):
            process = execute(["docker", "pull", image], 600)
            (output / (key + "-pull.stdout")).write_bytes(process.stdout)
            (output / (key + "-pull.stderr")).write_bytes(process.stderr)
            if process.returncode:
                raise RuntimeError(key + " immutable image pull failed")
            report[key + "_image_inspect"] = inspect_image(image)
            accepted = {image, image.removeprefix("library/"), "docker.io/" + image}
            if not set(report[key + "_image_inspect"].get("RepoDigests", [])) & accepted:
                raise ValueError(key + " image digest mismatch")
        before = create(report["base_image_inspect"]["Id"])
        report["before"] = source(before)
        tag = "p07-legacy-transplant:" + uuid.uuid4().hex
        building = execute(["docker", "build", "--network", "none", "--tag", tag,
            "--label", "atomic.p07.legacy.base=" + BASE, "--label", "atomic.p07.legacy.donor=" + DONOR,
            "--label", "atomic.p07.legacy.launcher=" + sha(transplant.LAUNCHER_SOURCE.encode()),
            "--label", "atomic.p07.requests.function.profile=" + prepared["profile_sha256"],
            "--label", "atomic.p07.requests.function.task=" + prepared["task"],
            "--label", "atomic.p07.requests.function.dependencies=" + prepared["dependency_profile_sha256"], str(context)], 900)
        (output / "build.stdout").write_bytes(building.stdout)
        (output / "build.stderr").write_bytes(building.stderr)
        if building.returncode:
            raise RuntimeError("private-prefix transplant build failed")
        image = inspect_image(tag)
        base_layers = report["base_image_inspect"]["RootFS"]["Layers"]
        layers = image["RootFS"]["Layers"]
        if not base_layers or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers):
            raise ValueError("transplant does not preserve official base ancestry")
        report.update(image_id=image["Id"], image_inspect=image)
        after = create(image["Id"])
        report["after"] = source(after)
        if report["before"] != report["after"]:
            raise ValueError("private runtime transplant changed target source")
        process = execute(["docker", "exec", after, transplant.LAUNCHER, "-c", transplant.PROBE], 45)
        (output / "probe.stdout").write_bytes(process.stdout)
        (output / "probe.stderr").write_bytes(process.stderr)
        report.update(process_exit=process.returncode, stdout_sha256=sha(process.stdout), stderr_sha256=sha(process.stderr))
        if process.returncode:
            raise RuntimeError("private-prefix runtime startup failed; original logs retained")
        report["observed"] = json.loads(process.stdout)
        compatible = transplant.validate_probe(report["observed"])
        report.update(status="runtime_probe_incompatibility", probe_compatible=compatible)
        if not compatible:
            raise ValueError("private runtime ABI qualification failed")
        inventory_code = inventory_source(prepared["installed_files"])
        (output / "inventory.py").write_bytes(inventory_code.encode())
        process = run_inventory(after, inventory_code, execute)
        (output / "inventory.stdout").write_bytes(process.stdout)
        (output / "inventory.stderr").write_bytes(process.stderr)
        report["inventory_probe_sha256"] = sha(inventory_code.encode())
        report["inventory_transfer"] = "docker_exec_stdin_no_filesystem_write"
        report["inventory_stdin_bootstrap_sha256"] = sha(STDIN_BOOTSTRAP.encode())
        report["inventory_exit"] = process.returncode
        report["inventory_stdout_sha256"] = sha(process.stdout)
        report["inventory_stderr_sha256"] = sha(process.stderr)
        if process.returncode:
            raise RuntimeError("installed dependency byte inventory failed")
        inventory = json.loads(process.stdout)
        if (inventory.get("files") != prepared["installed_files"] or inventory.get("prefix") != transplant.PREFIX
                or inventory.get("version_info") != [2, 7, 13] or inventory.get("target_or_plugins_imported") is not False):
            raise ValueError("installed dependencies differ from publisher wheels")
        report["inventory"] = inventory
        if source(after) != report["after"]:
            raise ValueError("qualification changed target source")
        certificate_code = certificate_source(prepared["certificate_files"])
        process = run_inventory(after, certificate_code, execute)
        (output / "certificates.stdout").write_bytes(process.stdout)
        (output / "certificates.stderr").write_bytes(process.stderr)
        report.update(certificate_probe_sha256=sha(certificate_code.encode()), certificate_exit=process.returncode,
                      certificate_stdout_sha256=sha(process.stdout), certificate_stderr_sha256=sha(process.stderr))
        if process.returncode or json.loads(process.stdout).get("certificate_files") != prepared["certificate_files"]:
            raise ValueError("function certificate inventory differs from reviewed bytes")
        report["certificate_inventory"] = json.loads(process.stdout)
        report["status"] = "function_runtime_qualified"
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        report["cleanup"] = []
        for name in containers:
            try:
                process = execute(["docker", "rm", "-f", name], 30)
                absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], 15)
                confirmed = absent.returncode == 0 and not absent.stdout.strip()
                report["cleanup"].append({"container": name, "exit": process.returncode, "confirmed": confirmed})
            except (OSError, subprocess.SubprocessError) as exc:
                report["cleanup"].append({"container": name, "confirmed": False, "error_type": type(exc).__name__})
        report["cleanup_confirmed"] = all(item["confirmed"] for item in report["cleanup"])
        if report["status"] == "function_runtime_qualified" and len(report["cleanup"]) != 2:
            report.update(status="runtime_unavailable", error="complete before/after qualification containers required")
        if not report["cleanup_confirmed"]:
            report.update(status="cleanup_uncertain", probe_compatible=False)
        save(output / RECEIPT, report)
    return report


def admit_receipt(directory):
    raw = (directory / RECEIPT).read_bytes()
    report = json.loads(raw)
    profile_raw, target, dep_raw, config = frozen_task(report.get("task"))
    wheels = report.get("wheels", [])
    files = {}
    for wheel in wheels:
        if files.keys() & wheel.get("installed_files", {}).keys():
            raise ValueError("receipt wheel inventory overlaps")
        files.update(wheel.get("installed_files", {}))
    expected_wheels = {(item["name"], item["version"], item["filename"], item["sha256"]) for item in config["wheels"]}
    actual_wheels = {(item.get("name"), item.get("version"), item.get("filename"), item.get("sha256")) for item in wheels}
    legacy.validate_installed_inventory(config, files)
    edges = legacy.closure(config, wheels)
    certs = {name: (directory / "certificates" / name).read_bytes() for name in CERT_NAMES}
    certs["wrongca.pem"] = (directory / "wrongca.pem").read_bytes()
    metadata = validate_certificates(certs)
    cert_files = {CERT_ROOT + "/" + name: sha(certs[name]) for name in CERT_NAMES}
    image = report.get("image_inspect", {})
    base = report.get("base_image_inspect", {})
    donor = report.get("donor_image_inspect", {})
    layers = image.get("RootFS", {}).get("Layers", [])
    base_layers = base.get("RootFS", {}).get("Layers", [])
    labels = image.get("Config", {}).get("Labels", {})
    if (report.get("status") != "function_runtime_qualified" or report.get("task_record") != target
            or report.get("base_image") != target["official_base_image"] or report.get("base_commit") != target["base_commit"]
            or report.get("donor_image") != DONOR or report.get("profile_sha256") != sha(profile_raw)
            or report.get("service_profile_sha256") != sha(profile_raw) or report.get("dependency_profile_sha256") != sha(dep_raw)
            or report.get("dependency_profile_path") != legacy.PROFILE_V2_NAME
            or actual_wheels != expected_wheels or len(wheels) != 18 or report.get("installed_files") != files
            or report.get("active_edges") != [list(edge) for edge in edges]
            or report.get("certificate_files") != cert_files or report.get("certificates_metadata") != metadata
            or report.get("wrong_ca_sha256") != sha(certs["wrongca.pem"])
            or report.get("model_calls") != 0 or report.get("calibrated") is not False
            or report.get("target_setup_or_tests_executed") is not False or report.get("target_or_plugins_imported") is not False
            or report.get("cleanup_confirmed") is not True or len(report.get("cleanup", [])) != 2
            or any(item.get("confirmed") is not True for item in report["cleanup"])
            or report.get("before") != report.get("after") or not isinstance(report.get("after"), dict)
            or not re.fullmatch(r"[0-9a-f]{40}", report["after"].get("head", ""))
            or report.get("probe_compatible") is not True or report.get("process_exit") != 0
            or not transplant.validate_probe(report.get("observed", {}))
            or report.get("inventory_exit") != 0 or report.get("inventory", {}).get("files") != files
            or report.get("inventory", {}).get("target_or_plugins_imported") is not False
            or report.get("certificate_exit") != 0 or report.get("certificate_inventory") != {
                "certificate_files": cert_files, "target_imported": False}
            or report.get("inventory_probe_sha256") != sha(inventory_source(files).encode())
            or report.get("inventory_stdin_bootstrap_sha256") != sha(STDIN_BOOTSTRAP.encode())
            or report.get("certificate_probe_sha256") != sha(certificate_source(cert_files).encode())
            or report.get("probe_sha256") != sha(transplant.PROBE.encode())
            or report.get("launcher_sha256") != sha(transplant.LAUNCHER_SOURCE.encode())
            or report.get("dockerfile_sha256") != sha(dockerfile(target).encode())
            or report.get("requirements_sha256") != sha(legacy.requirements(config).encode())
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", report.get("image_id", ""))
            or image.get("Id") != report["image_id"] or image.get("Architecture") != "amd64" or image.get("Os") != "linux"
            or not base_layers or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers)
            or target["official_base_image"] not in base.get("RepoDigests", [])
            or not {DONOR, DONOR.removeprefix("library/"), "docker.io/" + DONOR} & set(donor.get("RepoDigests", []))
            or labels.get("atomic.p07.legacy.base") != target["official_base_image"]
            or labels.get("atomic.p07.legacy.donor") != DONOR
            or labels.get("atomic.p07.legacy.launcher") != sha(transplant.LAUNCHER_SOURCE.encode())
            or labels.get("atomic.p07.requests.function.profile") != sha(profile_raw)
            or labels.get("atomic.p07.requests.function.task") != report["task"]
            or labels.get("atomic.p07.requests.function.dependencies") != sha(dep_raw)):
        raise ValueError("own-base function runtime receipt admission mismatch")
    for stem, stdout_hash, stderr_hash in (("probe", "stdout_sha256", "stderr_sha256"),
            ("inventory", "inventory_stdout_sha256", "inventory_stderr_sha256"),
            ("certificates", "certificate_stdout_sha256", "certificate_stderr_sha256")):
        if (sha((directory / (stem + ".stdout")).read_bytes()) != report.get(stdout_hash)
                or sha((directory / (stem + ".stderr")).read_bytes()) != report.get(stderr_hash)):
            raise ValueError("runtime raw probe output hash mismatch")
    return raw, report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparation = sub.add_parser("prepare")
    preparation.add_argument("--task", choices=TASK_IDS, required=True)
    preparation.add_argument("--output", type=Path, required=True)
    building = sub.add_parser("build")
    building.add_argument("--context", type=Path, required=True)
    building.add_argument("--output", type=Path, required=True)
    arguments = vars(parser.parse_args())
    action = arguments.pop("action")
    result = prepare(**arguments) if action == "prepare" else build(**arguments)
    print(json.dumps({key: result[key] for key in ("status", "task", "model_calls", "calibrated")}))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "function_runtime_qualified"} else 1)
