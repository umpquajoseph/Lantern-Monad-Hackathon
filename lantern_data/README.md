# lantern_data

Built data, so you can run Lantern without downloading or building
anything. `data/` is git-ignored; this folder is the shared copy.

Point the code at it before running anything:

```bash
export LANTERN_DATA="$(pwd)/lantern_data"
```

PowerShell: `$env:LANTERN_DATA = "$PWD\lantern_data"`

| Path | What | Real or made up |
|---|---|---|
| `terrain/shoemaker.npz` | Default terrain: 1 km at 0.25 m, LOLA window near Shoemaker (x 73250-74250, y 36000-37000, south polar stereographic m), plus 11,082 synthetic small craters and their list | Ground shape real (LOLA), small craters made up |
| `terrain/synthetic.npz` | Fallback terrain, same format | Made up |
| `terrain/display/` | Display-only realism: detail texture, albedo, rocks, rim lips, Hapke parameters, real LOLA backdrop. `display.json` describes it | Backdrops real (LOLA), the rest made up |

Load with `sim/terrain.py`: `load_terrain()`. Formats are in
`docs/handoffs.md`. Sources: Barker et al. 2021 (LOLA, NASA PGDA product 78);
display parameters cite their sources in `display.json`.
