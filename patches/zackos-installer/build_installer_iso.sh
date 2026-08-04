#!/bin/bash
# ZackOS installer-ISO rebuild - fully self-contained, idempotent-ish.
# Re-run this same script after any sandbox revert; it re-downloads/rebuilds
# everything from GitHub + the v4.2 release, no manual steps needed.
set -e
WORK=/app/installer_work
REPO_URL="https://github.com/zackmsa777-a11y/lfs-12.4-from-scratch"
GH_TOKEN="${GITHUB_TOKEN}"

echo "=== [1/8] apt/tooling ==="
sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
apt-get update -qq
apt-get install -y -qq procps qemu-system-x86 squashfs-tools xorriso grub-pc-bin grub-common mtools e2fsprogs git curl

mkdir -p "$WORK"
cd "$WORK"

echo "=== [2/8] fetch installer source from GitHub checkpoint ==="
rm -rf gh_repo
git clone -q "https://zackmsa777-a11y:${GH_TOKEN}@github.com/zackmsa777-a11y/lfs-12.4-from-scratch.git" gh_repo
INSTALLER_SRC="$WORK/gh_repo/patches/zackos-installer"
test -f "$INSTALLER_SRC/core.py" || { echo "FATAL: installer source missing from GitHub checkpoint"; exit 1; }

echo "=== [3/8] fetch v4.2 base ISO (skip if already present+verified) ==="
if [ ! -f zackos_v42.iso ] || [ "$(md5sum zackos_v42.iso 2>/dev/null | awk '{print $1}')" != "01ce19d3e421f53f7212c0759a1d9176" ]; then
  for p in part00 part01 part02; do
    curl -sL -o zackos_v42_$p "https://github.com/zackmsa777-a11y/lfs-12.4-from-scratch/releases/download/v4.2-live-cleaned-iso/zackos_v42_$p" &
  done
  wait
  cat zackos_v42_part00 zackos_v42_part01 zackos_v42_part02 > zackos_v42.iso
  rm -f zackos_v42_part00 zackos_v42_part01 zackos_v42_part02
fi
echo "v4.2 iso md5: $(md5sum zackos_v42.iso | awk '{print $1}') (expect 01ce19d3e421f53f7212c0759a1d9176)"

echo "=== [4/8] extract ISO + squashfs ==="
rm -rf iso_layout rootfs
mkdir -p iso_layout
xorriso -osirrox on -indev zackos_v42.iso -extract / iso_layout > /dev/null 2>&1
unsquashfs -d rootfs iso_layout/squash.img > /dev/null 2>&1
echo "rootfs size: $(du -sh rootfs | cut -f1)"

echo "=== [4b/8] install ZackOS live persistence init ==="
rm -rf initrd_work
mkdir -p initrd_work
# The base image ships a gzip-compressed cpio initramfs. Replace only its
# early userspace init; kernel and the rest of the live image stay untouched.
zcat iso_layout/boot/initramfs.img | (cd initrd_work && cpio -idm --quiet)
cp "$INSTALLER_SRC/../live-boot/zack-live-init.sh" initrd_work/init
chmod +x initrd_work/init
(cd initrd_work && find . -print | cpio -o -H newc --quiet | gzip -9) > iso_layout/boot/initramfs.img

echo "=== [5/8] prep chroot (dev nodes, resolv.conf, installer files) ==="
cd rootfs
rm -f dev/null dev/zero dev/random dev/urandom dev/tty dev/console
mknod -m666 dev/null c 1 3
mknod -m666 dev/zero c 1 5
mknod -m666 dev/random c 1 8
mknod -m666 dev/urandom c 1 9
mknod -m666 dev/tty c 5 0
mknod -m600 dev/console c 5 1
for f in null zero random urandom tty console; do
  t=$(stat -c "%F" dev/$f)
  [ "$t" = "character special file" ] || { echo "FATAL: dev/$f is not a char device ($t)"; exit 1; }
done
mkdir -p proc/self sys
NIX_STORE_PATH=$(find nix/store -maxdepth 1 -iname "*-nix-2.35.1" | head -1)
ln -sf "/${NIX_STORE_PATH}/bin/nix" proc/self/exe
cp /etc/resolv.conf etc/resolv.conf
mkdir -p usr/local/lib/zackos-installer usr/local/bin
cp "$INSTALLER_SRC"/core.py "$INSTALLER_SRC"/zackinstall.py usr/local/lib/zackos-installer/
cp "$INSTALLER_SRC"/zackinstall usr/local/bin/
chmod +x usr/local/bin/zackinstall

