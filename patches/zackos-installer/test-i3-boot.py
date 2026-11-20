#!/usr/bin/env python3
"""Smoke-test an installed qcow2 disk in a temporary QEMU snapshot."""
import argparse
import getpass
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time

import pexpect


def send_keys(monitor, text):
    names = {"-": "minus", "_": "shift-minus", ".": "dot", "\n": "ret"}
    for char in text:
        key = names.get(char, "shift-" + char.lower() if char.isupper() else char)
        monitor.sendall(f"sendkey {key} 25\n".encode())
        time.sleep(0.06)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disk", type=Path)
    parser.add_argument("--user", default="tester", help="existing unprivileged desktop test account")
    parser.add_argument("--first-boot", action="store_true",
                        help="also check that the installation-media handoff marker was consumed")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.user) or args.user == "root":
        parser.error("provide a regular local test account")
    password = getpass.getpass("Installed root password: ")
    with tempfile.TemporaryDirectory() as temp:
        monitor_path = Path(temp) / "monitor"
        vm = pexpect.spawn("qemu-system-x86_64", [
            "-machine", "pc", "-cpu", "max", "-smp", "2", "-m", "4096",
            "-snapshot", "-drive", f"file={args.disk.resolve()},format=qcow2,if=virtio",
            "-vga", "virtio", "-display", "none", "-serial", "stdio",
            "-nic", "none",
            "-monitor", f"unix:{monitor_path},server=on,wait=off",
        ], encoding="utf-8", timeout=120)
        try:
            vm.expect("login:")
            vm.sendline("root")
            vm.expect("Password:")
            vm.sendline(password)
            vm.expect(r"# ")
            # Set a temporary alphanumeric credential inside the disposable snapshot
            # so HMP keyboard input works with every original password layout.
            local_password = secrets.token_hex(12)
            vm.sendline(f"echo {args.user}:{local_password} | chpasswd")
            vm.expect(r"# ")
            def wait_guest(check, timeout=90):
                # Split the marker so the echoed command cannot satisfy the assertion.
                vm.sendline(f"for i in $(seq 1 {timeout}); do "
                            f"if ( {check} ) >/dev/null 2>&1; then echo PASSED_$((40+2)); break; fi; sleep 1; done")
                vm.expect_exact("PASSED_42", timeout=timeout + 10)
                vm.expect(r"# ")

            vm.sendline("export PATH=/nix/var/nix/profiles/default/bin:$PATH "
                        "DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0")
            vm.expect(r"# ")
            greeter = "xdotool search --onlyvisible --class lightdm-gtk-greeter"
            wait_guest(greeter)
            wait_guest(f"! pgrep -u {args.user} -x i3")
            if args.first_boot:
                wait_guest("test ! -e /boot/grub/zackos-first-user")
            with socket.socket(socket.AF_UNIX) as monitor:
                monitor.connect(str(monitor_path))
                state = "/var/lib/lightdm/.cache/lightdm-gtk-greeter/state"
                selected = f"grep -qx 'last-user={args.user}' {state}"
                # Up and Down at the password prompt select real entries in the GTK chooser.
                vm.sendline(f"if ! {selected}; then n=$(wc -l < /etc/passwd); "
                            "xdotool key --repeat $n Up; "
                            f"for i in $(seq 1 $n); do {selected} && break; "
                            "xdotool key Down; sleep .2; done; fi")
                vm.expect(r"# ")
                wait_guest(selected)
                send_keys(monitor, "invalid" + local_password + "\n")
                time.sleep(3)
                wait_guest(greeter + f" && ! pgrep -u {args.user} -x i3")
                send_keys(monitor, local_password + "\n")
                wait_guest(f"pgrep -u {args.user} -x i3 && i3-msg -t get_version")
                wait_guest("xdotool search --onlyvisible --name '^ZackOS Panel$'")
                monitor.sendall(b"sendkey meta_l-ret 75\n")
                wait_guest("xdotool search --onlyvisible --class kitty")
                monitor.sendall(b"sendkey meta_l-d 75\n")
                wait_guest("xdotool search --onlyvisible --name '^ZackOS Applications$'")
                send_keys(monitor, "xterm\n")
                wait_guest("xdotool search --onlyvisible --class XTerm")
                vm.sendline('panel=$(xdotool search --onlyvisible --name "^ZackOS Panel$" | head -1); '
                            'xdotool mousemove --window "$panel" 30 20 click 1')
                vm.expect(r"# ")
                wait_guest("xdotool search --onlyvisible --name '^ZackOS Applications$'")
                monitor.sendall(b"sendkey esc 75\n")
                wait_guest("! xdotool search --onlyvisible --name '^ZackOS Applications$'")
                # A restarted i3 can advertise a different IPC socket to the same dock.
                vm.sendline("i3-msg restart")
                vm.expect(r"# ")
                wait_guest("i3-msg -t get_version")
                vm.sendline("i3-msg 'workspace number 2:web'")
                vm.expect(r"# ")
                wait_guest('panel=$(xdotool search --onlyvisible --name "^ZackOS Panel$" | head -1); '
                           'xdotool mousemove --window "$panel" 138 20 click 1 && '
                           'i3-msg -t get_workspaces | python3 -c \'import json,sys; '
                           'sys.exit(not any(w["focused"] and w["num"] == 1 for w in json.load(sys.stdin)))\'')
                for key in ("meta_l-f2", "meta_l-b"):
                    monitor.sendall(f"sendkey {key} 75\n".encode())
                    time.sleep(1)
            wait_guest(f"ps -u {args.user} -o tty=,comm= | grep -E 'pts/[0-9]+ +zsh' "
                       "&& xdotool search --onlyvisible --class kitty")
            wait_guest("xdotool search --onlyvisible --class thunar "
                       "&& xdotool search --onlyvisible --class firefox")
            wait_guest("findmnt -no SOURCE / | grep -q /dev/vda1 "
                       "&& test -S /run/dbus/system_bus_socket && ! pgrep -x i3-nagbar")
            vm.sendline("i3-msg exit")
            vm.expect(r"# ")
            wait_guest(greeter + f" && ! pgrep -u {args.user} -x i3")
            print("LightDM authentication and logout, i3, Kitty/Zsh, D-Bus, Firefox and Thunar passed.")
        finally:
            vm.close(force=True)


if __name__ == "__main__":
    main()
