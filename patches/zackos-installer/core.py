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


def partition_disk(disk):
    """Wipe target disk, create a single bootable primary ext4 partition."""
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


def install_bootloader(disk, part):
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
    write_grub_cfg(part)


def write_grub_cfg(part):
    """Hand-write a minimal grub.cfg instead of trusting grub-mkconfig's
    auto-generated config. grub-mkconfig defaults to `terminal_output
    gfxterm` (graphical framebuffer) which is invisible over a serial
    console and caused real hangs during boot testing in this project.
    Also use root=UUID=... (not a raw device path like /dev/vdb1) since
    device names are NOT stable across different disk/bus configurations
    at boot time (confirmed: install with 2 disks attached names the
    target vdb, but booting standalone with only 1 disk renames it vda,
    causing a hardcoded /dev/vdb1 to kernel-panic with
    'VFS: Unable to mount root fs on unknown-block'). UUID is stable
    regardless of enumeration order/bus.
    """
    boot_dir = os.path.join(TARGET, "boot")
    kernels = sorted(
        f for f in os.listdir(boot_dir) if f.startswith("vmlinuz-")
    )
    if not kernels:
        raise InstallError("No kernel found in /boot on target - cannot write grub.cfg")
    kernel = kernels[-1]

    proc = subprocess.run(f"blkid -s UUID -o value {part}", shell=True,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    uuid = proc.stdout.decode().strip()
    if not uuid:
        raise InstallError(f"Could not read UUID for {part}")

    grub_dir = os.path.join(TARGET, "boot/grub")
    os.makedirs(grub_dir, exist_ok=True)
    cfg = f"""# ZackOS hand-written grub.cfg - plain console output on purpose,
# see write_grub_cfg() in the installer for why.
set timeout=5
set default=0

insmod part_msdos
insmod ext2
search --no-floppy --fs-uuid --set=root {uuid}
terminal_output console

menuentry "ZackOS" {{
    linux /boot/{kernel} root=UUID={uuid} ro console=tty0 console=ttyS0,115200
}}

menuentry "ZackOS (recovery / single-user)" {{
    linux /boot/{kernel} root=UUID={uuid} ro single console=tty0 console=ttyS0,115200
}}
"""
    with open(os.path.join(grub_dir, "grub.cfg"), "w") as f:
        f.write(cfg)


def write_fstab(part, label="ZACKROOT"):
    fstab_path = os.path.join(TARGET, "etc/fstab")
    with open(fstab_path, "w") as f:
        f.write(f"LABEL={label}  /  ext4  defaults  0  1\n")
        f.write("proc  /proc  proc  defaults  0  0\n")
        f.write("sysfs /sys sysfs defaults  0  0\n")
        f.write("devpts /dev/pts devpts gid=5,mode=620 0 0\n")
        f.write("tmpfs /dev/shm tmpfs defaults 0 0\n")


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
    """After nix-env install inside target, symlink real store binaries into
    /usr/local/bin since the nix-env profile activation is unreliable
    without a pty (see build notes) - find the real derivation paths."""
    nix_store = os.path.join(TARGET, "nix/store")
    if not os.path.isdir(nix_store):
        return
    bin_dir = os.path.join(TARGET, "usr/local/bin")
    os.makedirs(bin_dir, exist_ok=True)
    for entry in os.listdir(nix_store):
        bin_path = os.path.join(nix_store, entry, "bin")
        if os.path.isdir(bin_path):
            for name in names:
                candidate = os.path.join(bin_path, name)
                if os.path.isfile(candidate) or os.path.islink(candidate):
                    link_path = os.path.join(bin_dir, name)
                    if not os.path.exists(link_path):
                        os.symlink(f"/nix/store/{entry}/bin/{name}", link_path)


def install_init(choice):
    if choice == "sysvinit":
        log("Keeping sysvinit (default, already configured).")
        return
    log("Installing runit via Nix (experimental init option)...")
    export = (
        f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
        f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; "
    )
    chroot_run(export + "nix-env -iA nixpkgs.runit")
    link_nix_bins(["runit", "runsvdir", "runsv", "sv", "chpst", "svlogd"])

    etc_runit = os.path.join(TARGET, "etc/runit")
    os.makedirs(etc_runit, exist_ok=True)
    os.makedirs(os.path.join(TARGET, "etc/service/agetty-tty1"), exist_ok=True)

    with open(os.path.join(etc_runit, "1"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            "mount -t proc proc /proc 2>/dev/null\n"
            "mount -t sysfs sysfs /sys 2>/dev/null\n"
            "mount -t devtmpfs devtmpfs /dev 2>/dev/null\n"
            "mount -a\n"
            "hostname -F /etc/hostname\n"
            "exit 0\n"
        )
    with open(os.path.join(etc_runit, "2"), "w") as f:
        f.write("#!/bin/sh\nexec runsvdir -P /etc/service\n")
    with open(os.path.join(etc_runit, "3"), "w") as f:
        f.write("#!/bin/sh\nsv -w 5 force-stop /etc/service/*\n")
    for fname in ("1", "2", "3"):
        os.chmod(os.path.join(etc_runit, fname), 0o755)

    getty_run = os.path.join(TARGET, "etc/service/agetty-tty1/run")
    with open(getty_run, "w") as f:
        f.write("#!/bin/sh\nexec agetty tty1 38400 linux\n")
    os.chmod(getty_run, 0o755)

    # swap init - keep old sysvinit as fallback at /sbin/init.sysvinit
    sbin_init = os.path.join(TARGET, "sbin/init")
    if os.path.exists(sbin_init) and not os.path.islink(sbin_init):
        shutil.copy(sbin_init, sbin_init + ".sysvinit")
    with open(sbin_init, "w") as f:
        f.write("#!/bin/sh\nexec /usr/local/bin/runit\n")
    os.chmod(sbin_init, 0o755)


def cleanup_mounts():
    for d in ("dev", "sys", "proc"):
        run(f"umount -lf {TARGET}/{d}", check=False)
    run(f"umount -lf {TARGET}", check=False)


def full_install(disk, wm_choice, init_choice, hostname, root_password,
                  username=None, user_password=None):
    part = partition_disk(disk)
    format_partition(part)
    mount_target(part)
    try:
        copy_system()
        write_fstab(part)
        set_hostname(hostname)
        set_root_password(root_password)
        if username:
            create_user(username, user_password or root_password)
        install_wm(wm_choice)
        install_init(init_choice)
        install_bootloader(disk, part)
    finally:
        cleanup_mounts()
    log("Install complete! Remove installation media and reboot.")
