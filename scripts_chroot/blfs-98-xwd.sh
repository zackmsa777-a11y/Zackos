set -e
cd /sources
rm -rf xwd-1.0.9
tar xf xwd-1.0.9.tar.xz
cd xwd-1.0.9
./configure --prefix=/usr
make -j17
make install
test -f /usr/bin/xwd
cd /sources
rm -rf xwd-1.0.9
