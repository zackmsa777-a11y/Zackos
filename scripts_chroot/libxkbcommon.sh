#!/bin/bash
set -e
cd /sources/libxkbcommon-1.7.0
rm -rf build
meson setup build --prefix=/usr -D enable-x11=true -D enable-docs=false -D enable-wayland=false
ninja -C build -j17
ninja -C build install
echo "=== libxkbcommon build done ==="
ldd /usr/lib/libxkbcommon.so.0 || true
ldd /usr/lib/libxkbcommon-x11.so.0 || true
