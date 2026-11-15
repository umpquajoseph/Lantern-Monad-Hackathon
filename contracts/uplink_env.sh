#!/usr/bin/env bash
# Push the site uplink's secrets from the local .env into the Vercel project,
# so web/api/chain.js can sign postFind (agency A) and checkFind (agency B)
# during every play-through. Run from the repo root, once per Vercel project,
# and again after rotating a key. Values are never printed.
#
#   bash contracts/uplink_env.sh            # production and preview
#
# Needs: `npx vercel login` done once, and web/.vercel/project.json (from
# `npx vercel link` in web/). Then redeploy so the functions pick the values up:
#   cd web && npx vercel redeploy "$(npx vercel ls --prod 2>/dev/null | awk '/vercel.app/{print $2; exit}')"
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing (see .env.example)"; exit 1; }
getv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r\n'; }
for name in AGENCY_A_PRIVATE_KEY AGENCY_B_PRIVATE_KEY DEMO_KEY; do
  v="$(getv "$name")"
  [ -n "$v" ] || { echo "$name is empty in .env"; exit 1; }
  for env in production preview; do
    flag=""; [ "$name" != DEMO_KEY ] && flag="--sensitive"
    printf '%s' "$v" | (cd web && npx --no-install vercel env add "$name" "$env" $flag --force >/dev/null 2>&1) \
      && echo "set $name ($env)" || { echo "failed: $name ($env)"; exit 1; }
  done
done
echo "done. now redeploy (see the comment at the top) and check:"
echo "  curl -s https://lantern-teal.vercel.app/api/chain   # signers A,B true; keyed true"
