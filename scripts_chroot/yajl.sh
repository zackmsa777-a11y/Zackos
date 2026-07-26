#!/bin/bash
set -e
cd /sources/yajl-2.1.0
rm -rf CMakeCache.txt CMakeFiles
cmake -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_POLICY_DEFAULT_CMP0026=OLD .
make -j17
make install
echo "=== yajl build done ==="
ldd /usr/lib/libyajl.so.2 || find /usr/lib -iname "*yajl*"
