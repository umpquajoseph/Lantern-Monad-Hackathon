"""first_person time: power throttling on/off, 640x480 vs 320x240 (read-only, monkeypatched in this process)."""
import sys, time, ctypes, numpy as np
mode, size = sys.argv[1], sys.argv[2]
if mode == "nothrottle":
    class S(ctypes.Structure): _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]
    st_ = S(1, 1, 0)   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED, state 0 = off
    k = ctypes.windll.kernel32
    ok = k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(st_), ctypes.sizeof(st_))
    print("SetProcessInformation", ok)
sys.path.insert(0, r"C:\Monad-Hackathon-render")
import sim.scene as SC
if size == "half": SC.FP_SIZE = (320, 240)
from sim.terrain import load_terrain
rid = "w00003-c44d133e"
a = np.load(rf"C:\Monad-Hackathon-Project\data\campaigns\live-nav-check2\{rid}\steps.npz")["S1-01.step"]
h, cr, x0, y0, cell = load_terrain(rf"C:\Monad-Hackathon-Project\data\terrain\run_ground\{rid}.npz")
sc = SC.Scene(h, cr, x0, y0, cell, display="off")
t = []
for i in range(400, 460):
    sc.set_rover("cam", tuple(float(v) for v in a[i, 1:4]))
    t0 = time.perf_counter(); img, d = sc.first_person("cam"); t.append(time.perf_counter() - t0)
print(mode, size, img.shape, f"first_person median {np.median(t[3:])*1e3:.0f} ms, mean {np.mean(t[3:])*1e3:.0f} ms")
