#!/bin/bash
set -e
cd /sources/xterm-390
./configure --prefix=/usr
make -j17
make install
echo "=== xterm build done ==="
ldd /usr/bin/xterm || true
/usr/bin/xterm -version || true
echo "=== installing terminfo ==="
cd /sources/xterm-390
make install-ti 2>&1 | tail -20
