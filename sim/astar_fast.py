"""Compiled copy of SlopeMap.plan's search for the sensing rover's call
(cost given, float32 cost grid), with the same arithmetic and the same order.

Why it matches the Python loop value for value: under NumPy 2 promotion the
Python loop's costs are np.float32 (cost_2m is float32 and every Python float
it meets is cast to float32 first), so here every step is done in float32 in
the same order: 1 + c, w * that, g + that, key = g + float32(hypot). The
hypot values come from a table built with Python's math.hypot itself. Pops
follow (key, cell) order, the order heapq gives tuples, and the search keeps
no closed set (a cell is expanded again if popped again), as the loop does.

Without numba, available() is False and SlopeMap.plan runs its Python loop.
"""
from __future__ import annotations

import math

import numpy as np

try:
    import numba as _nb
except ImportError:          # the Python loop stays the path
    _nb = None


def available():
    return _nb is not None


def hypot_table(ny, nx):
    """float32(math.hypot(di, dj)) for 0 <= di < ny, 0 <= dj < nx."""
    t = np.empty((ny, nx), dtype=np.float64)
    hy = math.hypot
    for i in range(ny):
        t[i] = [hy(i, j) for j in range(nx)]
    return t.astype(np.float32)


if _nb is not None:
    @_nb.njit(cache=True)
    def _less(k1, n1, k2, n2):
        return k1 < k2 or (k1 == k2 and n1 < n2)

    @_nb.njit(cache=True)
    def search(okb, blk, costf, hyp, ny, nx, sf, gf, max_expand, gcost, came, stamp, tag):
        """Returns (status, path): status 0 = reached or emptied (path from
        start to goal if reached, else empty), 1 = budget spent (None)."""
        cap = 8 * max_expand + 16
        hk = np.empty(cap, np.float32)
        hn = np.empty(cap, np.int64)
        size = 0
        gi, gj = gf // nx, gf % nx
        si, sj = sf // nx, sf % nx
        # The start entry is popped before any other is pushed: its key is never compared.
        hk[0] = np.float32(0.0)
        hn[0] = sf
        size = 1
        stamp[sf] = tag
        gcost[sf] = np.float32(0.0)
        came[sf] = -1
        DI = np.array([-1, -1, -1, 0, 0, 1, 1, 1])
        DJ = np.array([-1, 0, 1, -1, 1, -1, 0, 1])
        W = np.array([1.4142, 1.0, 1.4142, 1.0, 1.0, 1.4142, 1.0, 1.4142], np.float32)
        one = np.float32(1.0)
        big = np.float32(1e18)
        expanded = 0
        reached = False
        while size > 0:
            # pop the least (key, cell)
            c = hn[0]
            size -= 1
            if size > 0:
                lk, ln = hk[size], hn[size]
                pos = 0
                while True:
                    ch = 2 * pos + 1
                    if ch >= size:
                        break
                    if ch + 1 < size and _less(hk[ch + 1], hn[ch + 1], hk[ch], hn[ch]):
                        ch += 1
                    if _less(hk[ch], hn[ch], lk, ln):
                        hk[pos], hn[pos] = hk[ch], hn[ch]
                        pos = ch
                    else:
                        break
                hk[pos], hn[pos] = lk, ln
            expanded += 1
            if expanded > max_expand:
                return 1, np.empty(0, np.int64)
            if c == gf:
                reached = True
                break
            gc = gcost[c]
            ci, cj = c // nx, c % nx
            for t in range(8):
                ni, nj = ci + DI[t], cj + DJ[t]
                if not (0 <= ni < ny and 0 <= nj < nx):
                    continue
                n = ni * nx + nj
                if not ((okb[n] and not blk[n]) or n == sf or n == gf):
                    continue
                ng = gc + W[t] * (one + costf[n])
                old = gcost[n] if stamp[n] == tag else big
                if ng < old:
                    stamp[n] = tag
                    gcost[n] = ng
                    came[n] = c
                    key = ng + hyp[abs(gi - ni), abs(gj - nj)]
                    # push
                    if size >= cap:
                        nk = np.empty(2 * cap, np.float32); nk[:size] = hk[:size]; hk = nk
                        nn = np.empty(2 * cap, np.int64); nn[:size] = hn[:size]; hn = nn
                        cap *= 2
                    pos = size
                    size += 1
                    while pos > 0:
                        par = (pos - 1) // 2
                        if _less(key, n, hk[par], hn[par]):
                            hk[pos], hn[pos] = hk[par], hn[par]
                            pos = par
                        else:
                            break
                    hk[pos], hn[pos] = key, n
        if not reached and not (stamp[gf] == tag and came[gf] >= 0) and gf != sf:
            return 0, np.empty(0, np.int64)
        # path from start to goal
        m = 1
        v = gf
        while v != sf:
            v = came[v]
            m += 1
        out = np.empty(m, np.int64)
        v = gf
        for k in range(m - 1, -1, -1):
            out[k] = v
            if k:
                v = came[v]
        return 0, out
