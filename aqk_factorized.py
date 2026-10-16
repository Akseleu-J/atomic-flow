"""Matmul formulation of the decay-weighted score matrices (Kernel A / B4 forward math).

v0.2 builds a (bc, bc, D) tensor exp(gc_i - gc_j) per block and reduces it on the VPU
(8 MiB per temporary for bc=D=128). For g <= 0 (log-decay) the kernel factorises exactly:

    exp(gc_i - gc_j) = exp(gc_i - c) * exp(c - gc_j),   c = gc[i0 - 1]   (last row BEFORE row block)

and for every i >= i0 > j:  gc_i <= c <= gc_j  =>  both factors are <= 1 (no overflow, no clip needed).
Only the small diagonal micro-blocks (mb x mb x D) keep the elementwise form.
"""
import numpy as np


def scores_reference(L_mat, R_mat, gc, mask):
    """Elementwise O(C^2 D) version (what the Pallas kernel does today, minus the +-20 clip)."""
    diff = gc[:, None, :] - gc[None, :, :]
    return np.einsum("id,ijd,jd->ij", L_mat, np.exp(diff), R_mat) * mask


def scores_factorized(L_mat, R_mat, gc, mb=16, strict=False):
    C = L_mat.shape[0]
    out = np.zeros((C, C), dtype=L_mat.dtype)
    for i0 in range(0, C, mb):
        i1 = i0 + mb
        # diagonal micro-block: elementwise, tiny
        d = gc[i0:i1, None, :] - gc[None, i0:i1, :]
        blk = np.einsum("id,ijd,jd->ij", L_mat[i0:i1], np.exp(d), R_mat[i0:i1])
        tri = np.tril(np.ones((mb, mb)), k=-1 if strict else 0)
        out[i0:i1, i0:i1] = blk * tri
        if i0 > 0:                       # everything strictly left of the block: ONE matmul
            c = gc[i0 - 1]
            left = L_mat[i0:i1] * np.exp(gc[i0:i1] - c)          # <= 1
            right = R_mat[:i0] * np.exp(c - gc[:i0])             # <= 1
            out[i0:i1, :i0] = left @ right.T
    return out
