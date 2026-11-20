#!/usr/bin/env bash
# Write a tar stream of the stock i3 Nix closure and its database registration.
set -euo pipefail
src=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
if [[ -n ${1:-} ]]; then
    profile=$(readlink -e "$1")
else
    mapfile -t packages < "$src/desktop/i3-packages"
    nix-env -p "$work/profile" -f '<nixpkgs>' -iA nix runit "${packages[@]}" >&2
    nix-env -p "$work/profile" -f "$src/desktop/panel/runtime.nix" -i >&2
    profile=$(readlink -e "$work/profile")
fi
for name in i3 Xorg xinit xauth dbus-run-session feh zackos-panel lightdm-gtk-greeter kitty zsh fastfetch runit nix-store nix-env; do
    test -x "$profile/bin/$name" || { echo "$name is missing from the desktop profile" >&2; exit 1; }
done
for file in sbin/lightdm bin/unix_chkpwd lib/security/pam_unix.so lib/security/pam_succeed_if.so lib/security/pam_permit.so lib/security/pam_deny.so; do
    test -e "$profile/$file" || { echo "$file is missing from the desktop profile" >&2; exit 1; }
done
gtk=$(nix-build --no-out-link "$src/desktop/gtk-python.nix")
meta=usr/local/share/zackos-media
mkdir -p "$work/$meta"
printf '%s\n' "$profile" > "$work/$meta/profile"
ln -s "$gtk/bin/zackos-gtk-python" "$work/$meta/gtk-python"
nix-store --query --requisites "$profile" "$gtk" | sort -u > "$work/closure"
mapfile -t paths < "$work/closure"
nix-store --dump-db "${paths[@]}" > "$work/$meta/registration"
sed 's|^/||' "$work/closure" > "$work/files"
chmod 755 "$work/$meta"
chmod 644 "$work/$meta/profile" "$work/$meta/registration"
tar --owner=0 --group=0 --numeric-owner --format=posix --pax-option=delete=atime,delete=ctime \
    -cf - -C / -T "$work/files" -C "$work" "$meta"
