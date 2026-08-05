#!/bin/sh
# ZackOS live init: Debian-style labeled persistence, ZackOS-owned boot flow.
# The live system never auto-formats or mounts an arbitrary install disk.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || mount -t tmpfs tmpfs /dev

log() { echo "[ZackOS live] $*"; }

mkdir -p /mnt/cdrom /mnt/squash /mnt/persist /mnt/overlay /newroot
CDDEV=""

# Find the read-only ISO by content, not by unstable device name.
# CD/DVD controllers (real hardware and some QEMU/hypervisor AHCI/IDE
# configs) can take longer than a single fixed sleep to register in
# /sys/block - a one-shot scan after `sleep 1` races that and can find
# nothing yet, dropping to a rescue shell even though the media is fine
# and shows up moments later. Retry the whole scan for up to ~10s instead
# of a single pass, giving slower hardware/controllers time to enumerate.
attempt=0
while [ -z "$CDDEV" ] && [ "$attempt" -lt 10 ]; do
    attempt=$((attempt + 1))
    for n in /sys/block/*; do
        base=$(basename "$n")
        case "$base" in loop*|ram*) continue ;; esac
        for dev in "/dev/$base" /dev/"$base"[0-9]*; do
            [ -b "$dev" ] || continue
            umount /mnt/cdrom 2>/dev/null || true
            if mount -t iso9660 -o ro "$dev" /mnt/cdrom 2>/dev/null || mount -o ro "$dev" /mnt/cdrom 2>/dev/null; then
                if [ -f /mnt/cdrom/squash.img ]; then
                    CDDEV="$dev"
                    break 2
                fi
                umount /mnt/cdrom 2>/dev/null || true
            fi
        done
    done
    [ -n "$CDDEV" ] && break
    sleep 1
done

if [ -z "$CDDEV" ]; then
    log "No boot media containing squash.img was found."
    ls -la /dev/sd* /dev/vd* /dev/nvme* /dev/sr* 2>/dev/null || true
    exec /bin/sh
fi
log "Boot media: $CDDEV"

mount -t squashfs -o loop,ro /mnt/cdrom/squash.img /mnt/squash || {
    log "Could not mount squash.img"
    exec /bin/sh
}

# Debian-style persistence: only a filesystem explicitly labeled ZACKPERSIST
# and containing persistence.conf is eligible. A blank target disk is never
# formatted or mounted here. The tiny initramfs has no blkid, so use the
# userspace copy from the already-mounted ZackOS squashfs through chroot.
probe_label() {
    if command -v blkid >/dev/null 2>&1; then
        blkid -s LABEL -o value "$1" 2>/dev/null
    elif [ -x /mnt/squash/usr/sbin/blkid ] && [ -x /bin/busybox ]; then
        /bin/busybox chroot /mnt/squash /usr/sbin/blkid -s LABEL -o value "$1" 2>/dev/null
    else
        echo ""
    fi
}

PERSISTDEV=""
# Make host block devices visible to the squashfs userspace blkid probe.
mkdir -p /mnt/squash/dev
mount --bind /dev /mnt/squash/dev 2>/dev/null || true
for n in /sys/block/*; do
    base=$(basename "$n")
    case "$base" in loop*|ram*) continue ;; esac
    for dev in "/dev/$base" /dev/"$base"[0-9]*; do
        [ -b "$dev" ] || continue
        [ "$dev" = "$CDDEV" ] && continue
        label=$(probe_label "$dev" || true)
        [ "$label" = "ZACKPERSIST" ] || continue
        if mount -t ext4 -o rw "$dev" /mnt/persist 2>/dev/null; then
            if [ -f /mnt/persist/persistence.conf ]; then
                PERSISTDEV="$dev"
                break 2
            fi
            umount /mnt/persist 2>/dev/null || true
        fi
    done
done

if [ -n "$PERSISTDEV" ]; then
    log "Persistence: $PERSISTDEV (LABEL=ZACKPERSIST)"
    mkdir -p /mnt/persist/upper /mnt/persist/work
    UPPERDIR=/mnt/persist/upper
    WORKDIR=/mnt/persist/work
else
    log "Persistence: RAM only (create an ext4 disk labeled ZACKPERSIST to enable it)"
    mount -t tmpfs -o size=1024M tmpfs /mnt/overlay
    mkdir -p /mnt/overlay/upper /mnt/overlay/work
    UPPERDIR=/mnt/overlay/upper
    WORKDIR=/mnt/overlay/work
fi

mount -t overlay overlay -o "lowerdir=/mnt/squash,upperdir=$UPPERDIR,workdir=$WORKDIR" /newroot || {
    log "Overlay mount failed"
    exec /bin/sh
}

# The live root must not try to mount the persistence or target disk as /
if [ -f /newroot/etc/fstab ]; then
    sed -i 's#^/dev/sda\([[:space:]]\).*#overlay\1/\1overlay\1defaults\1\10\10#' /newroot/etc/fstab
fi
touch /newroot/fastboot
mkdir -p /newroot/dev /newroot/proc /newroot/sys /newroot/mnt/cdrom /newroot/mnt/persist
if [ -n "$PERSISTDEV" ]; then
    mount --bind /mnt/persist /newroot/mnt/persist 2>/dev/null || true
fi
log "Starting ZackOS userspace"
exec switch_root /newroot /sbin/init
