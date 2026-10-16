"""Exact padding wrapper: lets any sequence length run on kernels that need L % bt == 0.

Pad tokens have k = b = v = w = 0 and g = 0:  alpha = exp(0) = 1 and the write is k (x) v_new = 0,
so the state is untouched and h_final is exact; padded outputs are simply sliced away.
"""
import jax.numpy as jnp


def pad_to_multiple(x, multiple: int, axis: int = 1):
    pad = (-x.shape[axis]) % multiple
    if pad == 0:
        return x, 0
    widths = [(0, 0)] * x.ndim
    widths[axis] = (0, pad)
    return jnp.pad(x, widths), pad


def gdn2_forward_any_length(fn, q, k, v, w, b, g, scale, *, bt: int, **kw):
    """fn = gdn2_pallas_forward_trainable (or any callable with the same signature)."""
    L = q.shape[1]
    q, pad = pad_to_multiple(q, bt)
    k, _ = pad_to_multiple(k, bt)
    v, _ = pad_to_multiple(v, bt)
    w, _ = pad_to_multiple(w, bt)
    b, _ = pad_to_multiple(b, bt)
    g, _ = pad_to_multiple(g, bt)          # zero-pad => alpha == 1
    o, h_final = fn(q, k, v, w, b, g, scale, **kw)
    return o[:, :L], h_final
