#!/usr/bin/env python3
"""
ZackOS Installer - core logic shared by TUI and Manual front-ends.
Designed to run from within the booted live environment (real kernel,
real mount()/chroot() available - this is NOT the build sandbox).
"""
import configparser
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import urllib.request
import shutil

from providers.profile import InstallerProfile
from providers.registry import validate_profile

TARGET = "/mnt/target"
DESKTOP_SHELL = "/nix/var/nix/profiles/default/bin/zsh"
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


def run(cmd, check=True, input_text=None, capture=False, stdin=None):
    """Run a shell command, echoing it first (manual-mode transparency)."""
    if isinstance(cmd, list):
        printable = " ".join(cmd)
    else:
        printable = cmd
    log(f"$ {printable}")
    kwargs = {"stdin": stdin}
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


def disk_inventory():
    """Offer writable whole disks with no mounted, live-media or persistence volumes."""
    columns = "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,MAJ:MIN,RO,LABEL,FSTYPE,MOUNTPOINTS"
    tree = json.loads(subprocess.check_output(["lsblk", "--json", "--bytes", "-o", columns]))

    def in_use(node):
        return (node.get("label") == "ZACKPERSIST" or node.get("fstype") == "iso9660"
                or any(node.get("mountpoints") or []) or
                any(in_use(child) for child in node.get("children", [])))

    disks = []
    for node in tree["blockdevices"]:
        if node["type"] != "disk" or node["ro"] or not node["size"] or in_use(node):
            continue
        name = node["name"]
        if not re.fullmatch(r"(?:sd|vd|xvd|hd)[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+", name):
            continue
        seq = Path("/sys/class/block", name, "diskseq")
        identity = [node["maj:min"], node["size"], node["serial"] or "", seq.read_text().strip() if seq.exists() else ""]
        fallback = "VirtIO disk" if name.startswith("vd") else "Storage disk"
        disks.append({"path": node["path"], "bytes": node["size"], "model": (node["model"] or "").strip() or fallback,
                      "identity": identity})
    return disks


def list_disks():
    return [(d["path"], f'{d["bytes"] / 1024**3:.1f}G', d["model"]) for d in disk_inventory()]


EFI_ASSETS = Path("/usr/local/share/zackos-efi")
ESP_MIB = 260


def partition_path(disk, number):
    return f"{disk}{'p' if disk[-1].isdigit() else ''}{number}"


def partition_disk(disk, identity=None, efi=False):
    """Recheck the actual device immediately before writing its partition table."""
    choice = next((d for d in disk_inventory() if d["path"] == disk), None)
    if choice is None or (identity is not None and choice["identity"] != identity):
        raise InstallError("The selected disk changed or is in use. Select an available disk again.")
    run(["wipefs", "-a", disk])
    if efi:
        layout = f"label: gpt\nsize={ESP_MIB}MiB, type=U\ntype=L\n"
    else:
        layout = "label: dos\nstart=2048, type=83, bootable\n"
    run(["sfdisk", disk], input_text=layout)
    run(["udevadm", "settle", "--timeout=15"])
    return partition_path(disk, 2 if efi else 1)


def format_partition(part, label="ZACKROOT"):
    run(f"mke2fs -F -t ext4 -L {label} {part}")


def mount_target(part):
    os.makedirs(TARGET, exist_ok=True)
    run(f"mount {part} {TARGET}")


MEDIA_BASE = Path("/mnt/zackos-base")
MEDIA_DESKTOP = Path("/mnt/zackos-desktop")
MEDIA_INFO = MEDIA_DESKTOP / "usr/local/share/zackos-media"


def bundled_desktop(profile):
    return profile.desktop == "i3" and MEDIA_DESKTOP.is_mount() and (MEDIA_INFO / "registration").is_file()


def check_capacity(disk, bundled, efi=False):
    sources = [str(MEDIA_BASE) if MEDIA_BASE.is_mount() else "/"]
    if bundled:
        sources.append(str(MEDIA_DESKTOP / "nix/store"))
    info = next((d for d in disk_inventory() if d["path"] == disk), None)
    if not info:
        raise InstallError("The installation disk is unavailable")
    omit = ["--exclude=" + path for path in ("proc", "sys", "dev", "tmp", "run", "mnt", "media")]
    used = subprocess.check_output(["du", "-sb", *omit, *sources], text=True)
    needed = sum(int(line.split()[0]) for line in used.splitlines()) * 1.15
    if efi:
        needed += ESP_MIB * 1024**2
    if info["bytes"] < needed:
        raise InstallError(f"The selected disk needs at least {needed / 1024**3:.1f} GiB for this system.")


