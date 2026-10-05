#!/bin/sh
# tools/install_githooks.sh -- switch every worktree of this repo onto the versioned hooks in githooks/.
#
# NOT run automatically by anything in this repo. Run by hand, once, after reviewing githooks/
# pre-commit and githooks/pre-push -- and again after changing either of them.
#
# Copies the shims into <git common dir>/pflege-githooks/ and sets the shared core.hooksPath to that
# ABSOLUTE directory. A relative "githooks" resolved against whichever worktree triggered the hook,
# so a worktree without githooks/ ran no hook at all (Opus review M3); a path into the main checkout's
# githooks/ vanished whenever the main checkout sat on a branch without it (N2). The git common dir is
# no working tree, so no checkout can remove the hooks; the shims themselves fail loudly when the
# main checkout's tools/test_gate.py is missing.
set -eu

src_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../githooks" && pwd)
git_common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
hooks_dir="$git_common_dir/pflege-githooks"
mkdir -p "$hooks_dir"
for hook in pre-commit pre-push; do
    cp "$src_dir/$hook" "$hooks_dir/$hook"
    chmod 755 "$hooks_dir/$hook"
done
git config core.hooksPath "$hooks_dir"
echo "core.hooksPath set to $hooks_dir (shims copied from $src_dir)"
