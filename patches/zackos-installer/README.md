# ZackOS Installer

Source for the ZackOS installer, meant to be dropped into the live
environment at:
- `/usr/local/lib/zackos-installer/{core,zackinstall}.py`
- `/usr/local/bin/zackinstall` (the installer entrypoint)

## One installer: ZackInstall
The old "Easy" (archinstall-style TUI) tier has been removed. ZackInstall
(`zackinstall.py`) is now the only installer ZackOS ships: every command
`core.run()` executes is echoed to the terminal before it runs, full
control over WM/init/disk/hostname/user, plain sequential Q&A, no curses.

Once ZackInstall is confirmed solid end-to-end, the plan is to also
publish a copy-paste, wiki-style install guide (Arch Wiki / Gentoo
Handbook style) documenting the same steps by hand, for people who'd
rather run the commands themselves than go through the script.

## Key bugs fixed in core.py (both found via real QEMU boot testing)
1. **Serial boot menu.** GRUB starts with its console available, enables the
   graphical theme when video initialization succeeds, and also presents the
   choices over a 115200-baud serial port.
2. **Stable root device.** GRUB finds the filesystem by UUID. The kernel
   uses the partition's PARTUUID, which it can resolve without an installed
   initramfs.

Both fixes verified end-to-end: full install via `zackinstall`
using a 2-disk QEMU setup (persistence disk + blank install target), then
a *separate* standalone boot of the install target alone, reaching a
working GRUB menu and successful `switch_root`/kernel boot.

## Install and boot i3 in QEMU

Use the installer ISO from the i3 desktop release. With QEMU installed, run
`./qemu-i3.sh zackos.qcow2 ZackOS-i3.iso` from this directory. It creates a
24 GiB disk if needed. Log in to the live system as `root` with password
`zackos` and run `zackinstall`. The default profile is glibc, Nix, runit,
GRUB and i3. The installer checks HTTPS access to the Nix cache before
partitioning. On failure it offers proxy configuration, retry, or an offline
SysV console. Confirm only the VM disk you intend to erase.

The QEMU profile uses x86-64, legacy BIOS, two vCPUs, 4 GiB RAM, and
VirtIO graphics. Select a regular username and password when prompted. A
successful live i3 installation restarts the machine and opens i3 as that
user once. After logout and on subsequent boots, use the XDM graphical
sign-in. Use `zackinstall --no-reboot` to control the restart yourself.

The **Start ZackOS** disk entry loads Linux and displays the logo with a
white Pac-Man eating dots in both directions while services start. Xorg
takes over when it is ready. The loader uses the first connected Linux KMS
display it can initialize. The live installer keeps the BIOS text console active for a local
monitor and keyboard. The GRUB menu also offers a recovery entry.

The top panel has an **Apps** button, clickable workspaces with a sliding
selection, the focused window title, live CPU history, memory usage and a
clock. Click **Apps** or press Super+D, type part of an application's name,
and press Enter to open it. Escape or a click outside closes the menu.
The menu includes visible XDG desktop entries for the system and user.
The animations follow GTK’s `gtk-enable-animations` setting.

| Shortcut | Action |
| --- | --- |
| Super + Enter | Open a terminal |
| Super + D | Open or close the application launcher |
| Super + Shift + D | Run a command with dmenu |
| Super + B | Start Firefox |
| Super + F2 | Open Thunar |
| Super + 1–5 | Switch workspace |

To boot the disk later, run `./qemu-i3.sh zackos.qcow2`. From the serial
console, `setsid -f /sbin/poweroff </dev/null >/dev/null 2>&1` lets
shutdown finish while runit stops the getty.

Rebuild from the verified v5.7 base using
`./remaster-iso.sh v5.7.iso ZackOS-i3.iso`. Install xorriso, cpio, gzip,
a native x86-64 C compiler, pkg-config, and the static libdrm, libpng and
zlib development libraries. The full kernel/base builder is
`build_installer_iso.sh`. To test a powered-off disk in a disposable
snapshot, run `python3 test-i3-boot.py zackos.qcow2 --user zack`. For a
fresh installation whose first session has yet to start, add `--first-boot`
to verify automatic entry and the return to XDM on logout.

## Boot artwork

Edit `branding/zackos.svg` to change the logo. The installed vector is at
`/usr/share/pixmaps/zackos.svg`. Recreate the desktop wallpaper and GRUB
background with `python3 branding/render-artwork.py` using Pillow, NumPy,
Inkscape and DejaVu Sans Mono. Recreate the Linux splash image with the
commands below. The Pac-Man frames are drawn by `boot/splash.c`.

```sh
inkscape branding/zackos.svg -o /tmp/zackos-logo.png
convert /tmp/zackos-logo.png -resize 440x -background '#10191c' -gravity center \
  -extent 800x600 -alpha remove -alpha off -strip PNG24:branding/splash.png
```
