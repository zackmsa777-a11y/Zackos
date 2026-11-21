#!/bin/sh
# Stage one complete installer payload for both ISO builders.
set -eu
src=$(cd "$(dirname "$0")" && pwd)
dst=${1:?installer payload directory}
mkdir -p "$dst/providers" "$dst/boot"
cp "$src"/core.py "$src"/zackinstall.py "$src"/zackinstall "$dst/"
cp "$src"/providers/*.py "$dst/providers/"
mkdir -p "$dst/live"
cp "$src"/live/*.py "$src"/live/*.css "$src"/live/*.desktop "$dst/live/"
mkdir -p "$dst/power"
cp "$src/power/zackos-power" "$dst/power/"
# The live init overlays this payload onto an existing copy with BusyBox cp.
# Materialize shared artwork so repeated boots can safely overwrite each file.
cp -RL "$src/branding" "$src/desktop" "$dst/"
cp "$src/boot/installed-init" "$src/boot/splash-done" "$src/boot/runit-stage1" "$dst/boot/"
"$src/boot/build-splash.sh" "$dst/boot/zack-splash"
find "$dst" -type d -exec chmod 755 {} +
find "$dst" -type f -exec chmod 644 {} +
chmod 755 "$dst/power/zackos-power" "$dst/zackinstall" "$dst/desktop/zackos-display" "$dst/boot/"*
