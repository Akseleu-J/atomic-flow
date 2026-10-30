"""Pure private-native launcher contract tests; no target execution or imports."""
import ast
import json
from dataclasses import replace

import pytest

from atomic_flow.check_private_sympy import private_sympy_args
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckSpec, SymPyRunnerIdentity


def spec():
    runtime = RuntimeIdentity(base_image="example/sympy@sha256:" + "a" * 64,
                              base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
                              interpreter="/opt/p07-python36/bin/python3.6", python_version=(3, 6, 15),
                              runner="sympy-native", profile_sha256="d" * 64, helper_sha256="e" * 64,
                              inventory_sha256="f" * 64, provisioning_receipt_sha256="1" * 64,
                              private_prefix="/opt/p07-python36", launcher="/opt/p07-runtime/python36",
                              launcher_sha256="2" * 64)
    identity = SymPyRunnerIdentity((3, 6, 15), "1.4.dev",
                                   tuple((path, "3" * 64) for path in SYMPY_RUNNER_PATHS))
    return CheckSpec(task_id="sympy_private_synthetic", task_commit="4" * 40, base_commit="5" * 40,
                     source_revision="6" * 64, image=runtime.image_id, interpreter=runtime.interpreter,
                     runner="sympy-native", test_paths=("sympy/core/tests/test_private.py",),
                     target_root="/testbed", sympy_identity=identity, runtime_identity=runtime)


def parsed_payload(source):
    tree = ast.parse(source, feature_version=(3, 6))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "payload" for target in node.targets))
    return json.loads(ast.literal_eval(assignment.value.args[0])), tree


def test_exact_contract_payload_and_python36_grammar_without_execution():
    bound = spec()
    args = private_sympy_args(bound, "7" * 32)
    assert args[0] == "-c" and len(args) == 2
    payload, tree = parsed_payload(args[1])
    assert payload == {"runtime": bound.runtime_identity.as_dict(), "runtime_digest": bound.runtime_identity.digest,
                       "identity": bound.sympy_identity.as_dict(), "check_spec_digest": bound.digest,
                       "source_revision": bound.source_revision, "nonce": "7" * 32,
                       "root": "/testbed", "paths": list(bound.test_paths),
                       "limit": bound.resources.output_bytes_per_stream}
    assert any(isinstance(node, ast.FunctionDef) and node.name == "_parse_sympy_output" for node in tree.body)
    assert "PAYLOAD_JSON" not in args[1] and "PARSE_SOURCE" not in args[1]
    assert "ATOMIC_CHECK_SPEC_RECEIPT:" in args[1]
    assert "ATOMIC_SYMPY_BEGIN:" in args[1] and "ATOMIC_SYMPY_END:" in args[1]


def test_same_interpreter_exact_native_runner_and_fixed_warning_policy():
    source = private_sympy_args(spec(), "7" * 32)[1]
    assert "runpy.run_path(native[0],run_name='__main__')" in source
    assert "native=[os.path.join(root,'bin','test'),'-C','--verbose']+paths" in source
    assert "'native_argv':native" in source
    assert "sys.path[:0]=[os.path.join(root,'bin'),root]" in source
    assert "warnings.filterwarnings('ignore',category=UserWarning)" in source
    assert "warnings.filterwarnings('ignore',category=SyntaxWarning)" in source
    tree = ast.parse(source)
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "subprocess" not in imports and "io" not in imports
    assert "sys.pycache_prefix" not in source


def test_runtime_launcher_and_protected_runner_bytes_checked_before_and_after():
    source = private_sympy_args(spec(), "7" * 32)[1]
    assert "checked_file(runtime['launcher'],runtime['launcher_sha256'],65536)" in source
    assert "for relative,expected in identity['source_sha256'].items():" in source
    assert source.count("    protected()") == 1
    assert "\nprotected()\n" in source
    assert "sys.executable!=runtime['interpreter']" in source
    assert "sys.prefix!=runtime['private_prefix']" in source
    assert "list(sys.version_info[:3])!=[3,6,15]" in source
    assert "not sys.flags.isolated" in source and "not sys.flags.dont_write_bytecode" in source
    assert "os.environ.get('PYTHONHOME')!=runtime['private_prefix']" in source


def test_bounded_text_capture_latches_overflow_and_restores_streams():
    source = private_sympy_args(spec(), "7" * 32)[1]
    assert "if self.size+size>limit:" in source
    assert "self.overflow=True" in source
    assert "size=len(text.encode('utf-8'))" in source
    assert "if stdout.overflow or stderr.overflow or code not in (0,1):" in source
    assert "finally:\n    sys.stdout,sys.stderr=original_stdout,original_stderr" in source
    assert "_parse_sympy_output(body,paths,code)" in source


def test_dependency_observation_explicitly_does_not_claim_inventory_verification():
    source = private_sympy_args(spec(), "7" * 32)[1]
    assert "mpmath.__version__!='1.2.1'" in source
    assert "runtime['private_prefix']+'/lib/python3.6/site-packages/mpmath/'" in source
    assert "'dependency_inventory_verification':'required_separately_by_host_admission'" in source
    assert "'dependency_inventory_sha256':runtime['inventory_sha256']" in source


def test_target_bytecode_and_links_are_refused_before_any_target_import():
    source = private_sympy_args(spec(), "7" * 32)[1]
    guard = source.index("for directory,dirs,files in os.walk(root,followlinks=False):")
    imports = source.index("import mpmath,sympy")
    assert guard < imports
    assert "entries>100000 or os.path.islink(absolute) or name.endswith(('.pyc','.pyo'))" in source
    assert "for name in dirs+files:" in source


@pytest.mark.parametrize("nonce", [None, "x" * 32, "A" * 32, "a" * 31, "a" * 33])
def test_reject_bad_nonce(nonce):
    with pytest.raises(ValueError):
        private_sympy_args(spec(), nonce)


def test_reject_unbound_modern_or_identityless_specs():
    bound = spec()
    with pytest.raises(ValueError):
        private_sympy_args(bound.as_dict(), "7" * 32)
    with pytest.raises(ValueError):
        private_sympy_args(replace(bound, sympy_identity=None), "7" * 32)
    modern = replace(bound, image=bound.runtime_identity.base_image, interpreter="/usr/local/bin/python",
                     sympy_identity=replace(bound.sympy_identity, python_version=(3, 9, 20)), runtime_identity=None)
    with pytest.raises(ValueError, match="exact admitted"):
        private_sympy_args(modern, "7" * 32)
