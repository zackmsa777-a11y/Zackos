#!/usr/bin/env bash
# Supply offline FAT formatting, ACPI events and the UEFI fallback loader.
set -euo pipefail
dest=$(realpath "${1:?provide the extracted base root}")
src=$(cd "$(dirname "$0")/.." && pwd)
# LFS OpenSSL reads cert.pem; Nix and curl already use the packaged trust bundle.
certs="$dest/etc/ssl/certs/ca-certificates.crt"
if [[ ! -s $certs ]]; then
    shopt -s nullglob
    bundles=("$dest"/nix/store/*-nss-cacert-*/etc/ssl/certs/ca-bundle.crt)
    (( ${#bundles[@]} )) || { echo "Missing the base system CA trust bundle" >&2; exit 1; }
    install -Dm644 "${bundles[-1]}" "$certs"
fi
if [[ ! -e $dest/etc/ssl/cert.pem ]]; then
    ln -sfn certs/ca-certificates.crt "$dest/etc/ssl/cert.pem"
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
curl -fLsS --retry 3 -o dos.tar.gz https://deb.debian.org/debian/pool/main/d/dosfstools/dosfstools_4.2.orig.tar.gz
curl -fLsS --retry 3 -o acpi.tar.xz https://deb.debian.org/debian/pool/main/a/acpid/acpid_2.0.34.orig.tar.xz
echo '64926eebf90092dca21b14259a5301b7b98e7b1943e8a201c7d726084809b527  dos.tar.gz' | sha256sum -c -
echo '2d095c8cfcbc847caec746d62cdc8d0bff1ec1bc72ef7c674c721e04da6ab333  acpi.tar.xz' | sha256sum -c -
tar xf dos.tar.gz
tar xf acpi.tar.xz
(cd dosfstools-4.2 && ./configure LDFLAGS=-static && make -C src mkfs.fat)
(cd acpid-2.0.34 && ./configure LDFLAGS=-static && make acpid)
install -Dm755 dosfstools-4.2/src/mkfs.fat "$dest/usr/sbin/mkfs.fat"
install -Dm755 acpid-2.0.34/acpid "$dest/usr/sbin/acpid"
efi="$dest/usr/local/share/zackos-efi"
mkdir -p "$efi"
# The installer uses the base system's GRUB image builder to embed the new
# root UUID in BOOTX64.EFI, even when firmware supplies no loader device path.
cp -a /usr/lib/grub/x86_64-efi "$efi/"
install -m644 /usr/share/grub/unicode.pf2 "$efi/unicode.pf2"
"$src/system-tools/build.sh" "$dest" "${ZACKOS_TOOLS_ISO:-}"
