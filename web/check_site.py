"""Check that web/ (or an exported copy) is a complete static site before hosting it.

    python web/check_site.py          # web/ as a host would receive it
    python web/check_site.py DIR      # an exported copy

Lists the folder the way the Vercel CLI uploads it (web/.vercelignore), then
follows every reference the page makes: index.html to its css and js, the js
imports, assets/terrain/meta.json to every terrain file, runs/index.json to
every log and every first-person frame. Sizes are checked against the host
limits. Exit code 1 means do not deploy. Nothing here changes any file.
"""
from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent

# Vercel Hobby plan, https://vercel.com/docs/limits (read 2026-11-15): a CLI
# deployment may upload 100 MB of source and 15,000 files. GitHub refuses any
# single file over 100 MB. These are limits, not targets.
MAX_TOTAL_MB = 100
MAX_FILES = 15_000
MAX_FILE_MB = 100

# What the Vercel CLI skips on its own (docs, "Ignored files and folders").
CLI_DEFAULT_IGNORES = [".git", ".vercel", ".gitignore", ".vercelignore", "__pycache__", ".DS_Store",
                       "node_modules", ".env.local"]


def ignore_patterns(site: Path) -> list[str]:
    pats = list(CLI_DEFAULT_IGNORES)
    vi = site / ".vercelignore"
    if vi.exists():
        pats += [ln.strip() for ln in vi.read_text(encoding="utf8").splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
    return pats


def ignored(rel: str, pats: list[str]) -> bool:
    """gitignore-style, close enough for this flat site: a pattern without a
    slash matches any path component; with a slash it matches the whole path."""
    parts = rel.split("/")
    for p in pats:
        p = p.strip("/")
        if "/" in p:
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*"):
                return True
        elif any(fnmatch.fnmatch(part, p) for part in parts):
            return True
    return False


def site_files(site: Path) -> dict[str, int]:
    """Relative posix path -> size in bytes, for every file a host would get."""
    pats = ignore_patterns(site)
    out: dict[str, int] = {}
    for p in sorted(site.rglob("*")):
        if p.is_file():
            rel = p.relative_to(site).as_posix()
            if not ignored(rel, pats):
                out[rel] = p.stat().st_size
    return out

def frame_keys(site: Path) -> tuple[str, ...]:
    """Run-log keys that name frames and that the site's scripts actually read. fp_frame
    and lidar_frame always; fp_sub (the spine's half-metre sub-frames, [[fraction, path], ...]) only once a
    script mentions it, so unread sub-frames are not uploaded and read ones are never pruned."""
    keys = ["fp_frame", "lidar_frame"]
    for js in (site / "js").glob("*.js"):
        if "fp_sub" in js.read_text(encoding="utf8"):
            keys.append("fp_sub")
            break
    return tuple(keys)


def frames_named(rover: dict, keys) -> list[str]:
    out = []
    for key in ("fp_frame", "lidar_frame"):
        if key in keys and rover.get(key):
            out.append(rover[key])
    if "fp_sub" in keys:
        out += [p for _, p in rover.get("fp_sub") or []]
    return out


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def err(self, m): self.errors.append(m)
    def warn(self, m): self.warnings.append(m)
    def note(self, m): self.notes.append(m)

    def print(self):
        for m in self.notes: print(f"  {m}")
        for m in self.warnings: print(f"  warning: {m}")
        for m in self.errors: print(f"  ERROR: {m}")
        if self.errors:
            print(f"DO NOT DEPLOY: {len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        else:
            print(f"OK to deploy ({len(self.warnings)} warning(s))")


EXTERNAL = ("http://", "https://", "data:", "#", "mailto:")


def check(site: Path) -> Report:
    rep = Report()
    files = site_files(site)
    have = set(files)

    def need(rel: str, why: str) -> bool:
        rel = posixpath.normpath(rel)
        if rel not in have:
            rep.err(f"missing {rel}  ({why})")
        return rel in have

    def no_absolute(ref: str, where: str) -> bool:
        if ref.startswith("/"):
            rep.err(f"{where} uses the absolute path {ref}; the site must work under a sub-path "
                    f"(GitHub Pages serves it at /<repo>/), so paths stay relative")
            return False
        return True

    # Every page at the root, and what each links; then the js imports they pull in.
    if "index.html" not in have:
        rep.err("missing index.html  (the page itself)")
        return rep
    pages = sorted(f for f in have if "/" not in f and f.endswith(".html"))
    js_todo, externals = [], set()
    for page in pages:
        html = (site / page).read_text(encoding="utf8")
        for r in re.findall(r'(?:href|src)="([^"]+)"', html):
            if r.startswith(EXTERNAL) or "${" in r:   # template placeholders in inline scripts
                continue
            if no_absolute(r, page) and need(r, f"linked from {page}") and r.endswith(".js"):
                js_todo.append(posixpath.normpath(r))
        for imp in re.findall(r"""(?:from|import)\s+["'](\.[^"']+)["']""", html):   # inline module scripts
            rel = posixpath.normpath(imp)
            if need(rel, f"imported by {page}"):
                js_todo.append(rel)
        externals.update(re.findall(r'https?://[^"\s<>]+', html))
    rep.note(f"pages: {', '.join(pages)}")

    seen = set()
    while js_todo:
        js = js_todo.pop()
        if js in seen:
            continue
        seen.add(js)
        src = (site / js).read_text(encoding="utf8")
        for imp in re.findall(r"""(?:from|import)\s+["']([^"']+)["']""", src):
            if imp.startswith("."):
                rel = posixpath.normpath(posixpath.join(posixpath.dirname(js), imp))
                if need(rel, f"imported by {js}"):
                    js_todo.append(rel)
            elif imp.startswith(("http://", "https://")):
                externals.add(imp)
            else:
                no_absolute(imp, js)
        for lit in re.findall(r"""["'](/(?:assets|runs|js|chain)/[^"']*)["']""", src):
            no_absolute(lit, js)
    for u in sorted(externals):
        rep.note(f"external (needs internet at demo time): {u}")

    # The live chain page, if present, and the contract it reads.
    chain_contract = None
    if "chain.html" in have:
        files_ok = [need("chain/" + f, "LanternReader.load('chain/') in chain.html")
                    for f in ("lantern_deployment.json", "lantern_abi.json")]
        if all(files_ok):
            dep = json.loads((site / "chain/lantern_deployment.json").read_text(encoding="utf8"))
            chain_contract = dep.get("address")
            rep.note(f"chain page: contract {chain_contract} on chain {dep.get('chainId')}, "
                     f"rpc {dep.get('rpcUrl')}, verified {dep.get('verified') or 'no'}")
            if not dep.get("rpcUrl", "").startswith("https://"):
                rep.err("chain/lantern_deployment.json: rpcUrl must be https for a hosted page")

    # Terrain assets, as js/data.js Terrain.load reads them.
    base = "assets/terrain/"
    if need(base + "meta.json", "Terrain.load('assets/terrain/') in js/main.js"):
        m = json.loads((site / base / "meta.json").read_text(encoding="utf8"))
        wanted = [m["coarse"]["file"], m["craters"], m["map"]["file"]] + [t["file"] for t in m["tiles"]]
        for k in ("backdrop", "backdrop_near", "rocks", "albedo"):
            if k in m:
                wanted.append(m[k]["file"])
        if "detail" in m:
            wanted += [m["detail"]["normal"], m["detail"]["albedo"]]
        if "far_detail" in m:
            wanted.append(m["far_detail"]["normal"])
        present = [w for w in wanted if need(base + w, "listed in assets/terrain/meta.json")]
        raw = [w for w in present if w.endswith(".bin")
               and (site / base / w).open("rb").read(2) != b"\x1f\x8b"]
        if raw:
            rep.note(f"{len(raw)} .bin file(s) are not gzip; the page reads raw bytes too, so this is fine")
        rep.note(f"terrain: {len(m['tiles'])} tiles, {len(present)} files, "
                 f"{sum(files[base + w] for w in present) / 1e6:.1f} MB, source: {m.get('source', '?')}")

    # Runs, as js/data.js loadIndex and Run.load read them.
    keys = frame_keys(site)
    if "fp_sub" in keys:
        rep.note("the scripts read fp_sub: sub-frames are kept and counted")
    if need("runs/index.json", "loadIndex('runs/') in js/main.js"):
        j = json.loads((site / "runs/index.json").read_text(encoding="utf8"))
        runs = j if isinstance(j, list) else j.get("runs", [])
        if not runs:
            rep.err("runs/index.json lists no runs; the page has nothing to replay")
        for r in runs:
            rid = r.get("run_id", "?")
            path = r.get("path") or f"runs/{r.get('dir', rid)}/log.json"
            if not need(path, f"run {rid} in runs/index.json"):
                continue
            log = json.loads((site / path).read_text(encoding="utf8"))
            rdir = posixpath.dirname(path)
            steps = log.get("steps", [])
            frames = {f for s in steps for rv in s.get("rovers", {}).values() for f in frames_named(rv, keys)}
            missing = [f for f in frames if posixpath.join(rdir, f) not in have]
            if missing:
                rep.err(f"{rid}: {len(missing)} of {len(frames)} first-person frames missing, e.g. {rdir}/{missing[0]}")
            if not frames:
                rep.warn(f"{rid}: the log names no first-person frames; the camera panel will show "
                         f"'No frame at this step' (run_demo.py without --no-render fixes that)")
            if not steps:
                rep.err(f"{rid}: no steps")
            if log.get("run_id") != rid:
                rep.warn(f"{rid}: index says run_id {rid!r}, log says {log.get('run_id')!r}; ?run= links use the index")
            events = sum(1 for s in steps for e in s.get("events", []) if e.get("tx"))
            if events == 0:
                rep.warn(f"{rid}: no chain events (no find, no check); the story has no ending")
            chain = log.get("chain") or {}
            si = log.get("standins") or log.get("standin") or {}
            real = bool(chain.get("explorer")) and not (si.get("chain") or si.get("tx"))
            if real and chain_contract and str(chain.get("contract", "")).lower() != chain_contract.lower():
                rep.warn(f"{rid}: posted to {chain.get('contract')}, but chain.html reads {chain_contract}")
            mb = sum(files.get(posixpath.join(rdir, f), 0) for f in frames) / 1e6 + files[path] / 1e6
            rep.note(f"{rid}: {len(steps)} steps, {events} chain events, {len(frames)} frames, {mb:.1f} MB; "
                     f"chain {'real, tx links to ' + chain['explorer'] if real else 'stand-in, hashes shown as fake'}")
            if si:
                rep.note(f"  stand-ins declared in the log: {', '.join(si)}")

    # Files under runs/ that no listed log names: the page never fetches them.
    if "runs/index.json" in have:
        used = {"runs/index.json"} | {f for f in have if f.startswith("runs/") and f.endswith("log.json")}
        for path in [f for f in have if f.startswith("runs/") and f.endswith("log.json")]:
            try:
                lg = json.loads((site / path).read_text(encoding="utf8"))
            except Exception:
                continue
            rdir = posixpath.dirname(path)
            used |= {posixpath.join(rdir, f) for st in lg.get("steps", [])
                     for rv in st.get("rovers", {}).values() for f in frames_named(rv, keys)}
        unused = [f for f in have if f.startswith("runs/") and f not in used]
        if unused:
            mb = sum(files[f] for f in unused) / 1e6
            rep.warn(f"{len(unused)} files under runs/ ({mb:.0f} MB) are not named by any log under {'/'.join(keys)}, "
                     f"e.g. {unused[0]}; `python web/publish.py prune` removes them from web/runs/ (copies; the data folder is untouched)")

    # Size and shape.
    total, n = sum(files.values()), len(files)
    rep.note(f"site as uploaded: {n} files, {total / 1e6:.1f} MB")
    if total > MAX_TOTAL_MB * 1e6:
        # docs.vercel.com/limits (2026-09-16) still lists 100 MB for Hobby CLI uploads; the
        # 2026-06-17 changelog says CLI deployment limits were removed. Try; if the upload is
        # refused, python web/shrink_frames.py, or a larger --frame-every in run_demo.py.
        rep.warn(f"{total / 1e6:.0f} MB is over the {MAX_TOTAL_MB} MB figure in Vercel's Hobby limits; "
                 f"the upload may be refused (see the note in check_site.py)")
    if n > MAX_FILES:
        rep.err(f"{n} files is over the {MAX_FILES} file limit")
    for rel, sz in files.items():
        if sz > MAX_FILE_MB * 1e6:
            rep.err(f"{rel} is {sz / 1e6:.0f} MB; GitHub and Vercel refuse files over {MAX_FILE_MB} MB")
    if (site / "public").is_dir():
        rep.err("a public/ folder exists: Vercel's 'Other' preset would serve it instead of the site")
    stray = [f for f in files if f.endswith((".py", ".pyc", ".npz", ".npy"))]
    if stray:
        rep.warn(f"build files would be uploaded: {', '.join(stray[:5])}; check .vercelignore")
    if "vercel.json" not in have:
        rep.warn("no vercel.json: Vercel will guess the settings (usually right for a static folder)")
    return rep


def main():
    site = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else WEB
    print(f"checking {site}")
    rep = check(site)
    rep.print()
    sys.exit(1 if rep.errors else 0)


if __name__ == "__main__":
    main()
