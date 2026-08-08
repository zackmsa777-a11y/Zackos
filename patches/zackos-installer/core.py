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


def wait_for_network(host="cache.nixos.org", attempts=15, delay=2):
    """Block until DNS resolution for `host` succeeds, or give up.

    BUG FIX (2026-08-08): install_wm() called nix-env immediately after
    boot, with no wait for dhcpcd to actually finish negotiating a lease
    and writing /etc/resolv.conf. Reproduced live: nix-env's fetch failed
    silently with "Could not resolve hostname", link_nix_bins() then found
    nothing in the Nix store and (since desktop packages were only ever
    treated as "optional") logged a one-line warning and let the install
    continue as if nothing was wrong - the user got a fully "successful"
    install with no i3, no Xorg, no xterm at all. Give the network time to
    come up and confirm DNS actually resolves before ever trying to fetch
    packages, so real failures surface as a real error instead of a silent
    no-op.
    """
    import socket
    for attempt in range(1, attempts + 1):
        try:
            socket.getaddrinfo(host, 443)
            return True
        except socket.gaierror:
            log(f"Waiting for network/DNS to be ready ({attempt}/{attempts})...")
            import time
            time.sleep(delay)
    log(f"WARNING: network/DNS never became ready (tried to resolve {host}); "
        "package installs will likely fail.")
    return False


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
    # BUG FIX (2026-08-05): the copied base system's /etc/resolv.conf is
    # whatever existed at tar-copy time -- often stale/empty, since dhcpcd
    # writes the live resolv.conf asynchronously. Any chroot_run that needs
    # network (e.g. `nix-env -iA nixpkgs.runit`) then fails DNS resolution
    # silently as "unable to download" errors, even though the live env
    # itself has working network+DNS (confirmed: install FAILED on runit
    # nix-env fetch, chpst/runitd never installed, while host resolv.conf
    # had a valid nameserver the whole time). Always resync the live
    # resolv.conf into the target immediately before every chroot_run.
    try:
        shutil.copyfile("/etc/resolv.conf", os.path.join(TARGET, "etc/resolv.conf"))
    except OSError:
        pass
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

    # BUG FIX (2026-08-08): this whole function used to be "best effort" -
    # link_nix_bins() was called with require_all=False (its default), so
    # if the nix-env fetch below failed for ANY reason (most commonly: the
    # transient DHCP/DNS race described in wait_for_network()'s docstring),
    # the installer printed one easy-to-miss "Optional provider binaries
    # unavailable" line and then reported "Install complete!" anyway.
    # Reproduced live end-to-end: user got a fully installed, "successful"
    # system with no i3, no Xorg, no xterm, no dmenu at all - not even a
    # bad config, the packages were simply never there. The WM the user
    # explicitly chose is not optional; fail loudly instead.
    wait_for_network()
    log("Installing i3-wm + Xorg + input/session deps via Nix "
        "(needs network access)...")
    export = (
        f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
        f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; "
    )
    # Full package set the shipped xorg.conf / .xinitrc / i3 config below
    # actually depend on - not just the 5 packages that were being
    # installed before while the config referenced far more (i3status,
    # dmenu, a real video driver + its vbe/int10/shadow helper modules,
    # libinput, dbus, a font so i3bar/dmenu don't render tofu boxes, and
    # eudev so libinput has a live device database instead of failing
    # with "udev device never initialized").
    # NOTE: no eudev/dbus here - scripts_chroot/udev.sh and
    # blfs-69-dbus.sh already build real udevd + dbus-daemon into /usr as
    # part of the base LFS/BLFS system. Pulling a second copy from nix
    # would be redundant and risks two udev daemons fighting over /run/udev.
    packages = [
        "nixpkgs.i3", "nixpkgs.i3status", "nixpkgs.dmenu", "nixpkgs.xterm",
        "nixpkgs.rxvt-unicode",
        "nixpkgs.xorg-server", "nixpkgs.xinit", "nixpkgs.xauth",
        "nixpkgs.xf86-video-vesa", "nixpkgs.xf86-input-libinput",
        "nixpkgs.dejavu_fonts",
        "nixpkgs.feh", "nixpkgs.scrot", "nixpkgs.xdotool", "nixpkgs.xclip",
        "nixpkgs.i3lock",
    ]
    ok = False
    for attempt in range(1, 4):
        result = chroot_run(export + "NIXPKGS_ALLOW_UNSUPPORTED_SYSTEM=1 nix-env -iA "
                             + " ".join(packages))
        if result.returncode == 0:
            ok = True
            break
        log(f"nix-env install attempt {attempt}/3 failed, retrying...")
    if not ok:
        raise InstallError(
            "Failed to install the i3/Xorg package set after 3 attempts - "
            "check network connectivity inside the chroot (see wait_for_network)."
        )
    # These are the packages the config files below actually invoke; if
    # any are missing the WM will render but be broken (no cursor, no
    # bar, tofu boxes), so require every one of them instead of quietly
    # continuing like the old "optional" path did.
    link_nix_bins([
        "i3", "i3bar", "i3status", "xterm", "urxvt", "dmenu", "startx",
        "Xorg", "xauth", "i3lock", "feh", "scrot", "xdotool", "xclip",
    ], require_all=True)

    write_xorg_conf()
    write_xinitrc()
    write_i3_config()


