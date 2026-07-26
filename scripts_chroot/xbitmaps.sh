#!/bin/bash
# xbitmaps-1.1.4 -- data-only package (bitmap headers), needed by xsetroot
set -e
export PATH=/usr/bin:/usr/sbin
export MAKEFLAGS=-j17

STAMP=/.stamps/xbitmaps.done
mkdir -p /.stamps
if [ -f "$STAMP" ]; then
    echo "=== xbitmaps already installed, skipping (stamp found) ==="
    exit 0
fi

cd /sources
rm -rf xbitmaps-1.1.4
tar xf xbitmaps-1.1.4.tar.xz
cd xbitmaps-1.1.4

./configure --prefix=/usr
make
make install

test -f /usr/include/X11/bitmaps/xlogo64 || test -d /usr/include/X11/bitmaps

touch "$STAMP"
echo "=== DONE: xbitmaps ==="
