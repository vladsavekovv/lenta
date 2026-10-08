#!/usr/bin/env bash
# Publish a new LENTA package to your GitHub copy in one step.
#
#   cd ~/github/lenta
#   ./tools/github-update.sh ~/lenta.tar.gz "What changed in this version"
#
# It unpacks the package over this folder (files that were removed from LENTA are removed here too),
# puts your GitHub name into the docs' "git clone" lines, shows what changed, commits and pushes.
#
# Edits you make on github.com are never lost: each package says which GitHub version it was built on
# (.github-base). If GitHub has newer changes than that, the script stops without uploading anything.
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
cd "$REPO"

# never overwrite changes made on GitHub that this package doesn't contain
BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
if git fetch -q origin "$BRANCH" 2>/dev/null; then
  REMOTE="$(git rev-parse FETCH_HEAD)"
  BASE="$(cat "$TMP/lenta/.github-base" 2>/dev/null || true)"
  if [[ -n "$BASE" ]]; then
    git cat-file -e "$BASE^{commit}" 2>/dev/null && git merge-base --is-ancestor "$REMOTE" "$BASE" 2>/dev/null || {
      echo "STOPPED, nothing uploaded: GitHub has changes that this package doesn't include"
      echo "(it was built on GitHub version ${BASE:0:7}; GitHub is now at ${REMOTE:0:7})."
      echo "Your GitHub edits are safe. Ask Claude for a new package built on the latest GitHub version."
      exit 1; }
  elif ! git merge-base --is-ancestor "$REMOTE" HEAD 2>/dev/null; then
    echo "STOPPED, nothing uploaded: GitHub has changes this folder doesn't have, and this package"
    echo "doesn't say which GitHub version it was built on. Ask Claude for a new package."
    exit 1
  fi
  git reset -q "$REMOTE"            # carry on from GitHub's latest version
fi

rsync -a --delete --exclude '.git/' --exclude '*.pem' --exclude 'node_modules/' --exclude '.github-base' "$TMP/lenta/" "$REPO/"

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
