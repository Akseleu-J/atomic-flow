"""Check pinned xdist double-build artifact in an isolated focused runtime.

This is a compatibility smoke, not a full suite, model result or admission.
Credentials are read only to download one fixed GitHub artifact on the host.
All target imports and public test execution occur in a bounded container.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as py38
import p07_pytest6116_xdist_build as built
import p07_pytest6116_xdist_prepare as source

from atomic_flow.check_requests_execution import _bounded

ARTIFACT_ID = 12924300891
ARCHIVE_SHA = "d1f83290bcd0c15a7ff0b0651e3eae9f17be9de12682724d2db1d68a3341041e"
WHEEL_SHA = "5fa22b9449f0afbcec12430a550ef9242a2307725b7409fba7e80b8de48db347"
BUILD_COMMIT = "04c3e69109b5ee4d3d5a2c830290fa7b96edee0a"
ARTIFACT_URL = f"https://api.github.com/repos/Akseleu-J/atomic-flow/actions/artifacts/{ARTIFACT_ID}/zip"
WHEEL_ITEM = {"name": "pytest-xdist", "version": "1.18.2", "filename": built.WHEEL, "sha256": WHEEL_SHA}
SMOKE = r"""
import json,os,sys
sys.dont_write_bytecode=True
os.environ['HYPOTHESIS_STORAGE_DIRECTORY']='/tmp/p07-hypothesis'
import pytest,_pytest,xdist,xdist.boxed,execnet
if sys.version_info[:3]!=(3,8,20) or os.path.realpath(pytest.__file__)!='/testbed/src/pytest.py': raise SystemExit(125)
class Recorder:
    def __init__(self): self.nodes=[];self.completed=[];self.outcomes={};self.plugins={};self.errors=[];self.finished=False
    def pytest_sessionstart(self,session):
        pm=session.config.pluginmanager
        self.plugins={n:bool(pm.hasplugin(n)) for n in ('xdist','xdist.boxed','xdist.looponfail')}
    def pytest_collection_finish(self,session): self.nodes=[i.nodeid for i in session.items]
    def pytest_collectreport(self,report):
        if report.failed or report.skipped:self.errors.append(report.nodeid)
    def pytest_runtest_logreport(self,report):
        k=report.when+':'+report.outcome;self.outcomes[k]=self.outcomes.get(k,0)+1
        if report.when=='teardown':self.completed.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
args=['-q','--tb=short','-o','cache_dir=/tmp/p07-cache',
      'testing/test_runner.py::TestExecutionForked::test_passfunction',
      'testing/test_runner.py::TestExecutionForked::test_failfunction']
code=int(pytest.main(args,plugins=[r]))
print('\nP07_XDIST_SMOKE='+json.dumps({'nodes':r.nodes,'completed':r.completed,'outcomes':r.outcomes,
 'plugins':r.plugins,'errors':r.errors,'finished':r.finished,'exit_code':code,
 'xdist':os.path.realpath(xdist.__file__),'boxed':os.path.realpath(xdist.boxed.__file__),
 'execnet':os.path.realpath(execnet.__file__)},sort_keys=True),flush=True)
