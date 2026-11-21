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
BIOS, two vCPUs, 4 GiB RAM and VirtIO graphics. The launcher uses KVM when available and falls back to TCG.
On a Linux graphical host with working OpenGL and QEMU's GTK and VirGL support,
`ZACKOS_QEMU_3D=1 ./qemu-i3.sh zackos.qcow2` enables 3D on the VirtIO adapter.

For VirtualBox, select Linux (64-bit), use a SATA disk, VMSVGA graphics,
64 MiB video memory and leave 3D acceleration off. Either enable EFI before
installation or keep BIOS selected. Setup uses a GPT disk with a 260 MiB
EFI System Partition when booted with UEFI and an MBR disk when booted with BIOS.
Keep the VM in the firmware mode you installed with. The EFI loader is unsigned.
On QEMU's legacy VMware SVGA II adapter, GRUB selects the firmware framebuffer.
This adapter lacks the pitch-lock feature required by Linux vmwgfx. VirtualBox
VMSVGA uses the native driver.

The graphical default installs glibc, Nix, runit, GRUB and i3. With automatic
restart enabled on the review screen, setup displays a 20-second countdown
after success. **Stay in live session** cancels that restart. The installed
disk opens the ZackOS LightDM sign-in screen. Choose your account and enter
your password to start i3. Logging out returns to the same screen.
Plasma X11 and the console with runit require access to the Nix cache.

### Download tools and administrator access

The base system includes curl and GNU Wget for every desktop and the text console.
The account created by the installer belongs to the wheel group. Use `sudo -v`
with your **login password**, or `su -` with the **root password** you set in the
installer. `passwd` changes your own password.

For an existing v5.10 VirtualBox disk, download `ZackOS-system-tools.iso` and
its SHA-256 file from the v5.11 release and attach the ISO to the virtual optical
drive. Boot the installed disk. Press **Host+F1** (normally **Right Ctrl+F1**)
and log in as `root` using the installed root password. Run these commands.

```sh
mkdir -p /mnt/zacktools
mount -o ro /dev/sr0 /mnt/zacktools
python3 /mnt/zacktools/install.py
umount /mnt/zacktools
exit
```

Press **Host+F7** to return to the desktop. Run `sudo -v`, then `sudo id -u`
as your everyday account. A successful check prints `0`. The data disc updates
the installed disk in place and preserves the accounts and any existing sudoers policy.
It also enables localhost on the stock runit installation, immediately and on later boots.

The live ISO uses SysVinit. New graphical installations default to runit.
To use KDE, select **KDE Plasma X11** in the graphical installer, then sign
in to LightDM with the account you created. Both init choices support Plasma.

Plasma starts with **ZackOS Orbit**, the ringed-planet wallpaper, and a
floating top panel. Click **Apps** to search applications. The bottom dock
pins Dolphin, Konsole and System Settings and moves aside for windows.
Konsole uses the matching Orbit terminal profile. Both Konsole and Kitty open
with the ZackOS zsh prompt and Fastfetch system summary. You can change the global
theme in **System Settings → Colors & Themes → Global Theme** and edit the
panels in Plasma's Edit Mode.
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

Super+Enter opens Kitty with Zsh. Each fresh Kitty shell displays the folded
Z and system details from Fastfetch. Run `fastfetch` to refresh them.
Edit `~/.config/kitty/kitty.conf`, `~/.zshrc`, or
`~/.config/fastfetch/config.jsonc` to personalize your account.

| Shortcut | Action |
| --- | --- |
| Super + Enter | Open Kitty |
| Super + D | Open or close the application launcher |
| Super + Shift + D | Run a command with dmenu |
| Super + B | Start Firefox |
| Super + F2 | Open Thunar |
| Super + 1–5 | Switch workspace |

To boot the disk later, run `./qemu-i3.sh zackos.qcow2`. The i3 power menu or
`zackos-power poweroff` and `zackos-power reboot` work for the installed
desktop user. The hypervisor’s ACPI power button requests a clean shutdown.
On runit, root can schedule with `shutdown -r +10`, inspect with
`zackos-power status`, and cancel with `shutdown -c`.

## Build the graphical ISO

On x86-64 Linux, rebuild from the verified v5.8 desktop ISO with
`sudo ./remaster-iso.sh v5.8.iso ZackOS-i3.iso`. This reuses the offline
desktop and builds a single kernel for both the live ISO and the install.
To refresh the desktop package set, use Nix and `tar2sqfs` from squashfs-tools-ng.

```sh
./live/build-desktop.sh desktop.img
sudo ./remaster-iso.sh v5.8.iso ZackOS-i3.iso desktop.img
```

For a release built from an existing stock ZackOS i3 installation, pass
`/nix/var/nix/profiles/default` as the second argument to `build-desktop.sh`.
The exporter reads the immutable Nix dependency closure and its registration
records. To build on two machines, pipe `live/export-desktop.sh` from the
Nix machine into `tar2sqfs -q -s -c gzip desktop.img` on the packaging host.
For a v5.7 base, the remaster script builds a fresh desktop image when none is supplied.
Pass `--console-only` as its third argument to produce the small text installer.

Install xorriso, cpio, gzip, squashfs-tools, mtools, GRUB BIOS and x86-64 EFI
platform tools, a kernel build toolchain, pkg-config and the static libdrm,
libpng and zlib development libraries. The VM installer workflow lists the Ubuntu packages. For a full
kernel/base rebuild, set `ZACKOS_DESKTOP_IMAGE` to the absolute desktop image
path when running `build_installer_iso.sh`.

Run the offline live-session check with

```sh
python3 test-live-installer.py ZackOS-i3.iso --screenshot installer.png
```

This captures the installer from the live user’s X session using scrot.
To test a powered-off disk in a disposable
snapshot, run `python3 test-i3-boot.py zackos.qcow2 --user zack`. Add `--first-boot`
to also verify the installation-media handoff marker was consumed. The check
tests password rejection, sign-in to i3, the default terminal, and logout.

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
