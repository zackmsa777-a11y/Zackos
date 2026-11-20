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
1. **GRUB gfxterm hang**: `grub-mkconfig`'s auto-generated grub.cfg defaults
   to `terminal_output gfxterm` (graphical framebuffer), which renders
   nothing over a serial console (`-nographic`) and caused real boot hangs.
   Fix: `write_grub_cfg()` hand-writes a minimal grub.cfg with plain
   `terminal_output console` instead of trusting grub-mkconfig.
2. **Unstable root device naming**: hardcoding `root=/dev/vdb1` (the device
   name seen AT INSTALL TIME) breaks if the disk gets a different name at
   boot (e.g. installed with 2 disks attached -> vdb, booted standalone
   with 1 disk -> vda) -> kernel panic "VFS: Unable to mount root fs on
   unknown-block". Fix: use `root=UUID=<uuid>` instead, which is stable
   regardless of enumeration order.

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

After installation, shut down the live VM and run `./qemu-i3.sh zackos.qcow2`
to boot from disk. Sign in to the XDM graphical login to start i3. Use Super+F2nter for a terminal,
Super+B for Firefox, and Super+F2 for Thunar. The serial console remains
available for maintenance. On the installed runit system, use
`setsid -f /sbin/poweroff </dev/null >/dev/null 2>&1` when shutting down
from the serial console so stopping getty leaves the shutdown process running.

To rebuild the i3 release ISO, run `./remaster-iso.sh v5.7.iso ZackOS-i3.iso`
from the release checkout. The script checks the base ISO digest and requires
xorriso, cpio, gzip, and Python 3. For a full kernel and base rebuild, use
`build_installer_iso.sh` instead. To smoke-test a powered-off install, run
`python3 test-i3-boot.py zackos.qcow2`. The test requires pexpect and QEMU,
uses an installed regular account (default `tester`, selectable with `--user`),
boots a temporary snapshot without installer media, and checks the local
desktop and a terminal PTY.