raise SystemExit(code)
"""


def verify_build_artifact(raw):
    """Bind both wheel bytes to the fixed diagnostic archive and receipt."""
    if len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError("build artifact archive differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [item.filename for item in z.infolist()]
        if len(names) != len(set(names)) or len(names) > 100:
            raise ValueError("ambiguous build artifact")
        required = [
            "tested-commit.txt",
            "diagnostic/summary.json",
            "diagnostic/preparation.json",
            "diagnostic/build-1.whl",
            "diagnostic/build-2.whl",
        ]
        if any(name not in names or z.getinfo(name).file_size > 16_000_000 for name in required):
            raise ValueError("build evidence incomplete")
        commit = z.read(required[0]).decode("ascii").strip()
        summary = json.loads(z.read(required[1]))
        preparation = json.loads(z.read(required[2]))
        first, second = z.read(required[3]), z.read(required[4])
    attempts = summary.get("attempts", [])
    if (
        commit != BUILD_COMMIT
        or summary.get("status") != "offline_double_build_byte_identical"
        or summary.get("runtime_acceptance") is not False
        or len(attempts) != 2
            or any(x.get("status") != "built" or x.get("cleanup_confirmed") is not True or x.get("wheel_sha256") != WHEEL_SHA for x in attempts)
        or preparation.get("profile_sha256") != source.PROFILE_SHA256
        or first != second
        or hashlib.sha256(first).hexdigest() != WHEEL_SHA
    ):
        raise ValueError("build receipt, commit or wheel identity differs")
    built.check_wheel(first)
    return first, summary


def download_artifact():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub artifact read credential unavailable")
    request = urllib.request.Request(
        ARTIFACT_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "atomic-flow-p07-xdist-runtime/1",
        },
    )

    class StripRedirectAuthorization(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.startswith("https://"):
                raise ValueError("artifact redirect must use HTTPS")
            redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
            if redirected is not None:
                redirected.remove_header("Authorization")
                redirected.remove_header("authorization")
            return redirected

    opener = urllib.request.build_opener(StripRedirectAuthorization())
    with opener.open(request, timeout=60) as response:
        return response.read(20_000_001)


def run(output):
    if os.name != "posix" or output.exists():
        raise ValueError("fresh output and Linux host required")
    output.mkdir(parents=True)
    report = {
        "status": "not_qualified",
        "scope": "two public tests and plugin smoke only",
        "source_build_run": 40933846341,
        "source_artifact": ARTIFACT_ID,
        "source_archive_sha256": ARCHIVE_SHA,
        "source_wheel_sha256": WHEEL_SHA,
        "ordinary_acceptance": False,
        "provider_calls": 0,
    }
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/tmp", "LANG": "C", "LC_ALL": "C"}

    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix="p07-xdist-smoke-") as temp:
        tmp = Path(temp)
        cidfile = tmp / "cid"
        cid = None
        try:
            wheel, build_receipt = verify_build_artifact(download_artifact())
            (output / "source-build-summary.json").write_text(json.dumps(build_receipt, indent=2, sort_keys=True) + "\n")
            wheelhouse = tmp / "optional"
            prep = optional.prepare(wheelhouse, with_pexpect=True)
            extra = tmp / "xdist-input"
            source.prepare(extra)
            profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            profile["wheels"].extend(json.loads(optional.PEXPECT_PROFILE.read_bytes())["wheels"])
            execnet = json.loads(source.PROFILE.read_bytes())["runtime_wheels"][0]
            profile["wheels"].extend([execnet, WHEEL_ITEM])
            context = tmp / "context"
            context.mkdir()
            for item in profile["wheels"][:-2]:
                shutil.copyfile(wheelhouse / item["filename"], context / item["filename"])
            shutil.copyfile(extra / execnet["filename"], context / execnet["filename"])
            (context / built.WHEEL).write_bytes(wheel)
            (output / "wheel-preparation.json").write_text(json.dumps(prep, indent=2, sort_keys=True) + "\n")
            image_bases = {}
            for name, image in (("python", profile["python_image"]), ("source", profile["source_image"])):
                pull = command([recon.ENGINE, "pull", image], timeout=240)
                (output / (name + "-pull.stdout")).write_bytes(pull.stdout)
                (output / (name + "-pull.stderr")).write_bytes(pull.stderr)
                if pull.returncode:
                    raise ValueError("pinned image pull failed: " + name)
                inspect = command([recon.ENGINE, "image", "inspect", image])
                item = json.loads(inspect.stdout)[0]
                if inspect.returncode or image not in item.get("RepoDigests", []) or item.get("Architecture") != "amd64":
                    raise ValueError("image identity differs")
                image_bases[name] = item
                (output / (name + "-image-inspect.json")).write_bytes(inspect.stdout)
            inject = (
                "from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")"
            )
            dockerfile = (
                "FROM "
                + profile["source_image"]
                + " AS target\nFROM "
                + profile["python_image"]
                + "\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\n"
                + "RUN --network=none /usr/local/bin/python -I -B -m pip --isolated install --no-index --no-deps --no-compile /opt/p07-wheels/*.whl\n"
                + "RUN --network=none /usr/local/bin/python -I -B -c "
                + shlex.quote(inject)
                + "\n"
            )
            (context / "Dockerfile").write_text(dockerfile)
            (output / "Dockerfile").write_text(dockerfile)
            build = command(
                [recon.ENGINE, "build", "--pull=false", "--network=none", "--iidfile", str(tmp / "iid"), str(context)], timeout=300
            )
            (output / "build.stdout").write_bytes(build.stdout)
            (output / "build.stderr").write_bytes(build.stderr)
            if build.returncode:
                raise ValueError("runtime image build failed")
            iid = (tmp / "iid").read_text().strip()
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", iid):
                raise ValueError("image ID missing")
            image_inspect = command([recon.ENGINE, "image", "inspect", iid])
            image_info = json.loads(image_inspect.stdout)[0] if not image_inspect.returncode else {}
            base_layers = image_bases["python"].get("RootFS", {}).get("Layers", [])
            layers = image_info.get("RootFS", {}).get("Layers", [])
            if not base_layers or image_info.get("Id") != iid or layers[:len(base_layers)] != base_layers or len(layers) <= len(base_layers):
                raise ValueError("runtime image ancestry differs")
            (output / "image-inspect.json").write_bytes(image_inspect.stdout)
            created = command(
                [
                    recon.ENGINE,
                    "create",
                    "--cidfile",
                    str(cidfile),
                    "--init",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--user=65534:65534",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=1g",
                    "--cpus=2",
                    "--pids-limit=128",
                    "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777",
                    "--workdir=/testbed",
                    "--env=HOME=/tmp",
                    "--entrypoint=/bin/sleep",
                    iid,
                    "infinity",
                ]
            )
            if created.returncode:
                raise ValueError("container create failed")
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", cid):
                raise ValueError("invalid container ID")
            detail = command([recon.ENGINE, "inspect", cid])
            c = json.loads(detail.stdout)[0]
            h = c["HostConfig"]
            if (
                c["Id"] != cid
                or c["Image"] != iid
                or c["State"]["Running"] is not False
                or c["Config"]["User"] != "65534:65534"
                or h["NetworkMode"] != "none"
                or not h["ReadonlyRootfs"]
                or not h["Init"]
                or h["Privileged"]
                or h["CapDrop"] != ["ALL"]
                or h.get("CapAdd")
                or h.get("Binds")
                or h.get("VolumesFrom")
                or c.get("Mounts")
                or h["Memory"] != 1024**3
                or h["NanoCpus"] != 2_000_000_000
                or h["PidsLimit"] != 128
                or "no-new-privileges" not in h.get("SecurityOpt", [])
            ):
                raise ValueError("container controls differ")
            (output / "container-inspect.json").write_bytes(detail.stdout)
            verified, inventory, extras, wrappers = py38.audit_relocated_wheels(command, cid, context, profile, tmp / "installed")
            (output / "installed-inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
            (output / "generated-scripts.json").write_text(json.dumps(wrappers, indent=2, sort_keys=True) + "\n")
            report.update(
                image_id=iid,
                installed_publisher_files=verified["publisher_files_verified"],
                pip_metadata_extras=extras,
                generated_script_contents_verified=False,
            )
            if command([recon.ENGINE, "start", cid]).returncode:
                raise ValueError("container start failed")
            tree = json.loads(recon.PUBLIC_TREE.read_bytes())
            if tree.get("base_commit") != recon.BASE or len(tree.get("blobs", [])) != 474:
                raise ValueError("public tree identity differs")
            check = command(
                [
                    recon.ENGINE,
                    "exec",
                    "--user=65534:65534",
                    cid,
                    py38.PYTHON,
                    "-I",
                    "-B",
                    "-c",
                    recon.VERIFY_SOURCE,
                    json.dumps(tree["blobs"], separators=(",", ":")),
                ],
                timeout=90,
            )
            (output / "source-verification.stdout").write_bytes(check.stdout)
            if check.returncode or json.loads(check.stdout) != {"checked": 474, "differences": []}:
                raise ValueError("public source differs")
            scheme = command(
                [recon.ENGINE, "exec", "--user=65534:65534", "-w", "/tmp", cid, py38.PYTHON, "-I", "-B", "-c", py38.SCHEME_PROBE]
            )
            if scheme.returncode or scheme.stderr or json.loads(scheme.stdout) != py38.FIXED_SCHEME:
                raise ValueError("Python or pip scheme differs")
            (output / "scheme.stdout").write_bytes(scheme.stdout)
            result = _bounded(
                [recon.ENGINE, "exec", "--user=65534:65534", "-w", "/testbed", cid, py38.PYTHON, "-I", "-B", "-c", SMOKE],
                home=tmp,
                timeout=180,
                limit=1_000_000,
                check=lambda: None,
            )
            (output / "smoke.stdout").write_bytes(result.stdout.encode())
            (output / "smoke.stderr").write_bytes(result.stderr.encode())
            report.update(
                exit_code=result.returncode,
                timed_out=result.timed_out,
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
                stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
            )
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError("smoke output incomplete")
            markers = [line.removeprefix("P07_XDIST_SMOKE=") for line in result.stdout.splitlines() if line.startswith("P07_XDIST_SMOKE=")]
            if len(markers) != 1:
                raise ValueError("smoke marker missing")
            obs = json.loads(markers[0])
            (output / "observation.json").write_text(json.dumps(obs, indent=2, sort_keys=True) + "\n")
            expected_nodes = [
                "testing/test_runner.py::TestExecutionForked::test_passfunction",
                "testing/test_runner.py::TestExecutionForked::test_failfunction",
            ]
            if (
                not obs["finished"]
                or obs["exit_code"] != result.returncode
                or obs["nodes"] != expected_nodes
                or obs["completed"] != expected_nodes
                or obs["errors"]
                or len(set(obs["nodes"])) != 2
            ):
                raise ValueError("smoke inventory differs")
            report["outcomes"] = obs["outcomes"]
            report["plugins"] = obs["plugins"]
            paths = {"xdist": "/usr/local/lib/python3.8/site-packages/xdist/__init__.py",
                     "boxed": "/usr/local/lib/python3.8/site-packages/xdist/boxed.py",
                     "execnet": "/usr/local/lib/python3.8/site-packages/execnet/__init__.py"}
            expected_outcomes = {"setup:passed": 2, "call:passed": 2, "teardown:passed": 2}
            expected_plugins = {"xdist": True, "xdist.boxed": True, "xdist.looponfail": True}
            report["module_paths"] = {name: obs[name] for name in paths}
            report["status"] = ("focused_smoke_passed" if result.returncode == 0 and obs["plugins"] == expected_plugins
                                and obs["outcomes"] == expected_outcomes
                                and all(obs[name] == value for name, value in paths.items()) else "focused_smoke_failed")
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text().strip()
            if cid and re.fullmatch(r"[0-9a-f]{64}", cid):
                removed = command([recon.ENGINE, "rm", "-f", cid])
                absent = command([recon.ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + cid, "--format", "{{.ID}}"])
                report["cleanup_confirmed"] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report["cleanup_confirmed"]:
                    report["status"] = "cleanup_uncertain"
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result["status"] == "focused_smoke_passed" else 1)