def copy_system(exclude_extra=None):
    # The graphical live user's home, passwords and mutable overlay stay on the media.
    source = MEDIA_BASE if MEDIA_BASE.is_mount() else Path("/")
    excludes = ["proc", "sys", "dev", "tmp", "run", "mnt", "media", "root/persist_test.txt"]
    excludes.extend(name.lstrip("/") for name in (exclude_extra or ()))
    log("Copying the base system to the target disk...")
    pack = ["tar", "--one-file-system", *("--exclude=./" + name for name in excludes),
            "-C", str(source), "-cpf", "-", "."]
    with subprocess.Popen(pack, stdout=subprocess.PIPE) as reader:
        try:
            run(["tar", "-C", TARGET, "-xpf", "-"], stdin=reader.stdout)
        finally:
            reader.stdout.close()
        if reader.wait():
            raise InstallError("Could not read the base system from the installation media")
    for name in ("proc", "sys", "dev", "tmp", "run", "mnt", "media"):
        (Path(TARGET) / name).mkdir(exist_ok=True)
    (Path(TARGET) / "tmp").chmod(0o1777)
    if source == MEDIA_BASE:
        payload = Path(__file__).resolve().parent
        shutil.copytree(payload, Path(TARGET, "usr/local/lib/zackos-installer"), dirs_exist_ok=True,
                        symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(payload / "zackinstall", Path(TARGET, "usr/local/bin/zackinstall"))


def populate_desktop():
    profile = (MEDIA_INFO / "profile").read_text().strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[A-Za-z0-9+._?-]+", profile):
        raise InstallError("Invalid desktop profile on the installation media")
    log("Copying the desktop packages from the installation media...")
    target_store = Path(TARGET, "nix/store")
    paths = sorted((MEDIA_DESKTOP / "nix/store").iterdir())
    for i in range(0, len(paths), 100):
        subprocess.run(["cp", "-a", "-t", target_store, *paths[i:i+100]], check=True)
    if chroot_run("true").returncode:
        raise InstallError("Could not enter the target system")
    nix = "/root/.nix-profile/bin/"
    with (MEDIA_INFO / "registration").open("rb") as registration:
        run(["chroot", TARGET, nix + "nix-store", "--load-db"], stdin=registration)
    run(["chroot", TARGET, nix + "nix-env", "--profile", "/nix/var/nix/profiles/per-user/root/profile",
         "--set", profile])


def chroot_run(cmd):
    """Run a command inside the target chroot with proc/sys/dev bind-mounted."""
    for src, dst in (("/proc", "proc"), ("/sys", "sys"), ("/dev", "dev")):
        dst_path = os.path.join(TARGET, dst)
        if not os.path.ismount(dst_path):
            run(["mount", "--bind", src, dst_path])
    # BUG FIX (2026-08-08): `mount --bind /dev target/dev` only binds that
    # single mountpoint - it does NOT carry across /dev/pts, which is its
    # own separate devpts mount on the live system. Any chroot_run that
    # allocates a pty (nix-env's own progress/substituter multiplexing
    # does this for every package it fetches) then fails with "opening
    # pseudoterminal master: No such device", which install_wm()'s retry
    # loop dutifully retried 3 times and then correctly hard-failed on -
    # but the real fix is just giving the chroot a working /dev/pts.
    os.makedirs(os.path.join(TARGET, "dev/pts"), exist_ok=True)
    if not os.path.ismount(os.path.join(TARGET, "dev/pts")):
        run(["mount", "--bind", "/dev/pts", os.path.join(TARGET, "dev/pts")])
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


def block_id(part, field):
    value = subprocess.check_output(["blkid", "-s", field, "-o", "value", part], text=True).strip()
    if not re.fullmatch(r"[0-9a-fA-F-]+", value):
        raise InstallError(f"Cannot read {field} from {part}")
    return value


def install_bootloader(disk, part, label="ZACKROOT", esp=None):
    log("Installing GRUB bootloader...")
    if esp:
        boot = Path(TARGET, "boot")
        loader = boot / "efi/EFI/BOOT"
        loader.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EFI_ASSETS / "BOOTX64.EFI", loader / "BOOTX64.EFI")
        (loader / "zackos.cfg").write_text(
            f"search --no-floppy --fs-uuid --set=root {block_id(part, 'UUID')}\n"
            "configfile /boot/grub/grub.cfg\n")
        fonts = boot / "grub/fonts"
        fonts.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EFI_ASSETS / "unicode.pf2", fonts / "unicode.pf2")
        write_grub_cfg(part, label)
        return
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
    """Write the boot entries and install the optional GRUB graphics setup.
    The splash configuration keeps a console fallback for text-only machines.

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

    fs_uuid = block_id(part, "UUID")
    partuuid = block_id(part, "PARTUUID")

    grub_dir = os.path.join(TARGET, "boot/grub")
    os.makedirs(grub_dir, exist_ok=True)
    branding = Path(__file__).with_name("branding")
    for asset in ("splash.cfg", "splash.png"):
        shutil.copyfile(branding / asset, Path(grub_dir) / asset)
    shutil.copytree(branding / "grub", Path(grub_dir) / "zackos", dirs_exist_ok=True)
    logo_dir = Path(TARGET, "usr/share/pixmaps")
    logo_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(branding / "zackos.svg", logo_dir / "zackos.svg")
    graphical = (Path(TARGET) / "etc/lightdm/lightdm.conf").is_file()
    boot_args = " quiet loglevel=3 init=/usr/local/lib/zackos-installer/boot/installed-init" if graphical else ""
    graphics_handoff = "    set gfxpayload=keep\n" if graphical else ""
    cfg = f"""# ZackOS boot menu.
set timeout=5
set default=0

insmod part_msdos
insmod part_gpt
insmod ext2
search --no-floppy --fs-uuid --set=root {fs_uuid}
terminal_output console
source /boot/grub/splash.cfg

