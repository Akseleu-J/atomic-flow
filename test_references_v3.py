from atomic_flow.tree.references import collect_references

SRC = '''
from .configs import _reshape_to_chunks as _r2c

@partial(jax.custom_vjp, nondiff_argnums=(6, 7))
def _gdn2_core(q): return q

def _gdn2_core_fwd(q): return _r2c(q)
def _gdn2_core_bwd(res, ct): return ct

_gdn2_core.defvjp(_gdn2_core_fwd, _gdn2_core_bwd)

def run(x):
    def step(c, i): return c, i
    return jax.lax.scan(step, 0, x)
'''


def test_module_level_wiring_and_passed_callbacks_are_seen():
    refs, aliases = collect_references(SRC)
    assert {"_gdn2_core_fwd", "_gdn2_core_bwd"} <= refs[""]       # defvjp wiring (module scope)
    assert "step" in refs["run"]                                   # callback passed to scan
    assert aliases == {"_r2c": "_reshape_to_chunks"}
