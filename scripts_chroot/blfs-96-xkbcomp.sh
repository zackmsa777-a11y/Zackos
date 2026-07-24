set -e
cd /sources
rm -rf xkbcomp-1.5.0
tar xf xkbcomp-1.5.0.tar.xz
cd xkbcomp-1.5.0
./configure --prefix=/usr
make -j17
make install
test -f /usr/bin/xkbcomp
cd /sources
rm -rf xkbcomp-1.5.0