menuentry "Start ZackOS" {{
{graphics_handoff}    linux /boot/{kernel} $zack_video root=PARTUUID={partuuid} ro{boot_args} console=tty0 console=ttyS0,115200
}}

menuentry "Recovery shell" {{
    linux /boot/{kernel} $zack_video root=PARTUUID={partuuid} ro single console=tty0 console=ttyS0,115200
}}
"""
    with open(os.path.join(grub_dir, "grub.cfg"), "w") as f:
        f.write(cfg)

def write_fstab(part, label="ZACKROOT", esp=None):
    fstab_path = os.path.join(TARGET, "etc/fstab")
    with open(fstab_path, "w") as f:
        # Keep the LFS virtual-filesystem mounts complete: SysVinit's
        # mountvirtfs service expects /run, devtmpfs, and cgroup2 here.
        # Without them an installed root booted with the kernel's default
        # read-only flag stops before getty starts.
        f.write(f"UUID={block_id(part, 'UUID')}  /  ext4  defaults  1  1\n")
        if esp:
            f.write(f"PARTUUID={block_id(esp, 'PARTUUID')} /boot/efi vfat umask=0077 0 0\n")
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


def set_password(username, password):
    if any(ch in password for ch in "\n\r"):
        raise InstallError("Passwords must fit on one line")
    run(["chroot", TARGET, "chpasswd"], input_text=f"{username}:{password}\n")


def set_root_password(password):
    set_password("root", password)


def create_user(username, password, use_wheel=True, shell="/bin/bash"):
    run(["chroot", TARGET, "useradd", "-m", "-s", shell, "--", username])
    set_password(username, password)
    if use_wheel:
        run(["chroot", TARGET, "groupadd", "-f", "wheel"])
        run(["chroot", TARGET, "usermod", "-aG", "wheel", "--", username])


def write_profile(profile):
    """Persist the exact LFS composition selected by the user."""
    path = os.path.join(TARGET, "etc/zackos")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "profile.toml"), "w") as f:
        f.write(profile.to_toml())
    log(f"Wrote ZackOS profile: {profile.to_dict()}")


def fetch_i3_packages():
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
    # Full package set the shipped .xinitrc / i3 config below actually
    # depend on - not just the 5 packages that were being installed
    # before while the config referenced far more (i3status, dmenu,
    # mesa for glamor/GL accel behind the built-in "modesetting" KMS
    # driver, libinput, dbus, a font so i3bar/dmenu don't render tofu
    # boxes, and eudev so libinput has a live device database instead of
    # failing with "udev device never initialized"). No xf86-video-vesa:
    # confirmed the shipped kernel config has CONFIG_DRM_I915=y (real
    # hardware) and CONFIG_DRM_BOCHS=y (QEMU std VGA) - xorg-server 1.18+
    # bundles the "modesetting" DDX driver internally and autodetects
    # via KMS/udev exactly like Fedora/Arch/Ubuntu, same as no distro
    # ships a hand-written xorg.conf video Section anymore. vesa is
    # VBE/INT10-only and doesn't even exist as a code path on UEFI boots.
    names = (Path(__file__).resolve().parent / "desktop/i3-packages").read_text().split()
    packages = ["nixpkgs." + name for name in names]
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
    # Nix's build hooks use bash process substitution on the LFS devtmpfs.
    panel = chroot_run(export + "if ! test -e /dev/fd; then ln -s /proc/self/fd /dev/fd; fi; "
                       "nix-env -f /usr/local/lib/zackos-installer/desktop/panel/runtime.nix -i")
    if panel.returncode:
        raise InstallError("Failed to install the ZackOS panel and application launcher")


def install_wm(choice, bundled=False):
    if choice == "plasma-x11":
        return install_plasma_x11()
    if choice == "none":
        log("Skipping WM install (console-only system).")
        return

    if not bundled:
        fetch_i3_packages()
    # These are the packages the config files below actually invoke; if
    # any are missing the WM will render but be broken (no cursor, no
    # bar, tofu boxes), so require every one of them instead of quietly
    # continuing like the old "optional" path did.
    link_nix_bins([
        "i3", "i3bar", "i3status", "xterm", "urxvt", "dmenu", "startx",
        "Xorg", "xauth", "xrdb", "i3lock", "feh", "scrot", "xdotool", "xclip",
    ], require_all=True)
    configure_i3_desktop()
    configure_lightdm()
    register_nix_fonts()

    write_xinitrc(choice)
    if choice == "i3":
        write_i3_config()


def configure_i3_desktop():
    target = Path(TARGET)
    profile = "/nix/var/nix/profiles/default"
    configure_xorg()
    dbus = target / "etc/dbus-1"
    dbus.mkdir(parents=True, exist_ok=True)
    for bus in ("session", "system"):
        name = bus + ".conf"
        dest = dbus / name
        if not dest.exists():
            # The packaged defaults include /etc/dbus-1/<bus>.conf as an override.
            # Installing them verbatim at that same path causes recursive includes.
            config = subprocess.check_output(
                ["chroot", TARGET, "cat", f"{profile}/share/dbus-1/{name}"], text=True
            )
            dest.write_text("".join(line for line in config.splitlines(True)
                                    if f">/etc/dbus-1/{name}</include>" not in line))
    (target / "var/lib/dbus").mkdir(parents=True, exist_ok=True)
    subprocess.run(["chroot", TARGET, f"{profile}/bin/dbus-uuidgen", "--ensure"], check=True)
    service = target / "usr/local/sbin/zackos-dbus"
    service.write_text(
        "#!/bin/sh\nset -eu\nmkdir -p /run/dbus\n"
        f"ln -sfn {profile} /run/opengl-driver\n"
        f"exec {profile}/bin/dbus-daemon --system --nofork --nopidfile\n"
    )
    service.chmod(0o755)
    session = target / "etc/X11/zackos-session"
    session.write_text(
        "#!/bin/sh\n"
        f"export PATH={profile}/bin:/usr/local/bin:/usr/bin:/bin\n"
        f"export XDG_DATA_DIRS={profile}/share:/usr/local/share:/usr/share\n"
        "export LANG=C.UTF-8\n"
        'mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"\n'
        "xrdb -nocpp -merge /etc/X11/zackos.Xresources\n"
        '[ ! -r "$HOME/.Xresources" ] || xrdb -merge "$HOME/.Xresources"\n'
        'if [ "$#" -eq 0 ]; then set -- i3; fi\n'
        'exec dbus-run-session -- "$@"\n'
    )
    session.chmod(0o755)
    (target / "tmp").chmod(0o1777)
    (target / "root").chmod(0o700)
    (target / "etc/shadow").chmod(0o600)


def configure_lightdm():
    target = Path(TARGET)
    src = Path(__file__).with_name("desktop") / "login"
    profile = "/nix/var/nix/profiles/default"
    # Install only the two LightDM PAM services, leaving console authentication intact.
    files = {
        "lightdm.conf": "etc/lightdm/lightdm.conf",
        "lightdm-gtk-greeter.conf": "etc/lightdm/lightdm-gtk-greeter.conf",
        "users.conf": "etc/lightdm/users.conf",
        "lightdm.pam": "etc/pam.d/lightdm",
        "lightdm-greeter.pam": "etc/pam.d/lightdm-greeter",
        "zackos-i3.desktop": "usr/share/xsessions/zackos-i3.desktop",
        "sign-in.png": "usr/share/backgrounds/zackos/sign-in.png",
        "avatar.png": "usr/share/pixmaps/zackos-avatar.png",
    }
    for name, destination in files.items():
        path = target / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / name, path)
        path.chmod(0o644)
    wrapper = target / "etc/lightdm/zackos-greeter"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export XDG_DATA_DIRS={profile}/share:/usr/local/share:/usr/share\n"
        'exec "$@"\n'
    )
    wrapper.chmod(0o755)
    if subprocess.run(["chroot", TARGET, "id", "-u", "lightdm"], capture_output=True).returncode:
        run(["chroot", TARGET, "useradd", "--system", "--user-group", "--no-create-home",
             "--home-dir", "/var/lib/lightdm", "--shell", "/usr/sbin/nologin", "lightdm"])
    run(["chroot", TARGET, "install", "-d", "-m", "0750", "-o", "lightdm", "-g", "lightdm",
         "/var/lib/lightdm", "/var/lib/lightdm/.config", "/var/lib/lightdm/.config/gtk-3.0"])
    (target / "var/lib/lightdm-data").mkdir(mode=0o755, exist_ok=True)
    css = target / "var/lib/lightdm/.config/gtk-3.0/gtk.css"
    shutil.copyfile(src / "greeter.css", css)
    css.chmod(0o644)
    # The LFS system bus looks under /etc, while Nix installs this policy in its profile.
    policy = target / "etc/dbus-1/system.d/org.freedesktop.DisplayManager.conf"
    policy.parent.mkdir(parents=True, exist_ok=True)
    if not os.path.lexists(policy):
        policy.symlink_to(profile + "/share/dbus-1/system.d/" + policy.name)


def install_plasma_x11():
    """Install KDE Plasma and the Xorg libinput driver."""
    wait_for_network()
    export = (f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
              f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; " )
    packages = ["nixpkgs.xorg-server", "nixpkgs.xinit", "nixpkgs.xauth",
        "nixpkgs.xf86-input-libinput", "nixpkgs.mesa", "nixpkgs.libglvnd",
        "nixpkgs.dejavu_fonts", "nixpkgs.rxvt-unicode",
        "nixpkgs.kdePackages.plasma-desktop", "nixpkgs.kdePackages.plasma-workspace",
        "nixpkgs.kdePackages.kwin-x11", "nixpkgs.kdePackages.systemsettings",
        "nixpkgs.kdePackages.dolphin", "nixpkgs.kdePackages.konsole",
        "nixpkgs.kdePackages.breeze", "nixpkgs.kdePackages.breeze-icons", "nixpkgs.elogind"]
    result = chroot_run(export + "NIXPKGS_ALLOW_UNSUPPORTED_SYSTEM=1 nix-env -iA " + " ".join(packages))
    if result.returncode != 0: raise InstallError("Failed to install KDE Plasma X11 package set")
    link_nix_bins(["startx", "Xorg", "xauth", "urxvt", "startplasma-x11", "kwin_x11", "plasmashell", "konsole", "dolphin"], require_all=True)
    register_nix_fonts()
    configure_xorg()
    write_xinitrc("plasma-x11")

def register_nix_fonts():
    """Point the base LFS system's fontconfig at the Nix-installed fonts.

    dejavu_fonts lands in the Nix profile at
    /nix/var/nix/profiles/default/share/fonts, but /etc/fonts/fonts.conf
    (shipped by the base LFS/BLFS fontconfig build) only scans
    /usr/share/fonts and ~/.fonts. Confirmed live: dejavu_fonts installs
    fine and i3's config references "DejaVu Sans Mono", but fc-list never
    sees it, so i3bar/dmenu would render tofu boxes / fall back to a
    default font instead of the requested DejaVu Sans Mono. Add an
    explicit conf.d include for the Nix font dir and rebuild the cache.
    """
    conf_dir = os.path.join(TARGET, "etc/fonts/conf.d")
    os.makedirs(conf_dir, exist_ok=True)
    with open(os.path.join(conf_dir, "00-nix-fonts.conf"), "w") as f:
        f.write(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
            '<fontconfig>\n'
            '  <dir>/nix/var/nix/profiles/default/share/fonts</dir>\n'
            '</fontconfig>\n'
        )
    chroot_run("fc-cache -f")


def configure_xorg():
    """Use Xorg modesetting and libinput hotplug on all VM display adapters."""
    target = Path(TARGET)
    profile = "/nix/var/nix/profiles/default"
    # The inherited image can contain the old fixed evdev configuration.
    (target / "etc/X11/xorg.conf").unlink(missing_ok=True)
    xorg = target / "etc/X11/xorg.conf.d"
    xorg.mkdir(parents=True, exist_ok=True)
    (xorg / "20-nix-modules.conf").write_text(
        f'Section "Files"\n ModulePath "{profile}/lib/xorg/modules"\nEndSection\n'
    )
    (xorg / "40-libinput.conf").write_text(
        'Section "InputClass"\n Identifier "event devices"\n'
        ' MatchDevicePath "/dev/input/event*"\n Driver "libinput"\nEndSection\n'
    )
    rules = target / "etc/udev/rules.d/70-zackos-render.rules"
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text('SUBSYSTEM=="drm", KERNEL=="renderD*", GROUP="render", MODE="0660"\n')


def write_xinitrc(choice="i3"):
    """Set up the selected X11 session for the new account."""
    if choice == "plasma-x11":
        content = """#!/bin/sh
