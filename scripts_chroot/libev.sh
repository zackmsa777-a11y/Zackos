#!/bin/bash
set -e
cd /sources/libev-4.33
./configure --prefix=/usr
make -j17
make install
echo "=== libev build done ==="
ldd /usr/lib/libev.so.4 || true
