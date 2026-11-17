"""CuPy vs numpy on a shading-like workload: bilinear gathers from a 1680^2 5 cm grid at 307k pixels plus ~60 elementwise ops, and a horizon-map march (read-only)."""
import time, numpy as np, cupy as cp
def work(xp, H, px, py):
    out = 0
    for rep in range(4):   # 4 surface queries per pixel (heights, slopes, mesh height, albedo)
        gx = (px + rep * 0.01) / 0.05; gy = py / 0.05
        i = xp.clip(gx.astype(xp.int32), 0, H.shape[1] - 2); j = xp.clip(gy.astype(xp.int32), 0, H.shape[0] - 2)
        fx = gx - i; fy = gy - j
        h = (H[j, i] * (1 - fx) * (1 - fy) + H[j, i + 1] * fx * (1 - fy) + H[j + 1, i] * (1 - fx) * fy + H[j + 1, i + 1] * fx * fy)
        out = out + h
    v = out
    for _ in range(15):    # BRDF-ish elementwise chain
        v = xp.sqrt(xp.abs(v) + 1.0) * xp.cos(v) + xp.exp(-v * v) / (1.0 + v * v)
    return v
def horizon(xp, H, n_az=1536, n_s=1000):
    az = xp.linspace(-np.pi / 2, np.pi / 2, n_az, dtype=xp.float32)[:, None]; r = xp.linspace(0.2, 80, n_s, dtype=xp.float32)[None, :]
    x = 42 + r * xp.cos(az); y = 42 + r * xp.sin(az)
    i = xp.clip((x / 0.05).astype(xp.int32), 0, H.shape[1] - 1); j = xp.clip((y / 0.05).astype(xp.int32), 0, H.shape[0] - 1)
    tan = (H[j, i] - 1.6) / r
    return tan.max(axis=1)  # cupy has no maximum.accumulate; a port needs a scan kernel
rng = np.random.default_rng(0)
Hn = rng.random((1680, 1680), dtype=np.float32); pxn = (rng.random(307200, dtype=np.float32) * 80); pyn = rng.random(307200, dtype=np.float32) * 80
Hc, pxc, pyc = cp.asarray(Hn), cp.asarray(pxn), cp.asarray(pyn)
for name, xp, H, px, py in (("numpy", np, Hn, pxn, pyn), ("cupy", cp, Hc, pxc, pyc)):
    for _ in range(2): work(xp, H, px, py); horizon(xp, H)
    if xp is cp: cp.cuda.Device().synchronize()
    t = time.perf_counter()
    for _ in range(10):
        v = work(xp, H, px, py); hz = horizon(xp, H)
        if xp is cp: img = cp.asnumpy(v)   # include the readback to host
    if xp is cp: cp.cuda.Device().synchronize()
    print(f"{name}: {(time.perf_counter() - t) * 100:.1f} ms per frame-like workload")