def write_xorg_conf():
    """Ship a video config that actually starts on real/virtual hardware
    with no KMS/DRM (this kernel has no vboxvideo/bochs-drm compiled in).

    BUG FIX (2026-08-08): the installed target shipped with NO
    /etc/X11/xorg.conf at all - the one reference config that existed in
    this repo (patches/etc-configs/xorg.conf) was never actually copied
    anywhere by build_installer_iso.sh. Xorg's pure autoconfig then found
    no usable KMS device and died with "no screens found" on every fresh
    install. Confirmed live: explicitly loading vesa + its vbe/int10/
    shadow helper modules (vesa needs all three - it fails with a
    different "undefined symbol" for each one missing) is what actually
    gets a screen up in both QEMU and VirtualBox with this kernel.
    Deliberately no InputDevice/ServerFlags section here: AutoAddDevices
    stays at its default (on) so Xorg's libinput driver picks up mouse
    and keyboard automatically via udev - hardcoding /dev/input/eventN or
    the legacy /dev/input/mice path was the single biggest time-sink in
    manual live debugging (both are guesses that silently break the
    moment the device enumeration order differs).
    """
    xorg_conf = os.path.join(TARGET, "etc/X11/xorg.conf")
    os.makedirs(os.path.dirname(xorg_conf), exist_ok=True)
    with open(xorg_conf, "w") as f:
        f.write(
            "Section \"Module\"\n"
            "    Load \"vbe\"\n"
            "    Load \"int10\"\n"
            "    Load \"shadow\"\n"
            "EndSection\n\n"
            "Section \"Device\"\n"
            "    Identifier \"Generic Video Driver\"\n"
            "    Driver \"vesa\"\n"
            "EndSection\n\n"
            "Section \"Screen\"\n"
            "    Identifier \"Screen0\"\n"
            "    Device \"Generic Video Driver\"\n"
            "EndSection\n\n"
            "Section \"ServerLayout\"\n"
            "    Identifier \"Default Layout\"\n"
            "    Screen 0 \"Screen0\" 0 0\n"
            "EndSection\n"
        )


def write_xinitrc():
    """.xinitrc must launch i3 inside a D-Bus session.

    BUG FIX (2026-08-08): the old .xinitrc was a bare `exec i3` with no
    D-Bus session at all. Reproduced live: nm-applet/dconf/notifications
    all failed with "DBUS_SESSION_BUS_ADDRESS is blank" and i3's own
    exit/lock menu tools that shell out to notify-send silently no-op'd.
    dbus-launch --exit-with-x11 starts a session bus and tears it down
    when i3 exits, with zero extra service wiring needed.
    """
    content = "#!/bin/sh\nexec dbus-launch --exit-with-x11 i3\n"
    for rel in ("etc/skel/.xinitrc", "root/.xinitrc"):
        path = os.path.join(TARGET, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)


