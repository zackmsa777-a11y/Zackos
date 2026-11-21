#!/usr/bin/env bash
# Start the installed disk, or provide an ISO as the second argument for installation.
set -euo pipefail
disk=${1:?provide a qcow2 disk path}
media=()
if [[ $# -eq 2 ]]; then
    test -f "$2"
    if [[ ! -e "$disk" ]]; then qemu-img create -f qcow2 "$disk" 24G; fi
    media=(-cdrom "$2" -boot once=d)
fi
test -f "$disk"
exec qemu-system-x86_64 -machine pc -cpu max -smp 2 -m 4096 -vga virtio \
    -drive "file=$disk,if=virtio,format=qcow2" -nic user,model=virtio-net-pci \
    -serial mon:stdio "${media[@]}"
