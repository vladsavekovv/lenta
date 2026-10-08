#!/usr/bin/env bash
# Publish a new LENTA package to your GitHub copy in one step.
#
#   cd ~/github/lenta
#   ./tools/github-update.sh ~/lenta.tar.gz "What changed in this version"
#
# It unpacks the package over this folder (files that were removed from LENTA are removed here too),
# puts your GitHub name into the docs' "git clone" lines, shows what changed, commits and pushes.
# Key files (*.pem, *.key, ...) are never published: .gitignore skips them and this script double-checks.
set -euo pipefail

PKG="${1:-}"; MSG="${2:-Update LENTA}"
[[ -f "$PKG" ]] || { echo "Usage: $0 /path/to/lenta.tar.gz \"What changed\""; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[[ -d "$REPO/.git" ]] || { echo "$REPO is not a git folder. Do the one-time setup first (git init, gh repo create)."; exit 1; }
command -v rsync >/dev/null || { echo "Needs rsync:  sudo apt install rsync"; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
tar xzf "$PKG" -C "$TMP"
[[ -d "$TMP/lenta/server" ]] || { echo "$PKG doesn't look like a LENTA package."; exit 1; }
rsync -a --delete --exclude '.git/' --exclude '*.pem' --exclude 'node_modules/' "$TMP/lenta/" "$REPO/"
cd "$REPO"

# your GitHub user name instead of the placeholder in the docs (this script itself is left alone)
PH="YOUR-""USERNAME"
if command -v gh >/dev/null && GHUSER="$(gh api user -q .login 2>/dev/null)" && [[ -n "$GHUSER" ]]; then
  grep -rlI --exclude-dir=.git --exclude-dir=node_modules --exclude=github-update.sh "$PH" . | xargs -r sed -i "s/$PH/$GHUSER/g" || true
fi

git add -A
if git diff --cached --quiet; then echo "Nothing changed since the last upload."; exit 0; fi
if git diff --cached --name-only | grep -E '\.(pem|key|keystore|jks|p12)$|(^|/)\.env$'; then
  git reset -q; echo "Stopped: the files above look like keys or passwords and must not be published."; exit 1
fi
echo "Changes:"; git status --short | head -50
git commit -q -m "$MSG"
git push -u origin HEAD
echo "Done: $(git remote get-url origin | sed 's/\.git$//')"
