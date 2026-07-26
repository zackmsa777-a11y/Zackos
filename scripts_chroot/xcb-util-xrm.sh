#!/bin/bash
set -e
cd /sources/xcb-util-xrm-1.3
./configure --prefix=/usr
make -j17
make install
echo "=== xcb-util-xrm build done ==="
ldd /usr/lib/libxcb-xrm.so.0 || true
