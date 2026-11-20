#!/bin/sh
# Build for the x86-64 live and installed systems, including the tiny initramfs.
set -eu
src=$(dirname "$0")
exec "${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -static -s \
    $(pkg-config --cflags libdrm libpng) "$src/splash.c" \
    $(pkg-config --static --libs libdrm libpng) -lm -o "${1:?output binary}"