profile=/nix/var/nix/profiles/default
export PATH="$profile/bin:/usr/local/bin:/usr/bin:/bin"
export XDG_DATA_DIRS="$profile/share:/usr/local/share:/usr/share"
export XDG_CONFIG_DIRS="$profile/etc/xdg:/etc/xdg"
export QT_PLUGIN_PATH="$profile/lib/qt-6/plugins"
export QML2_IMPORT_PATH="$profile/lib/qt-6/qml"
exec dbus-run-session -- startplasma-x11
"""
    else:
        content = "#!/bin/sh\nexec /etc/X11/zackos-session\n"
    for rel in ("etc/skel/.xinitrc", "root/.xinitrc"):
        path = os.path.join(TARGET, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: f.write(content)
    if choice == "plasma-x11":
        helper = os.path.join(TARGET, "root/start-kde.sh")
        with open(helper, "w") as f:
            f.write("""#!/bin/sh
set -eu
profile=/nix/var/nix/profiles/default
export PATH="$profile/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p /run/dbus
[ -S /run/dbus/system_bus_socket ] || dbus-daemon --system --fork
exec startx -- :0 vt7
""")
        os.chmod(helper, 0o755)

def write_i3_config():
    desktop = Path(__file__).with_name("desktop")
    files = {
        "i3.conf": ["etc/i3/config", "etc/skel/.config/i3/config", "root/.config/i3/config"],
        "i3status.conf": ["etc/zackos/i3status.conf"],
        "Xresources": ["etc/X11/zackos.Xresources"],
        "orbit.png": ["usr/share/backgrounds/zackos/orbit.png"],
        "terminal/kitty.conf": ["etc/skel/.config/kitty/kitty.conf"],
        "terminal/zprofile": ["etc/skel/.zprofile"],
        "terminal/zshrc": ["etc/skel/.zshrc"],
        "terminal/fastfetch.jsonc": ["etc/xdg/fastfetch/config.jsonc", "etc/skel/.config/fastfetch/config.jsonc"],
        "terminal/logo.txt": ["usr/share/zackos/fastfetch-logo.txt"],
    }
    for name, destinations in files.items():
        for destination in destinations:
            path = Path(TARGET, destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(desktop / name, path)
            path.chmod(0o644)

    # Fastfetch reads Kitty's version here before falling back to a terminal query.
    kitty_lib = Path(TARGET, "usr/lib/kitty")
    if not os.path.lexists(kitty_lib):
        kitty_lib.symlink_to("/nix/var/nix/profiles/default/lib/kitty")

    shells = Path(TARGET, "etc/shells")
    known = shells.read_text().splitlines() if shells.exists() else ["/bin/sh", "/bin/bash"]
    if DESKTOP_SHELL not in known:
        shells.write_text("\n".join([*known, DESKTOP_SHELL, ""]))
    release = Path(TARGET, "etc/os-release")
    if not os.path.lexists(release):
        release.write_text('NAME=ZackOS\nPRETTY_NAME=ZackOS\nID=zackos\n'
                           'HOME_URL="https://github.com/zackmsa777-a11y/Zackos"\n')

    gtk = Path(TARGET, "etc/gtk-3.0/settings.ini")
    gtk.parent.mkdir(parents=True, exist_ok=True)
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(gtk)
    if not settings.has_section("Settings"):
        settings.add_section("Settings")
    settings["Settings"].update({
        "gtk-application-prefer-dark-theme": "true",
        "gtk-theme-name": "Adwaita-dark",
        "gtk-icon-theme-name": "Adwaita",
    })
    with gtk.open("w") as output:
        settings.write(output)


def link_nix_bins(names, require_all=False):
    """Expose Nix package executables from both bin/ and sbin/.

    runit installs its PID1/stage binary as ``sbin/runit`` on some Nix
    revisions, while runsvdir/sv may live in ``bin``.  Looking in only one
    directory was the cause of the old ``/sbin/init`` service failure.

    BUG FIX (2026-08-08): crashed with FileExistsError the moment two
    different store paths both ship the same binary name (e.g. the
    wrapped ``rxvt-unicode`` derivation and the underlying
    ``rxvt-unicode-unwrapped`` one both have a bin/urxvt - completely
    normal in nixpkgs). The pre-existence guard here used
    os.path.exists(), which FOLLOWS the symlink through the live host's
    root, not through TARGET/'s root - so a link that's already correctly
    in place (and perfectly resolvable once this disk is actually
    chrooted/booted into) can still read back as "doesn't exist" from the
    host's point of view, and the subsequent os.symlink() call then fails
    on the literal symlink file already sitting there. lexists() checks
    only whether the link itself is present, never dereferencing it, which
    is the only correct test for "did we already create this one".
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
                    if not os.path.lexists(link_path):
                        os.symlink(f"/nix/store/{entry}/{subdir}/{name}", link_path)
                    found.add(name)
    missing = sorted(set(names) - found)
    if missing and require_all:
        raise InstallError("Provider binaries were not found in the Nix store: " + ", ".join(missing))
    if missing:
        log("Optional provider binaries unavailable: " + ", ".join(missing))


