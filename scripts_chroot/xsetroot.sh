#!/bin/bash
# xsetroot-1.1.4 -- small X11 app to set root window background/cursor
# Needed by .xinitrc: `xsetroot -solid "#3465a4"` for the desktop bg color.
set -e
export PATH=/usr/bin:/usr/sbin
export MAKEFLAGS=-j17

STAMP=/.stamps/xsetroot.done
mkdir -p /.stamps
if [ -f "$STAMP" ]; then
    echo "=== xsetroot already built, skipping (stamp found) ==="
    exit 0
fi

cd /sources
rm -rf xsetroot-1.1.4
tar xf xsetroot-1.1.4.tar.xz
cd xsetroot-1.1.4

./configure --prefix=/usr
make
make install

echo "=== verifying xsetroot ==="
xsetroot -version 2>&1 || true
test -x /usr/bin/xsetroot

touch "$STAMP"
echo "=== DONE: xsetroot ==="
