# ZackOS Installer

Boot **Try or install ZackOS** to open the graphical installer inside i3 as
the local account `live`. Choose **Try ZackOS** to explore the desktop.
Open **Apps → Install ZackOS** to return to setup.

Choose a destination disk, a desktop and init system, then enter the computer
name and passwords for your desktop account and root. Review the destination
and select **Erase all data** to enable **Install ZackOS**. Setup excludes
the boot device, mounted disks, and labeled persistence volumes. It checks
the selected device again immediately before partitioning. The default i3
installation uses packages from the ISO and works offline.

The GTK window runs as the live user. A local service performs the disk
operations and reports progress. Reopening the installer brings back the
current operation. The installed system comes from the original read-only
image, so live-session accounts, files and settings stay on the live media.

**Text installer** in GRUB opens the console workflow. Log in as `root` with
the live password `zackos` and run `zackinstall`. Both interfaces use the
same installation code and support i3, Plasma X11 and console profiles.

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

With QEMU installed, run `./qemu-i3.sh zackos.qcow2 ZackOS-i3.iso`
from this directory. It creates a 24 GiB disk if needed. The VM uses x86-64
BIOS, two vCPUs, 4 GiB RAM and VirtIO graphics.

The graphical default installs glibc, Nix, runit, GRUB and i3. With automatic
restart enabled on the review screen, setup displays a 20-second countdown
after success. **Stay in live session** cancels that restart. The installed
disk opens i3 once as the account you created. XDM handles subsequent sign-ins.
Plasma X11 and the console with runit require access to the Nix cache.
For a console-mode installation, `zackinstall --no-reboot` keeps the live session open.

The **Start ZackOS** disk entry loads Linux and displays the logo with a
white Pac-Man eating dots in both directions while services start. Xorg
takes over when it is ready. The loader uses the first connected Linux KMS
display it can initialize. The text installer uses the BIOS console for its local monitor and keyboard. The GRUB menu also offers a recovery entry.

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

## Build the graphical ISO

Use an x86-64 Linux build machine with Nix, the intended nixpkgs channel and
`tar2sqfs` from squashfs-tools-ng. The following commands create a compressed
desktop package set, then add it and the graphical installer to the verified
v5.7 BIOS ISO.

```sh
./live/build-desktop.sh desktop.img
./remaster-iso.sh v5.7.iso ZackOS-i3.iso desktop.img
```

For a release built from an existing stock ZackOS i3 installation, pass
`/nix/var/nix/profiles/default` as the second argument to `build-desktop.sh`.
The exporter reads the immutable Nix dependency closure and its registration
records. To build on two machines, pipe `live/export-desktop.sh` from the
Nix machine into `tar2sqfs -q -s -c gzip desktop.img` on the packaging host.
With two arguments, `remaster-iso.sh` builds the desktop image automatically.
Pass `--console-only` as its third argument to produce the small text installer.

Remastering requires xorriso, cpio, gzip, an x86-64 C compiler, pkg-config,
and static libdrm, libpng and zlib development libraries. For a full
kernel/base rebuild, set `ZACKOS_DESKTOP_IMAGE` to the absolute desktop image
path when running `build_installer_iso.sh`.

Run the offline live-session check with

```sh
python3 test-live-installer.py ZackOS-i3.iso --screenshot installer.png
```

This captures the installer from the live user’s X session using scrot.
To test a powered-off disk in a disposable
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
