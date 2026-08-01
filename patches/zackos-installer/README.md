# ZackOS Installer

Source for the ZackOS installer, meant to be dropped into the live
environment at:
- `/usr/local/lib/zackos-installer/{core,manual,tui}.py`
- `/usr/local/bin/zackos-install` (dispatcher: `zackos-install easy|manual`)

## Tiers
- **Easy** (`tui.py`): archinstall-style, automated, minimal questions,
  sensible defaults (i3-wm + sysvinit).
- **Medium** (`manual.py`): Arch-manual-style, every command echoed before
  running, full control over WM/init/disk/hostname/user.
- **Advanced**: gentoo-manual-style, not yet implemented.

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

Both fixes verified end-to-end: full install via `zackos-install manual`
using a 2-disk QEMU setup (persistence disk + blank install target), then
a *separate* standalone boot of the install target alone, reaching a
working GRUB menu and successful `switch_root`/kernel boot.
