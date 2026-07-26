#!/bin/bash
set -e
cd /sources/libXaw-1.0.16
./configure --prefix=/usr
make -j17
make install
echo "=== libXaw build done ==="
ldd /usr/lib/libXaw7.so.7 || find /usr/lib -iname "libXaw*"
