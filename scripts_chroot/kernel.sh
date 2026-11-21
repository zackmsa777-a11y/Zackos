set -e
mkdir -pv /boot
cd /sources
tar xf linux-6.16.1.tar.xz
cd linux-6.16.1

make mrproper
cp /patches/kernel-configs/config-6.16.1-lfs-12.4-bochs-drm.config .config

make olddefconfig
make
make modules_install

cp -v arch/x86/boot/bzImage /boot/vmlinuz-6.16.1-lfs-12.4
cp -v System.map /boot/System.map-6.16.1
cp -v .config /boot/config-6.16.1-lfs-12.4