def repair_sysv_console():
    """Guarantee a usable local + serial login on the sysvinit path."""
    path = os.path.join(TARGET, "etc/inittab")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = open(path).read() if os.path.exists(path) else ""
    lines = [line for line in old.splitlines() if not line.startswith(("id:", "1:2345:", "S0:2345:"))]
    lines += ["id:3:initdefault:",
              "1:2345:respawn:/sbin/agetty --noclear tty1 38400 linux",
              "S0:2345:respawn:/sbin/agetty -L 115200 ttyS0 vt100"]
    with open(path, "w") as f: f.write("\n".join(lines) + "\n")
    prof = os.path.join(TARGET, "etc/profile")
    with open(prof, "a") as f:
        f.write('\nexport PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:${PATH}"\n')


def install_init(choice, part=None, bundled=False):
    i3_desktop = (Path(TARGET) / "etc/lightdm/lightdm.conf").exists()
    display_command = "/usr/local/lib/zackos-installer/desktop/zackos-display"
    if choice == "sysvinit":
        repair_sysv_console()
        if i3_desktop:
            with (Path(TARGET) / "etc/inittab").open("a") as inittab:
                inittab.write("db:2345:respawn:/usr/local/sbin/zackos-dbus\n")
                inittab.write(f"xd:2345:respawn:{display_command}\n")
        log("Keeping sysvinit with explicit tty1/ttyS0 getty configuration.")
        return
    if choice != "runit":
        raise InstallError(f"Init provider {choice!r} is not implemented yet")

    log("Installing runit with explicit stage/service wiring...")
    export = (
        f"export PATH=/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH; "
        f"export HOME=/root; export NIX_PATH=nixpkgs={NIX_CHANNEL}; "
    )
    if not bundled:
        chroot_run(export + "nix-env -iA nixpkgs.runit")
    link_nix_bins(["runit", "runit-init", "runsvdir", "runsv", "sv", "chpst", "svlogd"], require_all=True)

    etc_runit = os.path.join(TARGET, "etc/runit")
    service_dir = os.path.join(TARGET, "etc/service")
    os.makedirs(etc_runit, exist_ok=True)
    os.makedirs(service_dir, exist_ok=True)

    # Resolve the booted partition through sysfs before udev creates /dev/disk links.
    # The installer and the installed disk can use different virtual storage controllers.
    with open(os.path.join(etc_runit, "1"), "w") as f:
        f.write(
            "#!/bin/sh\n"
            "mount -t proc proc /proc 2>/dev/null || true\n"
            "mount -t sysfs sysfs /sys 2>/dev/null || true\n"
            "mount -t devtmpfs devtmpfs /dev 2>/dev/null || true\n"
            "devnum=$(awk '$5 == \"/\" { print $3; exit }' /proc/self/mountinfo)\n"
            "rootdev=$(readlink -f /sys/dev/block/\"$devnum\") || exit 1\n"
            "mount -o remount,rw /dev/\"${rootdev##*/}\" / || exit 1\n"
            "mkdir -p /dev/pts /dev/shm\n"
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
    #
    # VOID-STYLE HANDOFF (added after auditing how Void Linux's own
    # void-runit stage 2 works): Void starts udevd the same way during
    # its stage 1 core-services, then hands it to real supervision via a
    # dedicated /etc/sv/udevd/run script that does
    # "udevadm control --exit" (stops this bootstrap instance) followed
    # by "exec udevd" (a fresh, supervised instance runsv can restart if
    # it ever dies). Adopted verbatim below - this ad-hoc bring-up here
    # stays exactly as before for early device population, but is no
    # longer the ONLY thing running udevd for the life of the system.
    stage2_lines = (
        "#!/bin/sh\n"
        + runit_path_export
        + "if [ -x /usr/sbin/udevd ]; then\n"
        "  /usr/sbin/udevd --daemon 2>/dev/null &\n"
        "  udevadm trigger 2>/dev/null || true\n"
        "  udevadm settle --timeout=10 2>/dev/null || true\n"
        "fi\n"
        "if grep -qs ' /boot/efi ' /etc/fstab; then mountpoint -q /boot/efi || mount /boot/efi; fi\n"
        "exec /usr/local/bin/runsvdir -P /etc/service\n"
    )
    with open(os.path.join(etc_runit, "2"), "w") as f:
        f.write(stage2_lines)
    # PID 1 runs stage 3 after leaving the service-supervision stage.
    with open(os.path.join(etc_runit, "3"), "w") as f:
        f.write("#!/bin/sh\n" + runit_path_export +
                "sv -w 5 force-stop /etc/service/* 2>/dev/null || true\n"
                "/sbin/killall5 -15 2>/dev/null || true\nsleep 1\n"
                "/sbin/killall5 -9 2>/dev/null || true\n"
                "swapoff -a 2>/dev/null || true\nsync\n"
                "umount -a -r -t noproc,sysfs,devtmpfs,tmpfs 2>/dev/null || true\n"
                "mount -n -o remount,ro /\nsync\n")
    for fname in ("1", "2", "3"):
        os.chmod(os.path.join(etc_runit, fname), 0o755)

    # VOID-STYLE SERVICE LAYOUT (adopted after auditing Void Linux's own
    # runit conventions, which every real-world runit deployment follows):
    # service *definitions* live as templates under /etc/sv/<name>/, and
    # the live tree runsvdir actually supervises (/etc/service) is just a
    # symlink farm pointing back at those templates - "ln -s /etc/sv/x
    # /etc/service/x". This replaces writing services directly into
    # /etc/service, which worked but mixed "template" and "live state"
    # in one place; Void keeps them separate so a service can be
    # disabled by removing one symlink without losing its definition,
    # and multiple live services (agetty-tty1/agetty-ttyS0) can share one
    # template (agetty-generic) via their own "run" symlink + per-service
    # "conf" file, exactly like Void does for its getty services.
    sv_dir = os.path.join(TARGET, "etc/sv")
    os.makedirs(sv_dir, exist_ok=True)

    def write_service(name, command, log=False):
        """Write a service template under /etc/sv/<name>/run and symlink
        it live into /etc/service/<name>. Optional svlogd-backed logging
        (Void's "./log/run" convention) when log=True.
        """
        template_dir = os.path.join(sv_dir, name)
        os.makedirs(template_dir, exist_ok=True)
        with open(os.path.join(template_dir, "run"), "w") as f:
            f.write("#!/bin/sh\nexec 2>&1\nexec " + command + "\n")
        os.chmod(os.path.join(template_dir, "run"), 0o755)
        if log:
            log_dir = os.path.join(template_dir, "log")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "run"), "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    "exec 2>&1\n"
                    f"[ -d /var/log/{name} ] || install -m755 -d /var/log/{name}\n"
                    f"exec /usr/local/bin/svlogd -tt /var/log/{name}\n"
                )
            os.chmod(os.path.join(log_dir, "run"), 0o755)
        link_path = os.path.join(service_dir, name)
        if os.path.islink(link_path):
            os.remove(link_path)
        elif os.path.isdir(link_path):
            shutil.rmtree(link_path)
        elif os.path.exists(link_path):
            os.remove(link_path)
        os.symlink(os.path.join("..", "sv", name), link_path)

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
    write_service("power", "/usr/local/bin/zackos-power --serve runit")
    write_service("acpid", "/usr/sbin/acpid -f")
    if i3_desktop:
        write_service("dbus", "/usr/local/sbin/zackos-dbus")
        write_service("lightdm", display_command)
    write_service("agetty-tty1", "setsid -c /sbin/agetty tty1 38400 linux")
    write_service("agetty-ttyS0", "setsid -c /sbin/agetty -L 115200 ttyS0 vt100")

    # VOID-STYLE ADDITIONS: two services Void supervises for real that
    # this system previously only ever ran ad-hoc (udevd, backgrounded in
    # stage 2, never restarted if it died) or not at all (dhcpcd - network
    # on the INSTALLED target never came up on its own; every prior test
    # session had to hand-configure "ip addr add 10.0.2.15/24 ..." after
    # every single boot). Both adapted directly from Void's own
    # srcpkgs/*/files/*/run templates.
    write_service(
        "udevd",
        'sh -c \'udevadm control --exit 2>/dev/null; exec /usr/sbin/udevd\''
    )
    # dhcpcd ships at /usr/local/sbin/dhcpcd (symlinked from the Nix store
    # path by build_installer_iso.sh's live-boot dhcpcd step) and survives
    # into the installed target because copy_system() copies /nix and
    # /usr/local wholesale. "-B" keeps it in the foreground (required for
    # a runit run script - runsv supervises the foreground process
    # directly, it does not track a forked-off child), matching Void's
    # own dhcpcd run script.
    write_service("dhcpcd", "/usr/local/sbin/dhcpcd -B", log=True)

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



