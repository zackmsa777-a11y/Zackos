#!/bin/bash
set -e
cd /sources/startup-notification-0.12
./configure --prefix=/usr
make -j17
make install
echo "=== startup-notification build done ==="
ldd /usr/lib/libstartup-notification-1.so.0 || true
