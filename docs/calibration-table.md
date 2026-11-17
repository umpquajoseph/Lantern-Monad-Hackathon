# Lantern camera navigation beside ShadowNav Table 2 (C23)

Final error and reported uncertainty (sqrt of the largest covariance eigenvalue, C23 sec. 6B) in metres, mean ± s.d. over seeds. > 5 m: share of runs ending more than 5 m off. False match (draft sec. 4.2: rims 'paired with the wrong mapped crater'): of rim points paired with a mapped crater, the share whose crater is not the rim's true one (a spurious rim that is paired counts as wrong); unpaired points are left out of both and reported apart. Two pairings: **filter**, the particle filter's own (Alg. 1 line 4, every point to its nearest mapped edge at the filter's mean pose before the update, so none is unpaired); **1 m**, the campaign labeller's rule (paired only when that edge is within 1 m).

- **600 none**: batch `realism-filter`, config `no-filter`, terrain `s89_134e.npz` (11082 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-filter --reuse --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **600 C23 8 m**: batch `realism-filter`, config `filter`, terrain `s89_134e.npz` (11082 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-filter --reuse --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **600 head-on 5.53 m**: batch `realism-filter`, config `filter-measured`, terrain `s89_134e.npz` (11082 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-filter --reuse --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **336 none**: batch `realism-eq336`, config `no-filter`, terrain `s89_134e_eq336.npz` (6185 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-eq336 --reuse --terrain C:/Monad-Hackathon-Project/data/terrain/s89_134e_eq336.npz --display-dir C:/Monad-Hackathon-Project/data/terrain/display_s89_134e_eq336 --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **336 C23 8 m**: batch `realism-eq336`, config `filter`, terrain `s89_134e_eq336.npz` (6185 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-eq336 --reuse --terrain C:/Monad-Hackathon-Project/data/terrain/s89_134e_eq336.npz --display-dir C:/Monad-Hackathon-Project/data/terrain/display_s89_134e_eq336 --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **336 head-on 5.53 m**: batch `realism-eq336`, config `filter-measured`, terrain `s89_134e_eq336.npz` (6185 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-eq336 --reuse --terrain C:/Monad-Hackathon-Project/data/terrain/s89_134e_eq336.npz --display-dir C:/Monad-Hackathon-Project/data/terrain/display_s89_134e_eq336 --seeds 100 --workers 4 --configs no-filter,filter,filter-measured`.
- **336 head-on 5.53 m, noisy depth**: batch `realism-eq336-nd`, config `filter-measured-nd`, terrain `s89_134e_eq336.npz` (6185 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-eq336-nd --reuse --terrain C:/Monad-Hackathon-Project/data/terrain/s89_134e_eq336.npz --display-dir C:/Monad-Hackathon-Project/data/terrain/display_s89_134e_eq336 --seeds 100 --workers 4 --configs filter-measured,filter-measured-nd,filter-route-nd`.
- **336 route 4.76 m, noisy depth**: batch `realism-eq336-nd`, config `filter-route-nd`, terrain `s89_134e_eq336.npz` (6185 craters), 100 seeds per traverse. Made by `python -m sim.calibrate --batch realism-eq336-nd --reuse --terrain C:/Monad-Hackathon-Project/data/terrain/s89_134e_eq336.npz --display-dir C:/Monad-Hackathon-Project/data/terrain/display_s89_134e_eq336 --seeds 100 --workers 4 --configs filter-measured,filter-measured-nd,filter-route-nd`.

| Traverse | C23 error | C23 uncertainty | 600 none error | 600 none uncertainty | 600 none false match, filter | 600 none false match, 1 m (unpaired) | 600 none > 5 m | 600 C23 8 m error | 600 C23 8 m uncertainty | 600 C23 8 m false match, filter | 600 C23 8 m false match, 1 m (unpaired) | 600 C23 8 m > 5 m | 600 head-on 5.53 m error | 600 head-on 5.53 m uncertainty | 600 head-on 5.53 m false match, filter | 600 head-on 5.53 m false match, 1 m (unpaired) | 600 head-on 5.53 m > 5 m | 336 none error | 336 none uncertainty | 336 none false match, filter | 336 none false match, 1 m (unpaired) | 336 none > 5 m | 336 C23 8 m error | 336 C23 8 m uncertainty | 336 C23 8 m false match, filter | 336 C23 8 m false match, 1 m (unpaired) | 336 C23 8 m > 5 m | 336 head-on 5.53 m error | 336 head-on 5.53 m uncertainty | 336 head-on 5.53 m false match, filter | 336 head-on 5.53 m false match, 1 m (unpaired) | 336 head-on 5.53 m > 5 m | 336 head-on 5.53 m, noisy depth error | 336 head-on 5.53 m, noisy depth uncertainty | 336 head-on 5.53 m, noisy depth false match, filter | 336 head-on 5.53 m, noisy depth false match, 1 m (unpaired) | 336 head-on 5.53 m, noisy depth > 5 m | 336 route 4.76 m, noisy depth error | 336 route 4.76 m, noisy depth uncertainty | 336 route 4.76 m, noisy depth false match, filter | 336 route 4.76 m, noisy depth false match, 1 m (unpaired) | 336 route 4.76 m, noisy depth > 5 m |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 gap, unmapped craters | 3.84 ± 2.78 | 1.84 ± 1.12 | 17.88 ± 11.03 | 1.89 ± 1.07 | 95% | 76% (98%) | 91% | 14.67 ± 5.33 | 12.62 ± 1.46 | nan% | nan% (nan%) | 97% | 15.93 ± 9.21 | 9.99 ± 3.57 | 37% | 2% (81%) | 92% | 19.86 ± 10.85 | 1.52 ± 0.92 | 97% | 85% (97%) | 99% | 13.25 ± 5.77 | 11.81 ± 1.82 | 64% | 47% (94%) | 96% | 9.84 ± 7.50 | 9.65 ± 3.93 | 58% | 48% (88%) | 66% | 11.49 ± 6.81 | 10.80 ± 3.13 | 56% | 45% (91%) | 82% | 10.89 ± 7.15 | 10.43 ± 3.70 | 56% | 46% (90%) | 77% |
| T2 straight past craters | 1.75 ± 0.78 | 1.32 ± 0.76 | 15.84 ± 10.19 | 1.62 ± 1.21 | 92% | 70% (95%) | 95% | 7.81 ± 6.38 | 7.95 ± 2.78 | 71% | 68% (81%) | 64% | 7.25 ± 6.43 | 6.95 ± 3.24 | 35% | 5% (74%) | 62% | 16.23 ± 11.06 | 1.59 ± 1.18 | 90% | 53% (98%) | 87% | 13.57 ± 5.02 | 11.43 ± 1.28 | nan% | nan% (nan%) | 97% | 10.71 ± 7.14 | 9.62 ± 3.04 | 18% | 0% (89%) | 76% | 10.48 ± 7.88 | 8.74 ± 3.70 | 15% | 1% (84%) | 73% | 8.47 ± 7.14 | 5.95 ± 3.44 | 11% | 1% (78%) | 59% |
| T3 half survey | 1.68 ± 0.70 | 1.39 ± 0.61 | 8.05 ± 5.15 | 0.98 ± 0.50 | 84% | 41% (96%) | 68% | 3.33 ± 2.34 | 2.11 ± 1.44 | 10% | 0% (71%) | 22% | 3.74 ± 2.93 | 1.46 ± 1.07 | 13% | 0% (66%) | 25% | 7.37 ± 4.58 | 0.79 ± 0.50 | 81% | 31% (96%) | 65% | 4.33 ± 3.61 | 2.14 ± 1.04 | 11% | 0% (74%) | 24% | 3.41 ± 2.45 | 1.33 ± 0.87 | 14% | 2% (70%) | 15% | 3.29 ± 1.75 | 1.19 ± 0.85 | 14% | 2% (69%) | 13% | 3.34 ± 1.82 | 1.12 ± 0.66 | 17% | 2% (70%) | 15% |

False-match breakdown (share of rim points: right / wrong mapped crater / crater not in the map / spurious edge), and rim points per run:

- 600 none, T1: 5 / 1 / 61 / 33%, 21495 points
- 600 none, T2: 8 / 1 / 50 / 40%, 20197 points
- 600 none, T3: 16 / 0 / 53 / 31%, 11441 points
- 600 C23 8 m, T1: nan / nan / nan / nan%, 0 points
- 600 C23 8 m, T2: 29 / 0 / 4 / 67%, 26 points
- 600 C23 8 m, T3: 90 / 0 / 9 / 1%, 641 points
- 600 head-on 5.53 m, T1: 63 / 2 / 34 / 0%, 329 points
- 600 head-on 5.53 m, T2: 65 / 0 / 25 / 10%, 342 points
- 600 head-on 5.53 m, T3: 87 / 0 / 12 / 0%, 1239 points
- 336 none, T1: 3 / 1 / 40 / 55%, 20712 points
- 336 none, T2: 10 / 1 / 35 / 55%, 17007 points
- 336 none, T3: 19 / 0 / 36 / 44%, 11724 points
- 336 C23 8 m, T1: 36 / 26 / 6 / 32%, 80 points
- 336 C23 8 m, T2: nan / nan / nan / nan%, 0 points
- 336 C23 8 m, T3: 89 / 0 / 11 / 0%, 640 points
- 336 head-on 5.53 m, T1: 42 / 21 / 6 / 31%, 320 points
- 336 head-on 5.53 m, T2: 82 / 7 / 11 / 0%, 160 points
- 336 head-on 5.53 m, T3: 86 / 0 / 11 / 3%, 1760 points
- 336 head-on 5.53 m, noisy depth, T1: 44 / 26 / 7 / 23%, 184 points
- 336 head-on 5.53 m, noisy depth, T2: 85 / 5 / 10 / 0%, 251 points
- 336 head-on 5.53 m, noisy depth, T3: 86 / 0 / 11 / 3%, 1798 points
- 336 route 4.76 m, noisy depth, T1: 44 / 25 / 7 / 25%, 230 points
- 336 route 4.76 m, noisy depth, T2: 89 / 0 / 11 / 0%, 560 points
- 336 route 4.76 m, noisy depth, T3: 83 / 0 / 11 / 6%, 1998 points
