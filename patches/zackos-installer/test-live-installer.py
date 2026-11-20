#!/usr/bin/env python3
"""Boot the ISO offline, check the live installer and optionally save a guest screenshot."""
import argparse
import base64
import hashlib
from pathlib import Path
import re
import tempfile

import pexpect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--screenshot", type=Path, help="save the live user's installer screenshot as a PNG")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        disk = Path(temp, "scratch.raw")
        with disk.open("wb") as out:
            out.truncate(24 * 1024**3)
        vm = pexpect.spawn("qemu-system-x86_64", [
            "-machine", "pc", "-cpu", "max", "-smp", "2", "-m", "3072", "-vga", "virtio",
            "-cdrom", str(args.iso.resolve()), "-boot", "once=d", "-nic", "none",
            "-drive", f"file={disk},format=raw,if=virtio", "-display", "none",
            "-monitor", "none", "-serial", "stdio",
        ], encoding="utf-8", codec_errors="replace", timeout=180)
        try:
            vm.expect_exact("login:")
            vm.sendline("root")
            vm.expect_exact("Password:")
            vm.sendline("zackos")
            vm.expect_exact("# ")
            vm.sendline("export DISPLAY=:0 XAUTHORITY=/run/zackos-first-session/server.auth "
                        "PATH=/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/bin:/bin")
            vm.expect_exact("# ")
            vm.sendline("for i in $(seq 1 100); do "
                        "win=$(xdotool search --onlyvisible --name '^Install ZackOS$' 2>/dev/null | head -1); "
                        "if test -n \"$win\" && pgrep -u live -x i3 >/dev/null && "
                        "test \"$(ps -o uid= -p \"$(xdotool getwindowpid \"$win\")\" | tr -d ' ')\" = \"$(id -u live)\"; "
                        "then echo GUI_$((70+2)); break; fi; sleep 1; done")
            vm.expect_exact("GUI_72")
            vm.expect_exact("# ")
            if args.screenshot:
                # i3 launches scrot in the same user's X session as the installer.
                vm.sendline("xdotool mousemove 2 2; "
                            "i3-msg 'exec --no-startup-id scrot -o /home/live/installer-capture.png' >/dev/null; "
                            "for i in $(seq 1 80); do "
                            "if test -s /home/live/installer-capture.png && ! pgrep -u live -x scrot >/dev/null; "
                            "then break; fi; sleep .2; done; "
                            "test \"$(stat -c %U /home/live/installer-capture.png)\" = live && "
                            "echo CAP_$((40+2)) && sha256sum /home/live/installer-capture.png && "
                            "base64 /home/live/installer-capture.png && echo END_$((40+2))")
                vm.expect_exact("CAP_42")
                vm.expect(r"([0-9a-f]{64})  /home/live/installer-capture\.png\r*\n")
                digest = vm.match.group(1)
                vm.expect_exact("END_42", timeout=150)
                raw = base64.b64decode(re.sub(r"\s+", "", vm.before), validate=True)
                if hashlib.sha256(raw).hexdigest() != digest or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Incomplete guest screenshot")
                args.screenshot.write_bytes(raw)
            print("Offline ISO booted into i3 with the graphical installer running as live.")
        finally:
            vm.close(force=True)
        with disk.open("rb") as source:
            if any(source.read(1024**2)):
                raise ValueError("The installer wrote to the disk before confirmation")


if __name__ == "__main__":
    main()