def write_i3_config():
    """Ship a real i3 config (adapted from github.com/TxGVNN/i3-config,
    per project standing instructions) instead of leaving ~/.config/i3
    empty.

    BUG FIX (2026-08-08): with no config file present, i3 shows its
    first-run setup wizard every single launch instead of a normal
    session - confirmed live as the unreadable "unreadable dialog box"
    the user kept hitting. The upstream TxGVNN config also hardcodes
    `i3bar_command /usr/bin/i3bar` (nix packages never install to
    /usr/bin - confirmed live: i3bar/child.c reported binary not found
    at that literal path even though it was on $PATH) and calls
    `systemctl poweroff/reboot/suspend/hibernate`, which do not exist on
    this runit-based, non-systemd system. Adapted here: i3bar resolved
    via $PATH, i3status instead of the upstream's i3blocks (avoids
    needing a second custom config file this installer doesn't ship),
    power/reboot routed through this project's own /sbin/poweroff and
    /sbin/reboot wrappers (see install_shutdown_wrappers()), and
    suspend/hibernate dropped (no ACPI sleep support in this build).
    """
    config = """# ZackOS i3 config - adapted from github.com/TxGVNN/i3-config
set $mod Mod4

font pango:DejaVu Sans Mono 10
floating_modifier $mod
focus_follows_mouse no

# terminal / launcher
bindsym $mod+Return exec urxvt
bindsym $mod+Shift+d exec dmenu_run
bindsym $mod+Shift+q kill
bindsym $mod+x border toggle

# focus (vim-style + arrows)
bindsym $mod+j focus left
bindsym $mod+k focus down
bindsym $mod+l focus up
bindsym $mod+semicolon focus right
bindsym $mod+Left focus left
bindsym $mod+Down focus down
bindsym $mod+Up focus up
bindsym $mod+Right focus right

# move
bindsym $mod+Shift+j move left
bindsym $mod+Shift+k move down
bindsym $mod+Shift+l move up
bindsym $mod+Shift+semicolon move right

# layout
bindsym $mod+h split h
bindsym $mod+v split v
bindsym $mod+f fullscreen toggle
bindsym $mod+s layout stacking
bindsym $mod+w layout tabbed
bindsym $mod+e layout toggle split
bindsym $mod+Shift+space floating toggle
bindsym $mod+space focus mode_toggle
bindsym $mod+a focus parent

# workspaces
bindsym $mod+1 workspace number 1
bindsym $mod+2 workspace number 2
bindsym $mod+3 workspace number 3
bindsym $mod+4 workspace number 4
bindsym $mod+5 workspace number 5
bindsym $mod+Shift+1 move container to workspace number 1
bindsym $mod+Shift+2 move container to workspace number 2
bindsym $mod+Shift+3 move container to workspace number 3
bindsym $mod+Shift+4 move container to workspace number 4
bindsym $mod+Shift+5 move container to workspace number 5

# resize mode
mode "resize" {
    bindsym j resize shrink width 5 px or 5 ppt
    bindsym k resize grow height 5 px or 5 ppt
    bindsym l resize shrink height 5 px or 5 ppt
    bindsym semicolon resize grow width 5 px or 5 ppt
    bindsym Return mode "default"
    bindsym Escape mode "default"
}
bindsym $mod+r mode "resize"

# power menu - routed through our own runit-safe wrappers, no systemd
mode "(l)ock (r)eboot (p)oweroff (e)xit-i3" {
    bindsym l exec i3lock -c 2E3440, mode "default"
    bindsym r exec /sbin/reboot
    bindsym p exec /sbin/poweroff
    bindsym e exec i3-msg exit
    bindsym Return mode "default"
    bindsym Escape mode "default"
}
bindsym $mod+Shift+e mode "(l)ock (r)eboot (p)oweroff (e)xit-i3"

bindsym $mod+Shift+c reload
bindsym $mod+Shift+r restart

bar {
    i3bar_command i3bar
    status_command i3status
    font pango:DejaVu Sans Mono 10
}
"""
    for rel in ("etc/skel/.config/i3/config", "root/.config/i3/config"):
        path = os.path.join(TARGET, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(config)


def link_nix_bins(names, require_all=False):
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
    if missing and require_all:
        raise InstallError("Provider binaries were not found in the Nix store: " + ", ".join(missing))
    if missing:
        log("Optional provider binaries unavailable: " + ", ".join(missing))


def install_init(choice, part=None):
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
    link_nix_bins(["runit", "runsvdir", "runsv", "sv", "chpst", "svlogd"], require_all=True)

    etc_runit = os.path.join(TARGET, "etc/runit")
    service_dir = os.path.join(TARGET, "etc/service")
    os.makedirs(etc_runit, exist_ok=True)
    os.makedirs(service_dir, exist_ok=True)

    # Stage 1 mounts the virtual filesystems that SysVinit used to mount.
    # The kernel mounts root read-only (confirmed live: "VFS: Mounted root
    # (ext4 filesystem) readonly"). SysVinit's own rcS-style scripts always
    # remount it rw before anything else runs; our runit stage 1 was missing
    # that step entirely. Without it, every later write to the root fs
    # silently fails - including runsv's own supervise/lock file, reproduced
    # live as:
    #   "runsv agetty-tty1: fatal: unable to open supervise/lock: file does
    #    not exist"
    #
    # A first attempt added a bare `mount -o remount,rw /` as the very first
    # line of stage 1, before anything else - and it STILL failed live, with
    # the remount itself throwing "Read-only file system" (confirmed by
    # instrumenting stage 1 to log to a file: the log write itself failed
    # with that exact error, proving the remount silently no-op'd). Root
    # cause: bare `mount -o remount,rw /` with no explicit device performs
    # the remount by looking up the current mount's source+fstype via
    # /proc/self/mountinfo - but /proc isn't mounted yet at that point in
    # stage 1 (it's mounted on a LATER line), so mount(8) can't resolve the
    # existing mount and the remount is dropped. Confirmed by reproducing
    # both ways live in QEMU: remount fails when attempted before /proc is
    # mounted, succeeds when /proc is already mounted first.
    # Fix: mount /proc FIRST, then do the rw remount (still bare - it will
    # now resolve via /proc/self/mountinfo), then the rest.
    # BUG FIX (2026-08-07): even with /proc mounted first (above), the bare
    # `mount -o remount,rw /` still silently no-op'd on a from-scratch
    # install/boot cycle (reproduced live: root stayed read-only, runsv's
    # supervise/lock writes kept failing). Root cause: bare remount with no
    # explicit source resolves the CURRENT mount's device via
    # /proc/self/mountinfo by matching against the udev-populated
    # /dev/disk/by-label/* symlink for the fstab LABEL - but this early in
    # boot (before any udev/mdev run), that symlink doesn't exist yet, so
    # the lookup fails and the remount is dropped with no visible error.
    # Fix: pass the actual block device path explicitly (known at
    # install-time, threaded in as `part`) instead of relying on label
    # resolution. Falls back to bare `/` if `part` wasn't provided, so this
    # never regresses call sites that don't have it.
    remount_target = part if part else ""
    with open(os.path.join(etc_runit, "1"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            "mount -t proc proc /proc 2>/dev/null || true\n"
            f"mount -o remount,rw {remount_target} / 2>/dev/null || true\n"
            "mount -t sysfs sysfs /sys 2>/dev/null || true\n"
            "mount -t devtmpfs devtmpfs /dev 2>/dev/null || true\n"
            "mount -t devpts devpts /dev/pts 2>/dev/null || true\n"
            "mount -t tmpfs tmpfs /run 2>/dev/null || true\n"
            "mount -a 2>/dev/null || true\n"
            "hostname -F /etc/hostname 2>/dev/null || true\n"
            "exit 0\n"
        )
    # Stage 2 must use the absolute provider path; PID1 has no reliable PATH.
    # runsvdir itself launches "runsv" (and runsv launches per-service "run"
    # scripts) via a PATH-based execvp, not an absolute path - confirmed
    # against runit's runsvdir.c (svdir.c calls exec_prog(name) which uses
    # execvp with the bare command name). With no PATH set for PID1's stage
    # 2, that exec fails ENOENT ("file does not exist") even though runsv
    # itself was correctly symlinked - reproduced live: runsvdir looped
    # forever logging "fatal: unable to start runsv agetty-tty1: file does
    # not exist" while the service's run script was present and executable.
    # Export PATH explicitly so runsvdir's children can resolve.
    runit_path_export = "export PATH=/usr/local/bin:/usr/local/sbin:/bin:/sbin:/usr/bin:/usr/sbin\n"
    # BUG FIX (2026-08-08): mouse/keyboard never worked under X on this
    # target - root cause was that udevd was NEVER STARTED AT ALL under
    # runit. The base LFS/BLFS build already compiles a real udev (see
    # scripts_chroot/udev.sh -> /usr/sbin/udevd, symlinked from the
    # udevadm multi-call binary per the upstream LFS book convention -
    # invoking it under that name activates persistent daemon mode
    # instead of the one-shot CLI tool), but SysVinit's own udev
    # bootscript is what starts it normally, and switching init to runit
    # here never carried that step over. Confirmed live: without a
    # running udevd, Xorg's libinput driver logs "udev device never
    # initialized" and neither mouse nor keyboard produce any input,
    # regardless of which xorg.conf is in place. Start it once, backgrounded,
    # before runsvdir takes over, then let udevadm populate the initial
    # device set so it is ready before any X session is later launched
    # from a login shell.
    stage2_lines = (
        "#!/bin/sh\n"
        + runit_path_export
        + "if [ -x /usr/sbin/udevd ]; then\n"
        "  /usr/sbin/udevd --daemon 2>/dev/null &\n"
        "  udevadm trigger 2>/dev/null || true\n"
        "  udevadm settle --timeout=10 2>/dev/null || true\n"
        "fi\n"
        "exec /usr/local/bin/runsvdir -P /etc/service\n"
    )
    with open(os.path.join(etc_runit, "2"), "w") as f:
        f.write(stage2_lines)
    # BUG FIX (2026-08-08): plain `reboot`/`poweroff`/`shutdown` (no -f)
    # hung forever, forcing hard VirtualBox resets - which in turn
    # corrupted /etc/shadow writes badly enough that login then rejected
    # the correct password on every subsequent boot ("Login incorrect"
    # even with byte-identical credentials). Root cause: the reboot/halt/
    # poweroff/shutdown binaries on this system are SysVinit's own
    # (built expecting to write a runlevel request to /run/initctl and
    # have SysVinit's PID1 read and act on it) - but PID1 here is runit,
    # which never creates or reads that fifo, so those commands just
    # blocked waiting for a response that will never come. Stage 3 itself
    # only stopped services and returned - it never performed the actual
    # power action either. Fixed properly in install_shutdown_wrappers():
    # /sbin/reboot, /sbin/halt, /sbin/poweroff, /sbin/shutdown are
    # replaced with tiny scripts that stop services, sync, remount root
    # read-only, then trigger the kernel directly via
    # /proc/sysrq-trigger - no init IPC protocol involved at all, so it
    # cannot hang regardless of which init is PID1. Stage 3 (still run via
    # those wrappers) keeps the service force-stop loop and adds an
    # explicit sync as defense in depth.
    with open(os.path.join(etc_runit, "3"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            + runit_path_export
            + "for service in /etc/service/*; do\n"
            "  [ -d \"$service\" ] || continue\n"
            "  /usr/local/bin/sv -w 5 force-stop \"$service\" 2>/dev/null || true\n"
            "done\n"
            "sync\n"
        )
    for fname in ("1", "2", "3"):
        os.chmod(os.path.join(etc_runit, fname), 0o755)

    def write_service(name, command):
        directory = os.path.join(service_dir, name)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "run"), "w") as f:
            f.write("#!/bin/sh\nexec " + command + "\n")
        os.chmod(os.path.join(directory, "run"), 0o755)

    # BUG FIX (2026-08-07): login always rejected the CORRECT root password
    # ("Login incorrect" every time), even after confirming byte-for-byte
    # that /etc/shadow's crypt() hash matched and getspnam() worked fine in
    # isolation. Root cause: agetty is exec'd directly as a runsv service
    # child (runsvdir -> runsv -> this run script), which is never a
    # session leader with no controlling terminal of its own - agetty's own
    # internal setsid()/TIOCSCTTY handling did not recover from that in
    # this environment, so `login` (its child) never actually owned
    # ttyS0/tty1 as a controlling terminal even though the prompt displayed
    # fine. Confirmed live: wrapping the exact same agetty invocation in
    # `setsid -c` (new session + explicitly claim the current tty as
    # controlling) fixed authentication immediately with the unmodified
    # password - proving this was a tty/session bug, never a password one.
    # ttyS0 is required for QEMU serial verification; tty1 is required on hardware.
    write_service("agetty-tty1", "setsid -c /sbin/agetty tty1 38400 linux")
    write_service("agetty-ttyS0", "setsid -c /sbin/agetty -L 115200 ttyS0 vt100")

    # runit is PID1; preserve SysVinit as a recovery binary.
    # NOTE: nixpkgs' runit derivation installs everything into $out/bin
    # only (confirmed against the upstream default.nix - installPhase does
    # `mkdir -p $out/bin; cp -t $out/bin $(< ../package/commands)`, no sbin
    # output exists at all). link_nix_bins() therefore only ever populates
    # usr/local/bin/runit on the target, never usr/local/sbin/runit.
    # Hard-coding the sbin path here would leave /sbin/init exec'ing a file
    # that never exists -> kernel panic "no working init found" on first
    # boot. Resolve the real installed path instead of assuming one.
    runit_bin = None
    for candidate in ("usr/local/bin/runit", "usr/local/sbin/runit"):
        full = os.path.join(TARGET, candidate)
        if os.path.exists(full) or os.path.islink(full):
            runit_bin = "/" + candidate
            break
    if not runit_bin:
        raise InstallError(
            "runit binary not found under /usr/local/bin or /usr/local/sbin "
            "on the target after install - refusing to write /sbin/init "
            "(this would panic on boot with 'no working init found')."
        )
    sbin_init = os.path.join(TARGET, "sbin/init")
    if os.path.exists(sbin_init) and not os.path.islink(sbin_init):
        shutil.copy2(sbin_init, sbin_init + ".sysvinit")
    with open(sbin_init, "w") as f:
        f.write(f"#!/bin/sh\nexec {runit_bin}\n")
    os.chmod(sbin_init, 0o755)

    install_shutdown_wrappers()


def install_shutdown_wrappers():
    """Replace /sbin/{reboot,halt,poweroff,shutdown} with init-agnostic
    scripts so they work under runit (see the stage-3 bug-fix note above
    for the full root cause). Backs up whatever SysVinit-provided binary
    was there first, matching the existing /sbin/init.sysvinit convention.
    """
    sbin = os.path.join(TARGET, "sbin")
    os.makedirs(sbin, exist_ok=True)

    def _backup(name):
        p = os.path.join(sbin, name)
        if os.path.exists(p) and not os.path.islink(p):
            shutil.copy2(p, p + ".sysvinit")

    stop_services = """for service in /etc/service/*; do
  [ -d "$service" ] || continue
  /usr/local/bin/sv -w 5 force-stop "$service" 2>/dev/null || true
done
sync
mount -o remount,ro / 2>/dev/null || true
echo 1 > /proc/sys/kernel/sysrq 2>/dev/null || true
"""

    def _write(name, sysrq_char):
        _backup(name)
        p = os.path.join(sbin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + stop_services +
                    f"echo {sysrq_char} > /proc/sysrq-trigger\n")
        os.chmod(p, 0o755)

    _write("reboot", "b")     # SysRq b = immediate reboot
    _write("poweroff", "o")   # SysRq o = power off
    _write("halt", "o")       # no separate "halt and don't power off" via
                              # sysrq; poweroff is the closest safe stop.

    shutdown_path = os.path.join(sbin, "shutdown")
    _backup("shutdown")
    with open(shutdown_path, "w") as f:
        f.write("""#!/bin/sh
# Minimal init-agnostic shutdown(8): only cares whether -r (reboot) was
# requested; everything else (delay, -h, -P, 'now', etc.) is accepted and
# ignored since this system has no multi-user warning/wall step to perform.
case " $* " in
  *' -r '*) exec /sbin/reboot ;;
  *) exec /sbin/poweroff ;;
esac
""")
    os.chmod(shutdown_path, 0o755)


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
        install_init(profile.init, part)
        install_bootloader(disk, part)
    finally:
        cleanup_mounts()
    log("Install complete! Remove installation media and reboot.")
