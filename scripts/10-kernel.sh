#!/bin/bash
set -uo pipefail
LFS=/app/lfs_build
STAMPS="$LFS/.stamps10"
mkdir -p "$STAMPS"
LOG="$LFS/ch10_output.log"

run_step () {
  local name="$1"
  local script="$2"
  if [ -f "$STAMPS/$name" ]; then
    echo "[$(date +%T)] $name already done" | tee -a "$LOG"
    return 0
  fi
  echo "[$(date +%T)] === $name ===" | tee -a "$LOG"
  if bash /app/lfs_build/scripts/chroot-run.sh "$script" >> "$LOG" 2>&1; then
    touch "$STAMPS/$name"
    echo "[$(date +%T)] === $name DONE ===" | tee -a "$LOG"
  else
    echo "[$(date +%T)] !!! $name FAILED !!!" | tee -a "$LOG"
    exit 1
  fi
}

run_step "fstab"  /scripts_chroot/fstab.sh
config=config-6.16.1-lfs-12.4-bochs-drm.config
source_config="$(cd "$(dirname "$0")/.." && pwd)/patches/kernel-configs/$config"
if [[ ! "$source_config" -ef "$LFS/patches/kernel-configs/$config" ]]; then
  install -Dm644 "$source_config" "$LFS/patches/kernel-configs/$config" || exit 1
fi
run_step "kernel" /scripts_chroot/kernel.sh

echo "[$(date +%T)] === CHAPTER 10 COMPLETE ===" | tee -a "$LOG"
