# Brief: lantern-scene (3D views)

You own the 3D scene for Lantern: what the rover's camera sees, and the
view the judges watch. Read `CLAUDE.md`, `docs/spec.html` (steps 5 and 6)
and `docs/handoffs.md` (step 2 and Scene sections) first.

## What it is for

Two rovers from two agencies drive dark lunar ground. Each finds its
position by lighting crater rims with a lamp mounted below its camera: the
lamp throws a shadow onto the near rim of each crater, and a crater
detector reads those shadow arcs from the image. That is ShadowNav's core
idea (Cauligi et al. 2023, section 3A and Figure 3; Atha et al. 2024; both
in `docs/sources/`).

The demo shows two views per rover:

- **First person:** the rover's camera, lit only by its lamp. The vision
  step runs edge detection on this image and uses its depth to place each
  rim point. So this image is both a demo visual and the sensor input, and
  its depth must be honest.
- **Third person:** a chase view behind the rover, showing the ground, the
  rover, its lamp pool, its track and its uncertainty circle. This is what
  makes the demo readable to judges who have never heard of ShadowNav.

Judging rewards "clean ui/ux and working features, end to end, no gaps".
Both views must look good and never stall the demo.

## Done means

- `sim/scene.py` implements the Scene interface in `docs/handoffs.md`. It
  is a contract with the vision step and the dashboard. If it is wrong,
  say so in your report; do not change it on your own.
- In first person, a crater 5 to 20 m ahead shows a dark shadow arc on its
  near rim, like the right half of ShadowNav's Figure 3. Depth agrees with
  the geometry: check a few pixels against a ray cast by hand.
- Third person is clear to someone seeing it for the first time.
- A short scripted drive renders to a video you have watched. Report the
  frame times.

## What is known

- Camera 2.5 m above the ground, lamp 0.2 m below the camera, 60°
  horizontal field of view (Atha et al. 2024: camera rig in section IV,
  best configuration in section V). Camera pitch, lamp cone, brightness and
  image size are not in the papers. Choose them, name them, mark them
  assumed.
- The paper used Hapke reflectance; the spec allows plain lamp shading
  instead. There is no sunlight. Any fill light in third person is for the
  viewer only, and should be labelled that way on screen.
- The terrain is a 4000 x 4000 grid at 0.25 m, about 1 km across (format in
  `docs/handoffs.md`). It is being built in parallel by lantern-terrain, so
  start on a stand-in grid of your own in the same format: a gentle slope
  with a few bowl craters 2 to 12 m wide and up to 1 m deep. When
  `C:\Monad-Hackathon-Project\data\terrain\*.npz` appears, switch to it.
- The full grid is 16 million cells, too many to mesh whole. The lamp only
  matters out to about 40 m (Atha et al. 2024, Table 1 caption).
- Machine: Windows 11, RTX 4060 laptop GPU, Python 3.13. VTK 9.3.1, PyQt5,
  OpenCV and imageio are installed; pyvista is not.

## A starting suggestion, which you may overrule

Use VTK through pyvista: one offscreen renderer gives the colour image, the
depth buffer, and both views. Spend the first 20 minutes proving that a
spotlight with shadow maps throws the rim shadow correctly. If it does not,
choose another route, such as a numpy raycast for first person or
moderngl. Mesh a fine patch around each rover and a coarse mesh for the
rest. Decide for yourself and say why.

## Hard limits

- Write only `sim/scene.py` and any files it alone needs. Another session
  is writing `sim/terrain.py` at the same time; read its output, never its
  source tree.
- Every number from a source carries the source in a comment. Every other
  number is a named parameter marked assumed.
- Work in your own git worktree on branch `step-6-scene`, cut from
  `origin/joseph`. If your working directory is the main checkout
  `C:\Monad-Hackathon-Project` itself, create a worktree first. Commit and
  push often. Do not merge into `joseph` or `main`; the lead session does
  that after review.
- Downscale renders before reading them back, unless the check needs fine
  detail.
- No commit message names a person.

## Report

When done, end with: what works, what does not, what you chose and why
(renderer, camera and lamp settings), frame times, what you changed from
this brief, and the paths of the images and video you looked at.
