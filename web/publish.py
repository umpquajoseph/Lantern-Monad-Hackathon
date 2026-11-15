"""Put the web viewer on a host. The site is web/ as it is on disk.

    python web/publish.py check                # is web/ complete? (web/check_site.py)
    python web/publish.py export DIR           # copy the site as a host receives it
    python web/publish.py vercel               # upload to Vercel, production
    python web/publish.py vercel --preview     # upload as a preview deployment (own URL)
    python web/publish.py branch               # commit the site to the `deploy` branch and push;
                                               # Vercel's GitHub integration and GitHub Pages serve it

Every command runs the check first and stops on errors. The one-time setup
for each route is in docs/hosting.md. Needs Node 20+ for the vercel route
(npx fetches the CLI) and a pushed-to remote for the branch route.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(WEB))
from check_site import check, site_files  # noqa: E402

DEPLOY_BRANCH = "deploy"     # orphan branch holding only the built site
PROJECT = "lantern"          # Vercel project name -> lantern-<hash>.vercel.app until a domain is set


def say(msg):
    print(f"[publish] {msg}", flush=True)


def preflight(site: Path):
    print(f"checking {site}")
    rep = check(site)
    rep.print()
    if rep.errors:
        sys.exit("[publish] fix the errors above, then run again")


def copy_site(site: Path, dest: Path) -> tuple[int, float]:
    files = site_files(site)
    for rel in files:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(site / rel, dest / rel)
    return len(files), sum(files.values()) / 1e6


def export(site: Path, dest: Path):
    preflight(site)
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"[publish] {dest} is not empty; give an empty or new folder")
    dest.mkdir(parents=True, exist_ok=True)
    n, mb = copy_site(site, dest)
    say(f"exported {n} files, {mb:.0f} MB -> {dest}  (upload this folder as it is)")


def npx() -> str:
    p = shutil.which("npx") or shutil.which("npx.cmd")
    if not p:
        sys.exit("[publish] npx not found: install Node.js 20 or newer from https://nodejs.org, then run again")
    return p


def vercel(site: Path, prod: bool, project: str):
    preflight(site)
    base = [npx(), "-y", "vercel@latest"]
    who = subprocess.run(base + ["whoami"], cwd=site, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if who.returncode:
        sys.exit("[publish] not logged in to Vercel. Run `npx vercel login` once (it opens a browser), or set "
                 "VERCEL_TOKEN, then run this again. docs/hosting.md has the steps.")
    say(f"Vercel account: {who.stdout.strip()}")
    if not (site / ".vercel" / "project.json").exists():
        say(f"first time here: linking {site.name}/ to the Vercel project '{project}'")
        if subprocess.run(base + ["link", "--yes", "--project", project], cwd=site).returncode:
            say(f"no project '{project}' in this account yet; creating it")
            if subprocess.run(base + ["project", "create", project], cwd=site).returncode:
                sys.exit("[publish] could not create the project; see above")
            if subprocess.run(base + ["link", "--yes", "--project", project], cwd=site).returncode:
                sys.exit("[publish] could not link; see above")
    args = base + ["deploy", "--yes", "--archive=tgz"] + (["--prod"] if prod else [])
    say(" ".join(args[3:]) + f"   (in {site})")
    r = subprocess.run(args, cwd=site, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.exit(f"[publish] vercel deploy failed (exit {r.returncode}); see above")
    # The CLI prints the deployment URL and, for production, the stable alias.
    urls = re.findall(r"https://[\w.-]+\.vercel\.app", r.stdout)
    alias = re.search(r"Aliased\s+(https://\S+)", r.stdout)
    url = alias.group(1) if alias else (urls[-1] if urls else "(see above)")
    say(f"{'PRODUCTION' if prod else 'preview'}: {url}")
    say("open it, watch the console for 404s, click one tx hash, then paste the URL in docs/WORKING.md")


def git(*args, cwd=ROOT, check=True, quiet=False):
    r = subprocess.run(["git", *args], cwd=cwd, text=True,
                       stdout=subprocess.PIPE if quiet else None, stderr=subprocess.PIPE if quiet else None)
    if check and r.returncode:
        sys.exit(f"[publish] git {' '.join(args)} failed" + (f": {r.stderr.strip()}" if quiet else ""))
    return (r.stdout or "").strip()


def branch(site: Path, remote: str, name: str):
    preflight(site)
    src = git("rev-parse", "--abbrev-ref", "HEAD", quiet=True)
    sha = git("rev-parse", "--short", "HEAD", quiet=True)
    git("fetch", remote, name, check=False, quiet=True)
    exists = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{remote}/{name}"],
                            cwd=ROOT, stdout=subprocess.DEVNULL).returncode == 0
    tmp = Path(tempfile.mkdtemp(prefix="lantern-site-"))
    tmp.rmdir()   # git worktree add wants to create the folder
    try:
        if exists:
            git("worktree", "add", "-B", name, str(tmp), f"{remote}/{name}", quiet=True)
        else:
            say(f"no {remote}/{name} yet: making an orphan branch with only the site in it")
            git("worktree", "add", "--detach", str(tmp), quiet=True)
            git("checkout", "--orphan", name, cwd=tmp, quiet=True)
            git("rm", "-rfq", ".", cwd=tmp, check=False, quiet=True)
        for p in tmp.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        n, mb = copy_site(site, tmp)
        (tmp / ".nojekyll").write_text("")   # GitHub Pages: serve files as they are
        (tmp / "README.md").write_text(
            f"# Lantern demo site\n\nBuilt files only. Written by `python web/publish.py branch` from "
            f"`{src}` at {sha}; edit the source there, never here.\n", encoding="utf8")
        git("add", "-A", cwd=tmp, quiet=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp).returncode == 0:
            say("nothing changed since the last publish")
            return
        git("commit", "-q", "-m", f"Site from {src}@{sha}: {n} files, {mb:.0f} MB", cwd=tmp, quiet=True)
        git("push", "-u", remote, f"{name}:{name}", cwd=tmp)
        say(f"pushed {n} files, {mb:.0f} MB to {remote}/{name}")
        say("Vercel (GitHub integration, production branch `deploy`) and GitHub Pages pick it up in a minute or two")
    finally:
        git("worktree", "remove", "--force", str(tmp), check=False, quiet=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", type=Path, default=WEB, help="site folder (default web/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    e = sub.add_parser("export"); e.add_argument("dest", type=Path)
    v = sub.add_parser("vercel")
    v.add_argument("--preview", action="store_true", help="a preview deployment instead of production")
    v.add_argument("--project", default=PROJECT)
    b = sub.add_parser("branch")
    b.add_argument("--remote", default="origin")
    b.add_argument("--branch", default=DEPLOY_BRANCH)
    a = ap.parse_args()
    site = a.site.resolve()
    if a.cmd == "check":
        preflight(site); say("ready")
    elif a.cmd == "export":
        export(site, a.dest.resolve())
    elif a.cmd == "vercel":
        vercel(site, not a.preview, a.project)
    elif a.cmd == "branch":
        branch(site, a.remote, a.branch)


if __name__ == "__main__":
    main()
