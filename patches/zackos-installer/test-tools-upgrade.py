"""Apply the data-only update disc to an installed, unmodified ZackOS v5.10 disk."""
import argparse
from pathlib import Path
import runpy
import secrets
import tempfile
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
vmtest = runpy.run_path(str(HERE / "test-vm-install.py"))


def upgrade(vm, installer, update):
    root_pass, user_pass = secrets.token_hex(12), secrets.token_hex(10)
    vm.boot(installer)
    live = vmtest["Serial"](vm.serial, "zackos", vmtest["LIVE_AUTH"])
    live.wait("pgrep -xu live i3 && xdotool search --onlyvisible --name '^Install ZackOS$'")
    vmtest["install_as_live"](live, "i3", "runit", root_pass, user_pass)
    vm.acpi()
    vm.off()
    live.sock.close()

    vm.boot(update, boot_from_disk=True)
    disk = vmtest["Serial"](vm.serial, root_pass, vmtest["DISK_AUTH"])
    try:
        print(disk.run("stat -c '%a %U %n' /usr/bin/su; test ! -u /usr/bin/su; "
                       "su -s /bin/sh -c 'for p in curl wget sudo; do if command -v \"$p\"; then exit 1; fi; done; "
                       "echo preserved > /home/tester/zackos-update-check' tester"), flush=True)
        vmtest["password_check"](disk, "su - root -c '/usr/bin/id -u'", root_pass, success=False)
        disk.run("mkdir -p /mnt/zacktools; mount -o ro /dev/sr0 /mnt/zacktools; "
                 "test -f /mnt/zacktools/install.py; cp /etc/shadow /run/shadow-before-tools")
        print(disk.run("python3 /mnt/zacktools/install.py"), flush=True)
        disk.run("echo '# Local policy retained' >> /etc/sudoers; chmod 440 /etc/sudoers; "
                 "python3 /mnt/zacktools/install.py; grep -qx '# Local policy retained' /etc/sudoers; "
                 "cmp /etc/shadow /run/shadow-before-tools; grep -qx preserved /home/tester/zackos-update-check")
        disk.wait("getent hosts cache.nixos.org")
        vmtest["check_system_tools"](disk, root_pass, user_pass, online=True)
        disk.run("umount /mnt/zacktools")
        vm.acpi()
        vm.off()
    finally:
        disk.sock.close()
    print("Updated a released v5.10 disk twice with its accounts, passwords and local sudoers intact.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("update", type=Path)
    args = parser.parse_args()
    opts = SimpleNamespace(firmware="bios", video="virtio", accel="kvm", move_to_sata=False)
    with tempfile.TemporaryDirectory(prefix="zackos-tools-") as work:
        vm = vmtest["Qemu"](Path(work), opts)
        try:
            upgrade(vm, args.installer.resolve(), args.update.resolve())
        finally:
            vm.close()
