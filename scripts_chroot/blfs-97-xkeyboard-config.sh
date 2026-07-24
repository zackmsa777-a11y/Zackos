set -e
cd /sources
rm -rf xkeyboard-config-2.48
tar xf xkeyboard-config-2.48.tar.xz
cd xkeyboard-config-2.48
mkdir -p build && cd build
meson setup --prefix=/usr --buildtype=release ..
ninja -j17
ninja install
test -d /usr/share/X11/xkb/rules
cd /sources
rm -rf xkeyboard-config-2.48
