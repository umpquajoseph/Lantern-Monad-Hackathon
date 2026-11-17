# Render speed experiments (worker render, 16 Nov 2026)

Scratch measurements behind the render report (ops mail 20261116-210600). None of these is run code;
each reads a check-2 run and writes only beside itself.

- `gate.py`, `gatecheck.py` (+ `.json`): share of steps a crater-geometry gate must render, and 990 frames
  outside the narrow gate rendered and read by `Vision.rims` (0 had rims).
- `gate_sites.py` (+ `.json`): gate share on the three live-run sites, sampled-engine paths.
- `gpu_fp.py`: CuPy port of `Scene.first_person` for display "off", same formulas. `gpu_test.py` (+ `.json`):
  50 check-2 poses, identical `Vision.rims` on all 50, max pixel diff 1 DN, depth exact.
  `gpu_time.py`, `gpu_prof.py`: 40 ms a frame on consecutive steps, launch-bound.
- `gpu_fused.py`: fused CUDA kernels (horizon maps, per-pixel shade). Written, not yet run. Paused.
- `bench.py`, `bench2.py`, `cupybench.py`: profiles and microbenchmarks.
