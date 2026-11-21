#!/usr/bin/env bash
# Run on a stock ZackOS i3 build machine, or feed its export to tar2sqfs on another host.
set -euo pipefail
out=$(realpath -m "${1:?output desktop.img path}")
test ! -e "$out"
src=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "$(dirname "$out")/.desktop-XXXXXX")
trap 'rm -rf "$work"' EXIT
"$src/export-desktop.sh" "${2:-}" |
    tar2sqfs -q -s -c gzip -X level=5 -j 4 "$work/desktop.img"
mv "$work/desktop.img" "$out"
