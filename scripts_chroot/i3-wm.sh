#!/bin/bash
set -e
cd /sources/i3-4.23
rm -rf build
meson setup build --prefix=/usr -D docs=false -D mans=false
ninja -C build -j17
ninja -C build install
echo "=== i3 build done ==="
ldd /usr/bin/i3 || true
/usr/bin/i3 --version || true