def configure_power(init, username=None, live=False):
    target = Path(TARGET)
    for group in ("power", "render"):
        run(["chroot", TARGET, "/usr/sbin/groupadd", "-f", "-r", group])
        if username:
            run(["chroot", TARGET, "/usr/sbin/usermod", "-aG", group, username])
    helper = target / "usr/local/bin/zackos-power"
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("power") / "zackos-power", helper)
    helper.chmod(0o755)
    rules = target / "etc/acpi/events"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "zackos-power").write_text("event=^button/power.*\naction=/usr/local/bin/zackos-power poweroff\n")
    if init == "runit":
        for name in ("shutdown", "halt", "reboot", "poweroff"):
            path = target / "sbin" / name
            backup = path.with_name(name + ".sysvinit")
            if path.is_file() and not path.is_symlink() and not backup.exists():
                shutil.copy2(path, backup)
            temp = path.with_name(name + ".zackos-new")
            temp.unlink(missing_ok=True)
            temp.symlink_to("/usr/local/bin/zackos-power")
            temp.replace(path)
    elif not live:
        path = target / "etc/inittab"
        entries = [line for line in path.read_text().splitlines() if not line.startswith(("zp:", "za:"))]
        entries += ["zp:2345:respawn:/usr/local/bin/zackos-power --serve sysvinit",
                    "za:2345:respawn:/usr/sbin/acpid -f"]
        path.write_text("\n".join(entries) + "\n")


