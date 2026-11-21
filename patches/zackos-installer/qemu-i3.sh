#!/usr/bin/env bash
# Start the installed disk, or provide an ISO as the second argument for installation.
set -euo pipefail
if (( $# < 1 || $# > 2 )); then echo "Usage: $0 DISK.qcow2 [INSTALLER.iso]" >&2; exit 2; fi
disk=$1
media=()
if (( $# == 2 )); then
    test -f "$2"
    if [[ ! -e "$disk" ]]; then qemu-img create -f qcow2 "$disk" 24G; fi
    media=(-cdrom "$2" -boot once=d)
fi
test -f "$disk"
if [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]] && qemu-system-x86_64 -accel help | grep -qx kvm; then
    # KVM can be present but inaccessible in a nested VM. QEMU tries TCG next.
    accel=(-accel kvm -accel tcg)
else
    accel=(-accel tcg)
fi
exec qemu-system-x86_64 -machine pc "${accel[@]}" -cpu max -smp 2 -m 4096 -vga virtio \
    -drive "file=$disk,if=virtio,format=qcow2" -nic user,model=virtio-net-pci \
    -serial mon:stdio "${media[@]}"
