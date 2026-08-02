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
