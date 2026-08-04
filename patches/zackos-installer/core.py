#!/usr/bin/env python3
"""
ZackOS Installer - core logic shared by TUI and Manual front-ends.
Designed to run from within the booted live environment (real kernel,
real mount()/chroot() available - this is NOT the build sandbox).
"""
import os
import re
import subprocess
import sys
import shutil

from providers.profile import InstallerProfile
from providers.registry import validate_profile

TARGET = "/mnt/target"
NIX_CHANNEL = "/nix/var/nix/profiles/per-user/root/channels/nixpkgs"


class InstallError(Exception):
    pass


def log(msg):
    print(f"\033[1;36m==>\033[0m {msg}", flush=True)


def run(cmd, check=True, input_text=None, capture=False):
    """Run a shell command, echoing it first (manual-mode transparency)."""
    if isinstance(cmd, list):
        printable = " ".join(cmd)
    else:
        printable = cmd
    log(f"$ {printable}")
    kwargs = {}
    if input_text is not None:
        kwargs["input"] = input_text.encode()
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    result = subprocess.run(cmd, shell=isinstance(cmd, str), **kwargs)
    if capture:
        out = result.stdout.decode(errors="replace")
        print(out)
    if check and result.returncode != 0:
        raise InstallError(f"Command failed ({result.returncode}): {printable}")
    return result


def list_disks():
    """Return list of (device, size_human, model) for real installable disks."""
    disks = []
    for entry in sorted(os.listdir("/sys/block")):
        if re.match(r"^(sd|vd|nvme|hd|xvd)", entry):
            dev = f"/dev/{entry}"
            size_path = f"/sys/block/{entry}/size"
            model_path = f"/sys/block/{entry}/device/model"
            try:
                sectors = int(open(size_path).read().strip())
                size_gb = sectors * 512 / (1024 ** 3)
            except Exception:
                size_gb = 0
            try:
                model = open(model_path).read().strip()
            except Exception:
                model = "Unknown"
            disks.append((dev, f"{size_gb:.1f}G", model))
    return disks


