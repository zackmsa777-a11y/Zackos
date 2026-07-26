#!/bin/bash
# git-2.47.1 — version control, built minimal (no curl/tcltk on this system yet)
set -e
export PATH=/usr/bin:/usr/sbin
export MAKEFLAGS=-j17

STAMP=/.stamps/git.done
mkdir -p /.stamps
if [ -f "$STAMP" ]; then
    echo "=== git already built, skipping (stamp found) ==="
    exit 0
fi

cd /sources
rm -rf git-2.47.1
tar xf git-2.47.1.tar.xz
cd git-2.47.1

# GCC 15 defaults to -std=gnu23, which makes C23 keywords/macros
# (unreachable(), thread_local) reserved -- git-2.47.1's own source
# uses both as plain identifiers (reflog.c's unreachable() function,
# builtin/index-pack.c's thread_local struct members), causing hard
# compile errors under the new default. Force the older standard so
# those names stay available as ordinary identifiers.
export CC="gcc -std=gnu17"
export CFLAGS="-std=gnu17 -O2"

# NO_CURL: no libcurl/curl headers on this system yet -> disables git
#          clone/fetch over http(s); local (file://) and future ssh://
#          transports still work fine.
# NO_TCLTK: no tcl/tk built -> skips git-gui/gitk.
# NO_GETTEXT: keep it simple/English-only, avoids gettext runtime dep checks.
# NO_PYTHON: no python-dependent contrib scripts needed.
make configure
./configure --prefix=/usr \
    CC="gcc -std=gnu17" \
    CFLAGS="-std=gnu17 -O2" \
    NO_CURL=1 \
    NO_TCLTK=1 \
    NO_GETTEXT=1 \
    NO_PYTHON=1

make CC="gcc -std=gnu17" CFLAGS="-std=gnu17 -O2" NO_CURL=1 NO_TCLTK=1 NO_GETTEXT=1 NO_PYTHON=1

make CC="gcc -std=gnu17" CFLAGS="-std=gnu17 -O2" NO_CURL=1 NO_TCLTK=1 NO_GETTEXT=1 NO_PYTHON=1 install

echo "=== verifying git ==="
git --version
git config --global user.email "root@zackos.local"
git config --global user.name "ZackOS"
mkdir -p /tmp/git-selftest && cd /tmp/git-selftest
git init -q
echo hello > f.txt
git add f.txt
git commit -q -m "selftest"
git log --oneline
rm -rf /tmp/git-selftest

touch "$STAMP"
echo "=== DONE: git ==="
