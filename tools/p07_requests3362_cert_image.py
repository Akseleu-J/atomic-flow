"""Fresh disclosed CN-only test certificates on a qualified Requests3362 runtime.

Only public benchmark-fixture TLS material changes. No target imports, package
plugins, setup or tests execute in this provisioning stage.
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

import p07_legacy_transplant as transplant
import p07_requests3362_legacy_image as legacy

CERT_ROOT = transplant.PREFIX + "/lib/python3.6/site-packages/pytest_httpbin/certs"
CERT_NAMES = ("cacert.pem", "cert.pem", "key.pem")
CERT_PATHS = tuple("pytest_httpbin/certs/" + name for name in CERT_NAMES)
RECEIPT = "requests-cert-runtime.json"


def sha(raw):
    return legacy.sha(raw)


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
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_cert.subject).public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=14))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
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
    if (leaf.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
            or leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            or leaf.extensions.get_extension_for_class(x509.KeyUsage).value != x509.KeyUsage(
                True, False, True, False, False, False, False, False, False)
            or not leaf.extensions.get_extension_for_class(x509.BasicConstraints).critical
            or not leaf.extensions.get_extension_for_class(x509.KeyUsage).critical
            or leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).critical
            or {ext.oid for ext in leaf.extensions} != {x509.ExtensionOID.BASIC_CONSTRAINTS,
                x509.ExtensionOID.KEY_USAGE, x509.ExtensionOID.EXTENDED_KEY_USAGE}
            or list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.SERVER_AUTH]
            or any(extension.oid == x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME for extension in leaf.extensions)):
        raise ValueError("CN-only server certificate profile mismatch")
    metadata["key.pem"] = {"sha256": sha(files["key.pem"]), "purpose": "public benchmark fixture only",
                           "public_key_sha256": sha(key.public_key().public_bytes(encoding, form))}
    return metadata


def admit_parent(directory):
    raw = (directory / "requests-runtime.json").read_bytes()
    report = json.loads(raw)
    try:
        raw_profile, profile = legacy.receipt_profile(report)
    except ValueError as exc:
        raise ValueError("Requests parent runtime was not fully qualified") from exc
    wheels = report.get("wheels", [])
    expected = {(item["name"], item["version"], item["filename"], item["sha256"]) for item in profile["wheels"]}
    actual = {(item.get("name"), item.get("version"), item.get("filename"), item.get("sha256")) for item in wheels}
    files = {}
    for item in wheels:
        if files.keys() & item.get("installed_files", {}).keys():
            raise ValueError("parent wheel inventory overlaps")
        files.update(item.get("installed_files", {}))
    legacy.validate_installed_inventory(profile, files)
    if (report.get("status") != "runtime_dependencies_qualified" or report.get("task") != legacy.TASK
            or report.get("base_image") != legacy.BASE or report.get("donor_image") != legacy.DONOR
            or report.get("base_commit") != legacy.COMMIT or report.get("profile_sha256") != sha(raw_profile)
            or report.get("cleanup_confirmed") is not True or len(report.get("cleanup", [])) != 2
            or any(item.get("confirmed") is not True for item in report["cleanup"])
            or report.get("probe_compatible") is not True or not transplant.validate_probe(report.get("observed", {}))
            or report.get("launcher_sha256") != sha(transplant.LAUNCHER_SOURCE.encode())
            or report.get("probe_sha256") != sha(transplant.PROBE.encode())
            or report.get("dockerfile_sha256") != sha(legacy.dockerfile().encode())
            or report.get("requirements_sha256") != sha(legacy.requirements(profile).encode())
            or report.get("inventory_exit") != 0 or report.get("before") != report.get("after")
            or report.get("installed_files") != files or actual != expected or len(wheels) != len(profile["wheels"])
            or report.get("inventory", {}).get("files") != files
            or report.get("model_calls") != 0 or report.get("target_or_plugins_imported") is not False
            or report.get("target_setup_or_tests_executed") is not False
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", report.get("image_id", ""))
            or report.get("image_inspect", {}).get("Id") != report["image_id"]
            or any(path not in files for path in CERT_PATHS)):
        raise ValueError("Requests parent runtime was not fully qualified")
    return raw, report


def parent_tag(image_id):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("immutable parent image required")
    return "p07-requests-cert-parent:" + image_id.split(":", 1)[1]


def dockerfile(image_id):
    return "FROM " + parent_tag(image_id) + "\nUSER root\n" + "".join(
        "COPY certificates/" + name + " " + CERT_ROOT + "/" + name + "\n" for name in CERT_NAMES)


def read_certificates(directory):
    return {**{name: (directory / "certificates" / name).read_bytes() for name in CERT_NAMES},
            "wrongca.pem": (directory / "wrongca.pem").read_bytes()}


def prepare(runtime_receipt, output):
    if output.exists():
        raise ValueError("fresh certificate context required")
    raw_parent, parent = admit_parent(runtime_receipt)
    files = create_certificates()
    metadata = validate_certificates(files)
    output.mkdir(parents=True)
    (output / "certificates").mkdir()
    for name in CERT_NAMES:
        (output / "certificates" / name).write_bytes(files[name])
    (output / "wrongca.pem").write_bytes(files["wrongca.pem"])
    (output / "parent-runtime.json").write_bytes(raw_parent)
    (output / "Dockerfile").write_bytes(dockerfile(parent["image_id"]).encode())
    overlay = {"pytest_httpbin/certs/" + name: sha(files[name]) for name in CERT_NAMES}
    result = {"status": "prepared_not_built", "task": legacy.TASK, "parent_image_id": parent["image_id"],
        "parent_runtime_report_sha256": sha(raw_parent), "profile_sha256": parent["profile_sha256"],
        "profile_path": parent.get("profile_path", legacy.PROFILE_V1_NAME),
        "original_certificate_files": {path: parent["installed_files"][path] for path in CERT_PATHS},
        "overlay_files": overlay, "certificate_metadata": metadata,
        "dockerfile_sha256": sha(dockerfile(parent["image_id"]).encode()), "model_calls": 0,
        "target_setup_or_tests_executed": False, "target_or_plugins_imported": False, "calibrated": False,
        "public_test_fixture_private_key": True, "CA_signing_key_retained": False}
    legacy.save(output / "preparation.json", result)
    return result


def validate_context(context):
    prepared = json.loads((context / "preparation.json").read_bytes())
    raw_parent, parent = admit_parent_file(context / "parent-runtime.json")
    metadata = validate_certificates(read_certificates(context))
    overlay = {path: metadata[path.rsplit("/", 1)[1]]["sha256"] for path in CERT_PATHS}
    if (prepared.get("status") != "prepared_not_built" or prepared.get("task") != legacy.TASK
            or prepared.get("parent_runtime_report_sha256") != sha(raw_parent)
            or prepared.get("parent_image_id") != parent["image_id"]
            or prepared.get("profile_sha256") != parent["profile_sha256"]
            or prepared.get("original_certificate_files") != {path: parent["installed_files"][path] for path in CERT_PATHS}
            or prepared.get("certificate_metadata") != metadata or prepared.get("overlay_files") != overlay
            or prepared.get("dockerfile_sha256") != sha(dockerfile(parent["image_id"]).encode())
            or (context / "Dockerfile").read_bytes() != dockerfile(parent["image_id"]).encode()
            or prepared.get("model_calls") != 0 or prepared.get("target_or_plugins_imported") is not False
            or prepared.get("target_setup_or_tests_executed") is not False
            or prepared.get("CA_signing_key_retained") is not False):
        raise ValueError("prepared certificate context changed")
    return prepared, raw_parent, parent


def admit_parent_file(path):
    # Share the same strict reader without copying/rewriting the original bytes.
    class Directory:
        def __truediv__(self, name):
            if name != "requests-runtime.json":
                raise ValueError("unexpected parent receipt path")
            return path
    return admit_parent(Directory())


def admit_certificate_receipt(directory):
    raw = (directory / RECEIPT).read_bytes()
    report = json.loads(raw)
    parent_raw = (directory / "parent-runtime.json").read_bytes()
    _, parent = admit_parent_file(directory / "parent-runtime.json")
    metadata = validate_certificates(read_certificates(directory))
    overlay = {path: metadata[path.rsplit("/", 1)[1]]["sha256"] for path in CERT_PATHS}
    expected_files = {**parent["installed_files"], **overlay}
    if (report.get("status") != "certificate_overlay_qualified" or report.get("task") != legacy.TASK
            or report.get("parent_runtime_report_sha256") != sha(parent_raw)
            or report.get("parent_runtime_report") != parent or report.get("parent_image_id") != parent["image_id"]
            or report.get("profile_sha256") != parent["profile_sha256"]
            or report.get("overlay_files") != overlay or report.get("installed_files") != expected_files
            or report.get("certificate_metadata") != metadata
            or report.get("original_certificate_files") != {path: parent["installed_files"][path] for path in CERT_PATHS}
            or report.get("CA_signing_key_retained") is not False
            or report.get("before") != parent["after"]
            or report.get("before") != report.get("after") or report.get("cleanup_confirmed") is not True
            or len(report.get("cleanup", [])) != 2 or any(not item.get("confirmed") for item in report["cleanup"])
            or report.get("probe_compatible") is not True or not transplant.validate_probe(report.get("observed", {}))
            or report.get("model_calls") != 0 or report.get("target_or_plugins_imported") is not False
            or report.get("target_setup_or_tests_executed") is not False
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", report.get("image_id", ""))
            or report.get("image_inspect", {}).get("Id") != report["image_id"]):
        raise ValueError("certificate overlay receipt qualification mismatch")
    return raw, report


def build(context, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("fresh output on approved Linux Docker worker required")
    prepared, raw_parent, parent = validate_context(context)
    output.mkdir(parents=True)
    (output / "certificates").mkdir()
    for name in CERT_NAMES:
        (output / "certificates" / name).write_bytes((context / "certificates" / name).read_bytes())
    (output / "wrongca.pem").write_bytes((context / "wrongca.pem").read_bytes())
    (output / "parent-runtime.json").write_bytes(raw_parent)
    report = {**prepared, "status": "certificate_overlay_unavailable", "parent_runtime_report": parent,
              "base_image": legacy.BASE, "donor_image": legacy.DONOR, "base_commit": legacy.COMMIT,
              "installed_files": {**parent["installed_files"], **prepared["overlay_files"]},
              "provider_credentials": "none", "launcher_sha256": sha(transplant.LAUNCHER_SOURCE.encode())}
    names = []

    def execute(args, timeout=60, *, input_bytes=None):
        env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=env)

    def checked(args, timeout=60):
        process = execute(args, timeout)
        if process.returncode:
            report["command_error"] = legacy.command_failure(args, process)
            raise RuntimeError("fixed certificate worker command failed")
        return process.stdout.decode().strip()

    def create(image_id):
        name = "p07-cert-" + uuid.uuid4().hex[:24]
        names.append(name)
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
            "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
            "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image_id, "infinity"])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        host = config["HostConfig"]
        if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                or host.get("NetworkMode") != "none" or not host.get("ReadonlyRootfs")
                or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
                or "no-new-privileges" not in host.get("SecurityOpt", []) or host.get("Memory") != 1024**3
                or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 128
                or config["Config"].get("User") != "65534:65534"):
            raise ValueError("certificate worker isolation mismatch")
        checked(["docker", "start", name])
        return name

    def source(name):
        git = ["docker", "exec", name, "git", "-c", "safe.directory=/testbed", "-C", "/testbed"]
        return {"head": checked([*git, "rev-parse", "HEAD"]), "diff": checked([*git, "diff", "--binary", "HEAD"]),
                "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}

    def inventory(name, files, label):
        code = legacy.inventory_source(files)
        process = legacy.run_inventory(name, code, execute)
        (output / (label + "-inventory.stdout")).write_bytes(process.stdout)
        (output / (label + "-inventory.stderr")).write_bytes(process.stderr)
        report[label + "_inventory"] = {"exit": process.returncode, "source_sha256": sha(code.encode()),
            "stdout_sha256": sha(process.stdout), "stderr_sha256": sha(process.stderr)}
        if process.returncode or json.loads(process.stdout).get("files") != files:
            raise ValueError("certificate layer installed-byte inventory mismatch")

    try:
        actual = json.loads(checked(["docker", "image", "inspect", parent["image_id"]]))[0]
        if (actual.get("Id") != parent["image_id"] or actual.get("RootFS") != parent["image_inspect"].get("RootFS")
                or actual.get("Architecture") != "amd64" or actual.get("Os") != "linux"
                or actual.get("Config", {}).get("Labels", {}).get("atomic.p07.requests.profile") != parent["profile_sha256"]):
            raise ValueError("qualified parent image identity mismatch")
        name = create(parent["image_id"])
        report["before"] = source(name)
        if report["before"] != parent["after"]:
            raise ValueError("parent target source differs from qualification")
        inventory(name, parent["installed_files"], "parent")
        checked(["docker", "tag", parent["image_id"], parent_tag(parent["image_id"])])
        if json.loads(checked(["docker", "image", "inspect", parent_tag(parent["image_id"])]))[0]["Id"] != parent["image_id"]:
            raise ValueError("local certificate parent tag changed")
        tag = "p07-requests-cert:" + uuid.uuid4().hex
        process = execute(["docker", "build", "--network", "none", "--pull=false", "--tag", tag,
            "--label", "atomic.p07.requests.cert.parent=" + parent["image_id"],
            "--label", "atomic.p07.requests.cert.overlay=" + sha(json.dumps(prepared["overlay_files"], sort_keys=True).encode()),
            str(context)], 600)
        (output / "build.stdout").write_bytes(process.stdout)
        (output / "build.stderr").write_bytes(process.stderr)
        if process.returncode:
            raise RuntimeError("fixed offline certificate layer build failed")
        image = json.loads(checked(["docker", "image", "inspect", tag]))[0]
        layers = parent["image_inspect"]["RootFS"]["Layers"]
        if (not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", "")) or not layers
                or image["RootFS"]["Layers"][:len(layers)] != layers or len(image["RootFS"]["Layers"]) <= len(layers)
                or image.get("Architecture") != "amd64" or image.get("Os") != "linux"):
            raise ValueError("certificate overlay image ancestry mismatch")
        report.update(image_id=image["Id"], image_inspect=image)
        name = create(image["Id"])
        report["after"] = source(name)
        if report["after"] != report["before"]:
            raise ValueError("certificate overlay changed target source")
        inventory(name, report["installed_files"], "overlay")
        process = execute(["docker", "exec", name, transplant.LAUNCHER, "-c", transplant.PROBE], 45)
        (output / "probe.stdout").write_bytes(process.stdout)
        (output / "probe.stderr").write_bytes(process.stderr)
        report["observed"] = json.loads(process.stdout) if not process.returncode else {}
        report["probe_compatible"] = process.returncode == 0 and transplant.validate_probe(report["observed"])
        report["probe_output"] = {"exit": process.returncode, "stdout_sha256": sha(process.stdout), "stderr_sha256": sha(process.stderr)}
        if not report["probe_compatible"]:
            raise ValueError("certificate layer private ABI differs")
        if source(name) != report["after"]:
            raise ValueError("certificate qualification changed target source")
        report["status"] = "certificate_overlay_qualified"
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        report["cleanup"] = []
        for name in names:
            try:
                removed = execute(["docker", "rm", "-f", name], 30)
                absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], 15)
                report["cleanup"].append({"container": name, "exit": removed.returncode,
                    "confirmed": absent.returncode == 0 and not absent.stdout.strip()})
            except (OSError, subprocess.SubprocessError) as exc:
                report["cleanup"].append({"container": name, "confirmed": False, "error_type": type(exc).__name__})
        report["cleanup_confirmed"] = len(report["cleanup"]) == 2 and all(item["confirmed"] for item in report["cleanup"])
        if not report["cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        legacy.save(output / RECEIPT, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    preparing = sub.add_parser("prepare")
    preparing.add_argument("--runtime-receipt", type=Path, required=True)
    preparing.add_argument("--output", type=Path, required=True)
    building = sub.add_parser("build")
    building.add_argument("--context", type=Path, required=True)
    building.add_argument("--output", type=Path, required=True)
    arguments = vars(parser.parse_args())
    action = arguments.pop("action")
    result = prepare(**arguments) if action == "prepare" else build(**arguments)
    print(json.dumps({key: result[key] for key in ("status", "model_calls", "calibrated")}, sort_keys=True))
    raise SystemExit(0 if result["status"] in {"prepared_not_built", "certificate_overlay_qualified"} else 1)