def is_persistence_disk(disk):
    """Return True only for the explicitly labeled live persistence disk."""
    try:
        out = subprocess.check_output(
            ["blkid", "-s", "LABEL", "-o", "value", disk],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return out == "ZACKPERSIST"
    except Exception:
        return False


def partition_disk(disk):
    """Wipe target disk, create a single bootable primary ext4 partition."""
    if is_persistence_disk(disk):
        raise InstallError(
            f"Refusing to wipe {disk}: it is labeled ZACKPERSIST. "
            "Choose the separate installation target disk."
        )
    run(f"wipefs -a {disk}", check=False)
    sfdisk_script = "label: dos\nstart=2048, type=83, bootable\n"
    run(["sfdisk", disk], input_text=sfdisk_script)
    run("partprobe " + disk, check=False)
    # first partition device name
    if re.search(r"\d$", disk):
        part = disk + "p1"
    else:
        part = disk + "1"
    return part


def format_partition(part, label="ZACKROOT"):
    run(f"mke2fs -F -t ext4 -L {label} {part}")


def mount_target(part):
    os.makedirs(TARGET, exist_ok=True)
    run(f"mount {part} {TARGET}")


def check_disk_space(excludes):
    """Rough preflight check that the target has enough room for the copy.
    NOTE: deliberately NOT using `du -x`/--one-file-system here. On this
    live system's overlayfs root, unmodified files are still backed by the
    lower (squashfs) device while copied-up files are backed by the upper
    (tmpfs) device, so `-x` silently excludes almost everything and
    undercounts to ~0. The excludes list already covers every other real
    mount in this live environment (proc/sys/dev/tmp/run/mnt/media), so a
    plain `du -s` over `/` gives the same effective total tar will copy.
    """
    du_excludes = " ".join(f"--exclude={e}" for e in excludes)
    result = subprocess.run(
        f"du -s {du_excludes} --block-size=1 /", shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        needed_bytes = int(result.stdout.decode().split()[0])
    except Exception:
        log("Warning: could not estimate required disk space, skipping preflight check.")
        return
    needed_gb = needed_bytes / (1024 ** 3)
    st = os.statvfs(TARGET)
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    log(f"Space check: need ~{needed_gb:.2f}G, target has {free_gb:.2f}G free.")
    if free_gb < needed_gb * 1.15:
        raise InstallError(
            f"Not enough space on target: need ~{needed_gb:.2f}G (+headroom), "
            f"only {free_gb:.2f}G free. Use a larger disk and try again."
        )


def copy_system(exclude_extra=None):
    """Copy the currently running live root filesystem onto the target."""
    excludes = ["/proc", "/sys", "/dev", "/tmp", "/run", "/mnt", "/media",
                "/root/persist_test.txt"]
    if exclude_extra:
        excludes += exclude_extra
    check_disk_space(excludes)
    tar_excludes = " ".join(f"--exclude={e}" for e in excludes)
    log("Copying base system to target disk (this can take a few minutes)...")
    cmd = (
        f"tar --one-file-system {tar_excludes} -cf - -C / . | "
        f"tar xpf - -C {TARGET}"
    )
    run(cmd)
    for d in ("proc", "sys", "dev", "tmp", "run", "mnt", "media"):
        os.makedirs(os.path.join(TARGET, d), exist_ok=True)


def chroot_run(cmd):
    """Run a command inside the target chroot with proc/sys/dev bind-mounted."""
    for src, dst in (("/proc", "proc"), ("/sys", "sys"), ("/dev", "dev")):
        dst_path = os.path.join(TARGET, dst)
        run(f"mount --bind {src} {dst_path}", check=False)
    full = f"chroot {TARGET} /bin/bash -c \"{cmd}\""
    result = run(full, check=False)
    return result


def install_bootloader(disk, part, label="ZACKROOT"):
    log("Installing GRUB bootloader...")
    # Use the full path, not bare 'grub-install': grub is provided via a
    # /usr/local/sbin symlink into the Nix store, but /usr/local/sbin is not
    # guaranteed to be on $PATH for every shell context this runs under
    # (confirmed: bare 'grub-install' -> "command not found" from the live
    # environment's default non-login PATH, even though the binary exists
    # and works fine).
    grub_install = shutil.which("grub-install") or "/usr/local/sbin/grub-install"
    if not os.path.exists(grub_install):
        raise InstallError(
            "grub-install not found (checked $PATH and /usr/local/sbin). "
            "The live/target rootfs is missing GRUB tools."
        )
    run(f"{grub_install} --target=i386-pc --boot-directory={TARGET}/boot "
        f"--modules='part_msdos ext2 biosdisk' {disk}")
    write_grub_cfg(part, label)


def write_grub_cfg(part, label="ZACKROOT"):
    """Hand-write a minimal grub.cfg instead of trusting grub-mkconfig's
    auto-generated config. grub-mkconfig defaults to `terminal_output
    gfxterm` (graphical framebuffer) which is invisible over a serial
    console and caused real hangs during boot testing in this project.

    IMPORTANT - two separate identifiers are needed here, for two
    different consumers, confirmed the hard way across three broken
    attempts:

    1. GRUB's own `search` command (finds the filesystem containing
       /boot so it can load the kernel file) - this DOES understand
       --fs-uuid natively and works fine. Keep using the filesystem UUID
       here, read fresh via blkid right before writing the config.

    2. The kernel's root= boot parameter - this system has NO initramfs
       (confirmed: no `initrd` line below), so there is no udev/mdev to
       populate /dev/disk/by-uuid or /dev/disk/by-label before the root
       mount attempt. Plain `root=UUID=...` and `root=LABEL=...` are
       NOT reliably resolved by the bare kernel without an initramfs -
       both were tried here and both produced 'VFS: Unable to mount
       root fs on unknown-block(0,0)'. The one identifier the kernel CAN
       resolve with no initramfs, straight from the partition table
       itself (no filesystem superblock scan needed) is PARTUUID. Use
       that for root=.
    """
    boot_dir = os.path.join(TARGET, "boot")
    kernels = sorted(
        f for f in os.listdir(boot_dir) if f.startswith("vmlinuz-")
    )
    if not kernels:
        raise InstallError("No kernel found in /boot on target - cannot write grub.cfg")
    kernel = kernels[-1]

    run("sync", check=False)

    def blkid_field(field):
        proc = subprocess.run(f"blkid -c /dev/null -s {field} -o value {part}", shell=True,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return proc.stdout.decode().strip()

    fs_uuid = blkid_field("UUID")
    partuuid = blkid_field("PARTUUID")
    if not fs_uuid:
        raise InstallError(f"Could not read filesystem UUID for {part}")
    if not partuuid:
        raise InstallError(
            f"Could not read PARTUUID for {part} - needed for a no-initramfs "
            "boot. Check the partition table type (expected msdos/dos)."
        )

    grub_dir = os.path.join(TARGET, "boot/grub")
    os.makedirs(grub_dir, exist_ok=True)
    cfg = f"""# ZackOS hand-written grub.cfg - plain console output on purpose,
# see write_grub_cfg() in the installer for why.
set timeout=5
set default=0

insmod part_msdos
insmod ext2
search --no-floppy --fs-uuid --set=root {fs_uuid}
terminal_output console

menuentry "ZackOS" {{
    linux /boot/{kernel} root=PARTUUID={partuuid} ro console=tty0 console=ttyS0,115200
}}

menuentry "ZackOS (recovery / single-user)" {{
    linux /boot/{kernel} root=PARTUUID={partuuid} ro single console=tty0 console=ttyS0,115200
}}
"""
    with open(os.path.join(grub_dir, "grub.cfg"), "w") as f:
        f.write(cfg)

def write_fstab(part, label="ZACKROOT"):
    fstab_path = os.path.join(TARGET, "etc/fstab")
    with open(fstab_path, "w") as f:
        # Keep the LFS virtual-filesystem mounts complete: SysVinit's
        # mountvirtfs service expects /run, devtmpfs, and cgroup2 here.
        # Without them an installed root booted with the kernel's default
        # read-only flag stops before getty starts.
        f.write(f"LABEL={label}  /  ext4  defaults  1  1\n")
        f.write("proc  /proc  proc  nosuid,noexec,nodev  0  0\n")
        f.write("sysfs /sys   sysfs nosuid,noexec,nodev  0  0\n")
        f.write("devpts /dev/pts devpts gid=5,mode=620 0 0\n")
        f.write("tmpfs /run   tmpfs defaults 0 0\n")
        f.write("devtmpfs /dev devtmpfs mode=0755,nosuid 0 0\n")
        f.write("tmpfs /dev/shm tmpfs nosuid,nodev 0 0\n")
        f.write("cgroup2 /sys/fs/cgroup cgroup2 nosuid,noexec,nodev 0 0\n")


def set_hostname(hostname):
    with open(os.path.join(TARGET, "etc/hostname"), "w") as f:
        f.write(hostname + "\n")
    hosts_path = os.path.join(TARGET, "etc/hosts")
    with open(hosts_path, "w") as f:
        f.write(f"127.0.0.1 localhost\n127.0.1.1 {hostname}\n")


def set_root_password(password):
    chroot_run(f"echo 'root:{password}' | chpasswd")


def create_user(username, password, use_wheel=True):
    chroot_run(f"useradd -m -s /bin/bash {username}")
    chroot_run(f"echo '{username}:{password}' | chpasswd")
    if use_wheel:
        chroot_run(f"groupadd -f wheel")
        chroot_run(f"usermod -aG wheel {username}")


def write_profile(profile):
    """Persist the exact LFS composition selected by the user."""
    path = os.path.join(TARGET, "etc/zackos")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "profile.toml"), "w") as f:
        f.write(profile.to_toml())
    log(f"Wrote ZackOS profile: {profile.to_dict()}")


def install_wm(choice):
    if choice == "none":
        log("Skipping WM install (console-only system).")
        return
    log("Installing i3-wm + xterm + dmenu via Nix (needs network access)...")
    export = (
        f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
        f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; "
    )
    chroot_run(export + "nix-env -iA nixpkgs.i3 nixpkgs.xterm nixpkgs.dmenu nixpkgs.xorg.xorgserver nixpkgs.xorg.xinit")
    link_nix_bins(["i3", "i3bar", "i3status", "xterm", "dmenu", "startx", "Xorg"])
    xinitrc = os.path.join(TARGET, "etc/skel/.xinitrc")
    os.makedirs(os.path.dirname(xinitrc), exist_ok=True)
    with open(xinitrc, "w") as f:
        f.write("#!/bin/sh\nexec i3\n")
    os.chmod(xinitrc, 0o755)
    root_xinitrc = os.path.join(TARGET, "root/.xinitrc")
    shutil.copy(xinitrc, root_xinitrc)


def link_nix_bins(names):
    """Expose Nix package executables from both bin/ and sbin/.

    runit installs its PID1/stage binary as ``sbin/runit`` on some Nix
    revisions, while runsvdir/sv may live in ``bin``.  Looking in only one
    directory was the cause of the old ``/sbin/init`` service failure.
    """
    nix_store = os.path.join(TARGET, "nix/store")
    if not os.path.isdir(nix_store):
        raise InstallError("Nix store is missing; cannot install provider binaries")
    destinations = {
        "bin": os.path.join(TARGET, "usr/local/bin"),
        "sbin": os.path.join(TARGET, "usr/local/sbin"),
    }
    found = set()
    for directory in destinations.values():
        os.makedirs(directory, exist_ok=True)
    for entry in os.listdir(nix_store):
        for subdir, destination in destinations.items():
            source_dir = os.path.join(nix_store, entry, subdir)
            if not os.path.isdir(source_dir):
                continue
            for name in names:
                candidate = os.path.join(source_dir, name)
                if os.path.isfile(candidate) or os.path.islink(candidate):
                    link_path = os.path.join(destination, name)
                    if not os.path.exists(link_path):
                        os.symlink(f"/nix/store/{entry}/{subdir}/{name}", link_path)
                    found.add(name)
    missing = sorted(set(names) - found)
    if missing:
        raise InstallError("Provider binaries were not found in the Nix store: " + ", ".join(missing))


def install_init(choice):
    if choice == "sysvinit":
        log("Keeping sysvinit (default, already configured).")
        return
    if choice != "runit":
        raise InstallError(f"Init provider {choice!r} is not implemented yet")

    log("Installing runit with explicit stage/service wiring...")
    export = (
        f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
        f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; "
    )
    chroot_run(export + "nix-env -iA nixpkgs.runit")
    link_nix_bins(["runit", "runsvdir", "runsv", "sv", "chpst", "svlogd"])

    etc_runit = os.path.join(TARGET, "etc/runit")
    service_dir = os.path.join(TARGET, "etc/service")
    os.makedirs(etc_runit, exist_ok=True)
    os.makedirs(service_dir, exist_ok=True)

    # Stage 1 mounts the virtual filesystems that SysVinit used to mount.
    with open(os.path.join(etc_runit, "1"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            "mount -t proc proc /proc 2>/dev/null || true\n"
            "mount -t sysfs sysfs /sys 2>/dev/null || true\n"
            "mount -t devtmpfs devtmpfs /dev 2>/dev/null || true\n"
            "mount -t devpts devpts /dev/pts 2>/dev/null || true\n"
            "mount -t tmpfs tmpfs /run 2>/dev/null || true\n"
            "mount -a 2>/dev/null || true\n"
            "hostname -F /etc/hostname 2>/dev/null || true\n"
            "exit 0\n"
        )
    # Stage 2 must use the absolute provider path; PID1 has no reliable PATH.
    with open(os.path.join(etc_runit, "2"), "w") as f:
        f.write("#!/bin/sh\nexec /usr/local/bin/runsvdir -P /etc/service\n")
    with open(os.path.join(etc_runit, "3"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            "for service in /etc/service/*; do\n"
            "  [ -d \"$service\" ] || continue\n"
            "  /usr/local/bin/sv -w 5 force-stop \"$service\" 2>/dev/null || true\n"
            "done\n"
        )
    for fname in ("1", "2", "3"):
        os.chmod(os.path.join(etc_runit, fname), 0o755)

    def write_service(name, command):
        directory = os.path.join(service_dir, name)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "run"), "w") as f:
            f.write("#!/bin/sh\nexec " + command + "\n")
        os.chmod(os.path.join(directory, "run"), 0o755)

    # ttyS0 is required for QEMU serial verification; tty1 is required on hardware.
    write_service("agetty-tty1", "/sbin/agetty tty1 38400 linux")
    write_service("agetty-ttyS0", "/sbin/agetty -L 115200 ttyS0 vt100")

    # runit is PID1; preserve SysVinit as a recovery binary.
    sbin_init = os.path.join(TARGET, "sbin/init")
    if os.path.exists(sbin_init) and not os.path.islink(sbin_init):
        shutil.copy2(sbin_init, sbin_init + ".sysvinit")
    with open(sbin_init, "w") as f:
        f.write("#!/bin/sh\nexec /usr/local/sbin/runit\n")
    os.chmod(sbin_init, 0o755)


def cleanup_mounts():
    for d in ("dev", "sys", "proc"):
        run(f"umount -lf {TARGET}/{d}", check=False)
    run(f"umount -lf {TARGET}", check=False)


def full_install(disk, wm_choice=None, init_choice=None, hostname="zackos",
                  root_password="", username=None, user_password=None, profile=None):
    profile = profile or InstallerProfile(
        desktop=wm_choice or "i3", init=init_choice or "sysvinit"
    )
    errors = validate_profile(profile, require_implemented=True)
    if errors:
        raise InstallError("Profile is not installable yet: " + "; ".join(errors))
    part = partition_disk(disk)
    format_partition(part)
    mount_target(part)
    try:
        copy_system()
        write_fstab(part)
        write_profile(profile)
        set_hostname(hostname)
        set_root_password(root_password)
        if username:
            create_user(username, user_password or root_password)
        install_wm(profile.desktop)
        install_init(profile.init)
        install_bootloader(disk, part)
    finally:
        cleanup_mounts()
    log("Install complete! Remove installation media and reboot.")
