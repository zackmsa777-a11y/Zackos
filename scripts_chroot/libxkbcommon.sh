set -e
cd /sources
rm -rf libxkbcommon-1.7.0
tar xf libxkbcommon-1.7.0.tar.xz
cd libxkbcommon-1.7.0
mkdir -p build
meson setup build \
  --prefix=/usr \
  --buildtype=release \
  -Denable-x11=true \
  -Denable-docs=false \
  -Denable-wayland=false
ninja -C build
ninja -C build install
cd /sources
rm -rf libxkbcommon-1.7.0
