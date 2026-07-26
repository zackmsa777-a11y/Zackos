# ZackOS

**A Linux distribution, built from scratch — and we're just getting started.**

> Kernel 6.16.1 · GCC 15.2.0 · Python 3.13.7 · 100+ packages, hand-built from raw source · full XFCE desktop · self-contained live-boot ISO

---

## What this actually is

Most people install a distro. We built one.

Every binary in this system — the kernel, the C library, the compiler, the desktop environment — was compiled from raw upstream source, in the correct dependency order, with every real-world build bug found and fixed along the way. No package manager, no prebuilt binaries, no shortcuts. This started as a strict [Linux From Scratch 12.4](https://www.linuxfromscratch.org/lfs/view/12.4/) + [BLFS](https://www.linuxfromscratch.org/blfs/) build and has grown into something that boots to a full graphical desktop on its own — which is where the "distro" part starts to become real.

We're calling it **ZackOS**. This repo is the automation that builds it.

## What's inside

| Layer | What we built |
|---|---|
| **Toolchain** | Cross-compiler, GCC 15.2.0, glibc, binutils — bootstrapped from nothing |
| **Base system** | ~630 core binaries, full LFS Chapter 8 final system (79 packages) |
| **Kernel** | Linux 6.16.1, custom config: SQUASHFS, OverlayFS, virtio, ext4 — all built-in, no initramfs bloat |
| **Desktop** | Full XFCE4 stack — Xorg, xfwm4, xfce4-panel, xfdesktop, Thunar, xfce4-terminal — ~100 packages total, all from source |
| **Delivery** | A real, self-contained **live-boot ISO** (SquashFS + OverlayFS + custom initramfs) — boot it standalone, no separate disk image needed |

Boot-verified end to end: reaches a real login prompt, logs in, gives you a working shell on a writable overlay root, and can launch the full graphical desktop.

## Get it

Grab the latest live ISO from [Releases](../../releases) — it's split into ~400MB parts to fit GitHub's asset limits:

```bash
cat lfs_live_part* > lfs_live.iso
qemu-system-x86_64 -cdrom lfs_live.iso -m 2G -nographic
# login: root / lfs
```

Or boot it on real hardware / any hypervisor that can boot from an ISO. Root's writable layer is RAM-only (tmpfs overlay) unless you set up persistence yourself.

## Build it yourself

This repo ships the **build automation**, not a giant binary blob — a full rootfs is multiple GB, too big for a sane git history. Bring your own Linux box (or `--privileged` container) with root access and ~20GB free disk, and:

```bash
export LFS=/mnt/lfs
bash scripts/05-cross-toolchain.sh   # Ch5: cross-compiler
bash scripts/06-temp-tools.sh        # Ch6: temp tools
bash scripts/07-chroot-prep.sh       # Ch7: chroot prep
chroot "$LFS" /usr/bin/env -i HOME=/root PATH=/usr/bin:/usr/sbin \
    MAKEFLAGS=-j$(nproc) /usr/bin/bash /scripts/08-final-system.sh
```

Every script checks a stamp file before building, so it's safe to re-run after any interruption. BLFS desktop packages live in `scripts_chroot/blfs-*.sh`, applied in numbered batch order — patch tarballs for each batch are checkpointed in `patches/`.

Full build notes, every real bug we hit and fixed (MB_LEN_MAX, diffutils cross-compile quirks, glibc locale gotchas, GTK3/meson dependency ordering, the works), and kernel config details are further down in this README / in the script comments.

## Roadmap — where ZackOS is going

This has stopped being "just an LFS exercise." The plan:

- [x] Bootstrap toolchain, base system, kernel — from raw source
- [x] Full XFCE graphical desktop, boot-verified
- [x] Self-contained live-boot ISO (SquashFS + OverlayFS)
- [ ] **Our own package manager, written in C** — no more manual `scripts_chroot/*.sh` babysitting. Dependency resolution, versioned installs, clean uninstall, the real deal. This is the next big milestone — built by us, not borrowed.
- [ ] Persistent live storage (writable overlay that survives reboot)
- [ ] Networking stack (dhcpcd, iproute2, openssh) for a system that's actually usable off a LAN
- [ ] Browser support (Firefox — the big one; needs a Rust/Node toolchain bootstrap first)
- [ ] A proper installer, so this stops being "dd an ISO and pray"

If you want to help build the package manager, this is the spot — everything upstream of it (the OS it needs to manage) is already real and booting.

## Verified working

Booted this exact build to a full login prompt, logged in as root, ran `uname -a`, `df -h`, `free -h`, compiled and ran a C program with `gcc`, ran `python3`, launched a full XFCE session (Xorg + xfwm4 + xfdesktop + xfce4-panel all running simultaneously, clean logs), and did a clean shutdown back through SysVinit — all while boot-tested purely in QEMU (TCG software emulation, no KVM). On real hardware with KVM this is faster and even more capable.

## Known real bugs we found & fixed (not sandbox artifacts — will bite you on real hardware too)

- **MB_LEN_MAX** header conflict after GCC pass1 — fixed by composing `limits.h` from `limitx.h`+`glimits.h`+`limity.h`
- **diffutils** cross-compile configure failure — pre-set `gl_cv_func_strcasecmp_works=yes`
- **Chapter 8 doc-generation** fails before texinfo exists — every doc step guarded with `command -v makeinfo`
- **libxcrypt** needs perl≥5.14 earlier than the book's default order
- **python-bootstrap** needs `--with-ensurepip=no`; the real Ch8 python build needs `--enable-optimizations` removed
- **glibc** needs `mkdir -pv /usr/lib/locale` before `localedef` will work
- **ncurses** needs `rm -rf /usr/share/terminfo` before every retry (partial-copy corruption otherwise)
- **Live-ISO fstab bug**: baked-in `/etc/fstab` pointed at `/dev/sda` (real-disk boot), which made the live-CD's fsck halt on a nonexistent device — fixed by rewriting the root fstab entry to `overlay` type with fsck-pass 0 in the initramfs, pre-`switch_root`
- A long tail of GTK3/Xfce meson-vs-autotools, header-install-path, and stale-mtime checkpoint gotchas — see script comments for the gory details

## Releases

| Tag | What it is |
|---|---|
| `v3.0-live-squashfs-iso` | **Current** — self-contained live-boot ISO, SquashFS+OverlayFS, boots to login on its own |
| `v2.0-iso-xfce` | GRUB-shell ISO + companion raw disk, full XFCE desktop |
| `v1.0`–`v1.5` | Early raw-disk-image milestones (boot, fastfetch, BLFS X11 base) |

---

*Built by hand, one package at a time. If you're reading this thinking "why would you do this to yourself" — same question, honestly. But it boots.*
