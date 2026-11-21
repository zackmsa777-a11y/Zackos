#!/usr/bin/env bash
# Build BIOS and UEFI media from a verified ZackOS base and one kernel bundle.
set -euo pipefail
base=$(realpath "${1:?provide the v5.7 or v5.8 installer ISO}")
out=$(realpath -m "${2:?provide a new output ISO path}")
test ! -e "$out"
src=$(cd "$(dirname "$0")" && pwd)
sum=$(sha256sum "$base" | cut -d' ' -f1)
case $sum in
    414a7c4ddaaa10c95444c48534468d8250dea761718b7b88089595180de70c61) version=5.7 ;;
    c5983503f08831e319312c1e07ff422d17fdbdd6280d3996e35adfffa2f5865f) version=5.8 ;;
    *) echo "Unrecognized ZackOS base ISO checksum" >&2; exit 1 ;;
esac
if (( EUID != 0 )); then echo "Run as root to preserve the base filesystem ownership and device nodes." >&2; exit 1; fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
layout="$work/iso"
root="$work/root"
mkdir -p "$layout/boot/grub" "$work/initrd"
desktop=${3:-}
if [[ $desktop != --console-only ]]; then
    if [[ -n $desktop ]]; then
        cp --reflink=auto "$desktop" "$layout/desktop.img"
    elif [[ $version == 5.8 ]]; then
        xorriso -osirrox on -indev "$base" -extract /desktop.img "$layout/desktop.img"
    else
        "$src/live/build-desktop.sh" "$layout/desktop.img"
    fi
fi
kernel=${ZACKOS_KERNEL_BUNDLE:-}
if [[ -z $kernel ]]; then
    kernel="$work/kernel.tar.gz"
    "$src/boot/build-kernel.sh" "$kernel"
fi
xorriso -osirrox on -indev "$base" -extract /squash.img "$work/base.img" -extract /boot/initramfs.img "$work/old-init.gz"
unsquashfs -no-progress -d "$root" "$work/base.img"
rm "$work/base.img"
rm -f "$root"/{build.log,build_ch8.log,ch5_output.log,ch6_output.log,ch8_output.log,cmake_build.log}
rm -f "$root"/boot/{vmlinuz-*,config-*,System.map-*}
rm -rf "$root/usr/lib/modules"
tar --no-same-owner -xzf "$kernel" -C "$root"
kernels=("$root"/boot/vmlinuz-*)
[[ ${#kernels[@]} == 1 && -s ${kernels[0]} ]]
cp "${kernels[0]}" "$layout/boot/vmlinuz"
"$src/boot/build-support.sh" "$root"
# Read the original busybox archive; earlier remasters appended installer overlays.
gzip -dc "$work/old-init.gz" > "$work/old-init.cpio"
(cd "$work/initrd" && cpio -idm --quiet --no-absolute-filenames < "$work/old-init.cpio")
rm -rf "$work/initrd/zackinstaller"
"$src/package-installer.sh" "$work/initrd/zackinstaller"
install -m755 "$src/../live-boot/zack-live-init.sh" "$work/initrd/init"
rm -rf "$root/usr/local/lib/zackos-installer"
mkdir -p "$root/usr/local/lib"
cp -a "$work/initrd/zackinstaller" "$root/usr/local/lib/zackos-installer"
install -m755 "$src/zackinstall" "$root/usr/local/bin/zackinstall"
(cd "$work/initrd" && find . -print0 | cpio --null --quiet -o -H newc --owner=0:0 | gzip -n > "$layout/boot/initramfs.img")
mksquashfs "$root" "$layout/squash.img" -noappend -no-progress -comp zstd -Xcompression-level 12 -b 256K
# Compare the actual boot images after packing, including the kernel inside SquashFS.
unsquashfs -cat "$layout/squash.img" "boot/$(basename "${kernels[0]}")" | cmp - "$layout/boot/vmlinuz"
cp "$src/branding/live.cfg" "$layout/boot/grub/grub.cfg"
cp "$src/branding/splash.cfg" "$layout/boot/grub/"
cp -R "$src/branding/grub" "$layout/boot/grub/zackos"
grub-mkrescue -o "$work/output.iso" "$layout"
catalog=$(xorriso -indev "$work/output.iso" -report_el_torito plain 2>/dev/null)
grep -q ' BIOS ' <<< "$catalog"
grep -q ' UEFI ' <<< "$catalog"
mv "$work/output.iso" "$out"