def cleanup_mounts():
    for d in ("dev/pts", "dev", "sys", "proc", "boot/efi"):
        run(f"umount -lf {TARGET}/{d}", check=False)
    run(f"umount -lf {TARGET}", check=False)


def check_package_cache():
    try:
        with urllib.request.urlopen("https://cache.nixos.org/nix-cache-info", timeout=12) as response:
            if b"StoreDir: /nix/store" not in response.read(4096):
                raise ValueError("unexpected response from the package cache")
    except (OSError, ValueError) as exc:
        raise InstallError(f"Cannot reach the Nix package cache over HTTPS ({exc})") from exc


def full_install(disk, wm_choice=None, init_choice=None, hostname="zackos",
                 root_password="", username=None, user_password=None, profile=None, identity=None, progress=None):
    profile = profile or InstallerProfile(desktop=wm_choice or "i3", init=init_choice or "sysvinit")
    errors = validate_profile(profile, require_implemented=True)
    if errors:
        raise InstallError("Profile is not installable yet: " + "; ".join(errors))
    Path("/run/lock").mkdir(parents=True, exist_ok=True)
    with open("/run/lock/zackos-install.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("Another ZackOS installation is running") from exc
        bundled = bundled_desktop(profile)
        if not bundled and (profile.desktop != "none" or profile.init == "runit"):
            check_package_cache()
        if os.path.ismount(TARGET):
            raise InstallError("The installation target is already mounted")
        report = progress or (lambda step, message: None)
        report(0, "Checking available space")
        efi = Path("/sys/firmware/efi").is_dir()
        if efi and not all(path.is_file() for path in
                           (EFI_ASSETS / "BOOTX64.EFI", EFI_ASSETS / "unicode.pf2", Path("/usr/sbin/mkfs.fat"))):
            raise InstallError("The installation media is missing its UEFI tools.")
        if not Path("/usr/sbin/acpid").is_file():
            raise InstallError("The installation media is missing its ACPI power service.")
        check_capacity(disk, bundled, efi)
        report(0, "Preparing the disk")
        part = partition_disk(disk, identity, efi)
        esp = partition_path(disk, 1) if efi else None
        if esp:
            run(["/usr/sbin/mkfs.fat", "-F", "32", "-n", "ZACKEFI", esp])
        format_partition(part)
        mount_target(part)
        try:
            report(1, "Copying the base system")
            copy_system()
            if esp:
                mountpoint = Path(TARGET, "boot/efi")
                mountpoint.mkdir(parents=True, exist_ok=True)
                run(["mount", "-t", "vfat", "-o", "umask=0077", esp, str(mountpoint)])
            write_fstab(part, esp=esp)
            write_profile(profile)
            set_hostname(hostname)
            set_root_password(root_password)
            report(2, "Installing the desktop and packages")
            if bundled:
                populate_desktop()
            install_wm(profile.desktop, bundled)
            report(3, "Setting up accounts and services")
            if username:
                create_user(username, user_password or root_password,
                            shell=DESKTOP_SHELL if profile.desktop == "i3" else "/bin/bash")
            configure_power(profile.init, username)
            install_init(profile.init, part, bundled)
            report(4, "Installing GRUB")
            install_bootloader(disk, part, esp=esp)
            if profile.desktop == "i3" and username:
                marker = Path(TARGET, "boot/grub/zackos-first-user")
                marker.write_text(username + "\n")
                marker.chmod(0o600)
            run(["sync"])
        finally:
            cleanup_mounts()
        log("Install complete!")
