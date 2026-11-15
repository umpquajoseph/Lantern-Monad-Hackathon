# Lantern: to do after the checkpoint merge

Updated 13:05. The checkpoint merge is in progress: `integrator` is putting
every branch on `checkpoint`. It goes into `main` on Joseph's word.

## The demo (decided)
Ritvik's Surveyor and Prospector run an 8-site mission. They use our
renderer, Calvin's navigation and our site, with NASA rover models. Finds
post to Kellen's Lantern contract on Monad testnet.

## To do
| # | Item | Who |
|---|---|---|
| 1 | One sim: the 8-site mission writes our run log, with our renderer and ParticleNav | scene + Ritvik |
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
