# Lantern: to do after the checkpoint merge

Updated 13:56. Final merge 15:00: last pushes 14:40, integration frozen 14:45, full runs 14:45-15:00. `main` is the checkpoint (25acb62), with every branch
merged. Switch to it. The `merger` worker is still cleaning up on
`checkpoint`, and `main` will be updated again when that is done.

## The demo (decided)
Kellen's 2-site mission, `run_mission.py` (decided 14:04). The Surveyor finds
S1 dry and S2 icy, then posts the find to Kellen's Lantern contract on
Monad testnet. The Prospector reads it, drives over and confirms it. It runs
on our renderer, ParticleNav, planner, lidar and vision, with NASA rover
models. run1/run2 are the backup.

## To do
| # | Item | Who |
|---|---|---|
| 1 | One sim: `run_mission.py` merged into integration, rendered with vision | Kellen + scene |
| 2 | Site plays the mission: NASA models, science stops, verify button | viewer |
| 3 | Smart contract: live chain feed on the site; every tx opens on Monadscan in a second window (it cannot be embedded) | viewer + Kellen |
| 4 | Live demo: the laptop runs the mission with keys and posts on testnet; the hosted site replays as backup | Kellen |
| 5 | Vision: rims from camera frames, replacing the stand-in | Calvin |
| 6 | Faster reruns | perf |
| 7 | UI polish | designer |
| 8 | Hosting: redeploy Kellen's Vercel site from `main` | Kellen |
| 9 | Demo prep: script, backup video, a fresh contract deploy 1 h before | Joseph + Kellen |

## Waiting on Joseph
- Keep a disputed site in the mission (shows why uncertainty matters)?
- Uncertainty formula: the paper's larger-value form (leader recommends)?
