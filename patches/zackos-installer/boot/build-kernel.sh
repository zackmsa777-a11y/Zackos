#!/usr/bin/env bash
# Build the kernel, configuration and module tree as one installable bundle.
set -euo pipefail
out=$(realpath -m "${1:?provide the path of a new kernel tar.gz}")
test ! -e "$out"
src=$(cd "$(dirname "$0")/../.." && pwd)
version=6.16.1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
curl -fLsS --retry 3 "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$version.tar.xz" -o linux.tar.xz
echo 'ea43491bc7ace1e414b3b2d957f8cf96e7049155123f0acce798accf8da1acba  linux.tar.xz' | sha256sum -c -
tar xf linux.tar.xz
cd "linux-$version"
cp "$src/kernel-configs/config-$version-lfs-12.4-bochs-drm.config" .config
make olddefconfig
for symbol in DRM_VIRTIO_GPU DRM_VMWGFX DRM_VBOXVIDEO DRM_BOCHS EFI_STUB EFI_PARTITION VFAT_FS SQUASHFS OVERLAY_FS BLK_DEV_LOOP ISO9660_FS; do
    grep -qx "CONFIG_$symbol=y" .config || { echo "Missing built-in $symbol" >&2; exit 1; }
done
make -j"${JOBS:-$(nproc)}" bzImage modules
release=$(make -s kernelrelease)
bundle="$work/bundle"
mkdir -p "$bundle/boot" "$bundle/usr/lib"
ln -s usr/lib "$bundle/lib"
make INSTALL_MOD_PATH="$bundle" INSTALL_MOD_STRIP=1 modules_install
rm -f "$bundle/usr/lib/modules/$release/build" "$bundle/usr/lib/modules/$release/source"
install -m644 arch/x86/boot/bzImage "$bundle/boot/vmlinuz-$release-lfs-12.4"
install -m644 .config "$bundle/boot/config-$release-lfs-12.4"
install -m644 System.map "$bundle/boot/System.map-$release"
tar -C "$bundle" -czf "$work/kernel.tar.gz" boot usr
mv "$work/kernel.tar.gz" "$out"
