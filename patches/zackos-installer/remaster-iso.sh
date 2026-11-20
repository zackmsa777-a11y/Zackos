#!/usr/bin/env bash
# Refresh the installer on the verified v5.7 BIOS image, preserving its kernel and boot catalog.
set -euo pipefail
base=$(realpath "${1:?provide the v5.7 installer ISO}")
out=$(realpath -m "${2:?provide a new output ISO path}")
test ! -e "$out"
src=$(cd "$(dirname "$0")" && pwd)
echo "414a7c4ddaaa10c95444c48534468d8250dea761718b7b88089595180de70c61  $base" | sha256sum -c -
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
xorriso -osirrox on -indev "$base" -extract /boot/initramfs.img initrd
chmod u+w initrd
mkdir -p overlay/zackinstaller/providers
cp "$src"/{core.py,zackinstall.py,zackinstall} overlay/zackinstaller/
cp "$src"/providers/*.py overlay/zackinstaller/providers/
cp "$src/../live-boot/zack-live-init.sh" overlay/init
chmod 755 overlay/init
(cd overlay && find . -print0 | cpio --null --quiet -o -H newc --owner=0:0 | gzip -n) >> initrd
xorriso -indev "$base" -outdev "$work/output.iso" -boot_image any replay -map initrd /boot/initramfs.img -commit
mv "$work/output.iso" "$out"
