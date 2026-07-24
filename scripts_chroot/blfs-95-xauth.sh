set -e
cd /sources
rm -rf xauth-1.1.5
tar xf xauth-1.1.5.tar.xz
cd xauth-1.1.5
./configure --prefix=/usr
make -j17
make install
test -f /usr/bin/xauth
cd /sources
rm -rf xauth-1.1.5
