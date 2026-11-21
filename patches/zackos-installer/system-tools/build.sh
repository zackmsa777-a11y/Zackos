#!/usr/bin/env bash
# Install host-built x86-64 tools against ZackOS's native glibc/OpenSSL ABI.
set -euo pipefail
root=$(realpath "${1:?provide the extracted ZackOS root}")
output=${2:-}
src=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
media="$work/disc"
files="$media/files/usr"
mkdir -p "$files"/{bin,sbin,libexec/sudo,share/zackos-tools} "$work/static"
fetch() {
    curl -fsSL --retry 4 "$2" -o "$1"
    echo "$3  $1" | sha256sum -c -
    tar -xf "$1"
}
fetch sudo.tar.gz https://github.com/sudo-project/sudo/releases/download/v1.9.17p2/sudo-1.9.17p2.tar.gz 4a38a1ab3adb1199257edc2a7c4a2bd714665eb605b04368843b06dada2cfcfb
fetch wget.tar.gz https://ftp.gnu.org/gnu/wget/wget-1.25.0.tar.gz 766e48423e79359ea31e41db9e5c289675947a7fcf2efdcedb726ac9d0da3784
fetch curl.tar.xz https://github.com/curl/curl/releases/download/curl-8_23_0/curl-8.23.0.tar.xz 8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5
jobs=$(nproc)
# Keep IDN, public-suffix cookie checks and HTTP/2 without introducing host
# library paths into the image. OpenSSL, glibc and zlib come from ZackOS.
for lib in psl idn2 unistring nghttp2; do
    archive=$(cc -print-file-name="lib$lib.a")
    test -f "$archive"
    ln -s "$archive" "static/lib$lib.a"
done
(
    cd sudo-1.9.17p2
    ./configure --prefix=/usr --sysconfdir=/etc --libexecdir=/usr/libexec \
        --with-rundir=/run/sudo --with-vardir=/var/lib/sudo --without-pam \
        --without-linux-audit --without-selinux --without-apparmor --without-ldap \
        --enable-static-sudoers --disable-shared-libutil --disable-log-server --disable-nls
    make -j"$jobs"
    make install DESTDIR="$work/sudo-install"
)
(
    export LDFLAGS="-L$work/static" LIBS="-lidn2 -lunistring"
    cd curl-8.23.0
    ./configure --prefix=/usr --disable-shared --enable-static --with-openssl \
        --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt --with-libpsl --with-libidn2 --with-nghttp2 \
        --without-brotli --without-zstd --without-libssh2 --without-librtmp --disable-ldap --disable-ldaps
    make -j"$jobs" -C lib
    make -j"$jobs" -C src
)
(
    export LDFLAGS="-L$work/static" LIBS="-lidn2 -lunistring"
    cd wget-1.25.0
    ./configure --prefix=/usr --sysconfdir=/etc --with-ssl=openssl --disable-nls --disable-pcre2 --disable-pcre
    make -j"$jobs"
)
install -m755 curl-8.23.0/src/curl wget-1.25.0/src/wget "$work/sudo-install/usr/bin/sudo" "$files/bin/"
install -m755 "$work/sudo-install/usr/sbin/visudo" "$files/sbin/"
plugins="$work/sudo-install/usr/libexec/sudo"
install -m644 "$plugins"/*.so "$files/libexec/sudo/"
if [[ -f $plugins/sesh ]]; then install -m755 "$plugins/sesh" "$files/libexec/sudo/"; fi
strip "$files"/bin/{curl,wget,sudo} "$files/sbin/visudo" "$files"/libexec/sudo/*
# Use the original trusted Shadow executables from the checksum-verified base.
install -m755 "$root/usr/bin/su" "$root/usr/bin/passwd" "$files/bin/"
install -m644 sudo-1.9.17p2/LICENSE.md "$files/share/zackos-tools/sudo-LICENSE"
install -m644 wget-1.25.0/COPYING "$files/share/zackos-tools/wget-COPYING"
install -m644 curl-8.23.0/COPYING "$files/share/zackos-tools/curl-COPYING"
for pkg in libpsl-dev libidn2-dev libunistring-dev libnghttp2-dev; do
    install -m644 "/usr/share/doc/$pkg/copyright" "$files/share/zackos-tools/$pkg-copyright"
done
install -m644 "$src/install.py" "$src/sudoers" "$src/../boot/runit-stage1" "$media/"
python3 "$media/install.py" --root "$root"
if [[ -n $output ]]; then
    xorriso -as mkisofs -quiet -r -J -V ZACKTOOLS -o "$output" "$media"
fi