# Bake in the checkpointed /etc/profile + network-init fixes permanently.
# These live in git as reference patches (patches/etc-configs/profile,
# patches/etc-configs/network-init) from the 2026-08-01 fix session but were
# NEVER actually applied by this build script before now -- confirmed via a
# live boot test (bare `zackinstall`/`ip addr` both broken: PATH missing
# /usr/local/{bin,sbin}, ens3 stayed DOWN with no DHCP client running).
cp "$WORK/gh_repo/patches/etc-configs/profile" etc/profile
cp "$WORK/gh_repo/patches/etc-configs/network-init" etc/init.d/network
cp "$WORK/gh_repo/patches/etc-configs/network-init" etc/rc.d/init.d/network
chmod +x etc/init.d/network etc/rc.d/init.d/network
cd "$WORK"

echo "=== [6/8] install grub2 into rootfs via nix (needs network) ==="
GRUB_DIR=$(find rootfs/nix/store -maxdepth 1 -iname "*-grub-2.12" 2>/dev/null | head -1)
if [ -z "$GRUB_DIR" ]; then
  NIX_BIN="/${NIX_STORE_PATH}/bin"
  chroot rootfs /bin/sh -c "export PATH=$NIX_BIN:\$PATH; export HOME=/root; nix-env -f https://github.com/NixOS/nixpkgs/archive/nixos-unstable.tar.gz -iA grub2" || true
  GRUB_DIR=$(find rootfs/nix/store -maxdepth 1 -iname "*-grub-2.12" 2>/dev/null | head -1)
fi
test -n "$GRUB_DIR" || { echo "FATAL: grub2 not present in nix store after install attempt"; exit 1; }
GRUB_STORE=${GRUB_DIR#rootfs/}
echo "grub store path: $GRUB_STORE"
mkdir -p rootfs/usr/local/sbin
for b in $(ls "rootfs/$GRUB_STORE/sbin/"); do
  ln -sf "/$GRUB_STORE/sbin/$b" "rootfs/usr/local/sbin/$b"
done
chroot rootfs /bin/sh -c "export PATH=/usr/local/sbin:\$PATH; grub-install --version" || { echo "FATAL: grub-install not runnable"; exit 1; }
echo "grub-install OK"

echo "=== [6b/8] install dhcpcd into rootfs via nix (live-boot DHCP + installer network access) ==="
DHCPCD_DIR=$(find rootfs/nix/store -maxdepth 1 -iname "*-dhcpcd-*" ! -name "*.drv" 2>/dev/null | head -1)
if [ -z "$DHCPCD_DIR" ]; then
  NIX_BIN="/${NIX_STORE_PATH}/bin"
  chroot rootfs /bin/sh -c "export PATH=$NIX_BIN:\$PATH; export HOME=/root; nix-env -f https://github.com/NixOS/nixpkgs/archive/nixos-unstable.tar.gz -iA dhcpcd" || true
  DHCPCD_DIR=$(find rootfs/nix/store -maxdepth 1 -iname "*-dhcpcd-*" ! -name "*.drv" 2>/dev/null | head -1)
fi
test -n "$DHCPCD_DIR" || { echo "FATAL: dhcpcd not present in nix store after install attempt"; exit 1; }
DHCPCD_STORE=${DHCPCD_DIR#rootfs/}
echo "dhcpcd store path: $DHCPCD_STORE"
ln -sf "/$DHCPCD_STORE/sbin/dhcpcd" rootfs/usr/local/sbin/dhcpcd
chroot rootfs /bin/sh -c "export PATH=/usr/local/sbin:\$PATH; dhcpcd --version" || { echo "FATAL: dhcpcd not runnable"; exit 1; }
echo "dhcpcd OK"

echo "=== [7/8] repack squashfs + ISO ==="
cd rootfs
rm -rf proc/self sys/*
rm -f dev/null dev/zero dev/random dev/urandom dev/tty dev/console
mknod -m666 dev/null c 1 3
mknod -m666 dev/zero c 1 5
mknod -m666 dev/random c 1 8
mknod -m666 dev/urandom c 1 9
mknod -m666 dev/tty c 5 0
mknod -m600 dev/console c 5 1
cd "$WORK"
rm -f squash_installer.img
mksquashfs rootfs squash_installer.img -comp gzip -processors 4 -noappend
echo "squash size: $(du -sh squash_installer.img | cut -f1)"

rm -rf iso_final
mkdir -p iso_final/boot/grub
cp iso_layout/boot/vmlinuz iso_final/boot/ 2>/dev/null || cp iso_layout/boot/*vmlinuz* iso_final/boot/ 2>/dev/null || true
cp iso_layout/boot/initramfs.img iso_final/boot/ 2>/dev/null || cp iso_layout/boot/*initramfs* iso_final/boot/ 2>/dev/null || true
ls iso_layout/boot/
cp squash_installer.img iso_final/squash.img
cat iso_layout/boot/grub/grub.cfg 2>/dev/null > iso_final/boot/grub/grub.cfg || true
grub-mkrescue -o zackos_installer.iso iso_final > /dev/null 2>&1
echo "=== [8/8] done ==="
ls -la zackos_installer.iso
md5sum zackos_installer.iso
