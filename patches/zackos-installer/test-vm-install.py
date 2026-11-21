#!/usr/bin/env python3
"""Install and sign in on a disposable 24 GiB QEMU or VirtualBox disk."""
import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from PIL import Image

HERE = Path(__file__).resolve().parent
PATH = "/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
LIVE_AUTH = "/run/zackos-first-session/server.auth"
DISK_AUTH = "/run/lightdm/root/:0"


def call(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def connect(path, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX)
        try:
            client.connect(str(path))
            return client
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
            time.sleep(.2)
    raise TimeoutError(f"Missing VM socket {path}")


class Serial:
    prompt = b"ZACK_TEST>"

    def __init__(self, path, password, auth):
        self.sock = connect(path)
        self.buf = b""
        self.sock.settimeout(1)
        self.login(password, auth)

    def login(self, password, auth):
        issue = self.until(b"login:", 200)
        # agetty writes the hostname just before the prompt. PAM also prints
        # "Last login:" on subsequent logins, including after reboot.
        host = re.search(r"([\w.()-]+)$", issue)
        prompt = (host[1].encode() + b" login:") if host else b"login:"
        responses = []
        for _ in range(4):
            # agetty and PAM can flush queued input when they change tty modes.
            time.sleep(.3)
            self.send("root")
            marker, reply = self.expect((b"Password:", prompt), 45)
            responses.append(reply[-180:])
            if marker == prompt:
                continue
            time.sleep(.3)
            self.send(password)
            marker, reply = self.expect((b"# ", prompt), 45)
            responses.append(reply[-180:])
            if marker == b"# ":
                break
        else:
            raise RuntimeError("Console login did not reach the root shell: " +
                               repr(responses).replace(password, "[redacted]"))
        self.send("set +o history; set +o emacs; set +o vi; stty -echo cols 240; "
                  f"export PS1=ZACK_''TEST'>' DISPLAY=:0 XAUTHORITY={auth} PATH={PATH}; umask 077")
        self.until(self.prompt, 10)

    def until(self, marker, timeout):
        return self.expect((marker,), timeout)[1]

    def expect(self, markers, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [(self.buf.index(m), m) for m in markers if m in self.buf]
            if matches:
                _, marker = min(matches)
                head, self.buf = self.buf.split(marker, 1)
                return marker, head.decode(errors="replace").replace("\r", "").strip()
            try:
                block = self.sock.recv(16384)
                if not block:
                    raise EOFError("Serial closed: " + self.buf[-1500:].decode(errors="replace"))
                self.buf += block
            except socket.timeout:
                continue
        raise TimeoutError(f"Waiting for {markers!r}: {self.buf[-1800:]!r}")

    def send(self, command):
        self.sock.sendall(command.encode() + b"\n")

    def write_script(self, body, path):
        encoded = base64.b64encode(body.encode()).decode()
        self.send(f": > {path}")
        self.until(self.prompt, 8)
        for start in range(0, len(encoded), 800):
            self.send(f"echo {encoded[start:start+800]} | base64 -d >> {path}")
            self.until(self.prompt, 8)

    def run(self, body, timeout=40):
        self.write_script("set -e\n" + body, "/run/zack-test.sh")
        self.send("bash /run/zack-test.sh >/run/zack-test.out 2>&1; code=$?; "
                  "printf '\\nZS%s\\n' \"$code\"; base64 -w0 /run/zack-test.out; printf '\\nZE\\n'")
        response = self.until(self.prompt, timeout)
        result = re.search(r"(?m)^ZS(\d+)\n([A-Za-z0-9+/=]*)\nZE", response)
        if not result:
            raise RuntimeError("Incomplete serial command response: " + response[-700:])
        out = base64.b64decode(result[2]).decode(errors="replace").strip()
        if int(result[1]):
            raise RuntimeError(f"Guest command failed ({result[1]}): {out[-2300:]}")
        return out

    def wait(self, check, timeout=90):
        self.run(f"for n in $(seq 1 {timeout}); do if ( {check} ) >/dev/null 2>&1; "
                 f"then exit 0; fi; sleep 1; done; echo {shlex.quote('Timed out: ' + check)}; "
                 "ls -l /dev/dri 2>/dev/null || true; dmesg | grep -Ei 'vmwgfx|simpledrm|pitchlock|framebuffer' | tail -20; "
                 "tail -18 /var/log/Xorg.0.log 2>/dev/null || true; "
                 "tail -8 /var/log/lightdm/lightdm.log 2>/dev/null || true; exit 1", timeout+12)


class Qemu:
    def __init__(self, work, options):
        self.work = work
        self.options = options
        self.serial = work / "serial.sock"
        self.monitor = work / "monitor.sock"
        self.disk = work / "disk.qcow2"
        self.proc = None
        self.log = (work / "qemu.log").open("w")
        call("qemu-img", "create", "-f", "qcow2", self.disk, "24G")
        if options.firmware == "uefi":
            self.vars = work / "OVMF_VARS.fd"
            shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", self.vars)

    def boot(self, iso=None):
        opt = self.options
        self.serial.unlink(missing_ok=True)
        self.monitor.unlink(missing_ok=True)
        machine = "pc" if opt.video == "virtio" and (iso or not opt.move_to_sata) else "q35"
        bus = "virtio" if machine == "pc" else "ide"
        args = ["qemu-system-x86_64", "-machine", machine, "-accel", opt.accel, "-cpu", "max",
                "-smp", "2", "-m", "3072", "-vga", opt.video, "-display", "none",
                "-drive", f"file={self.disk},if={bus},format=qcow2",
                "-nic", "user,model=" + ("virtio-net-pci" if machine == "pc" else "e1000"),
                "-serial", f"unix:{self.serial},server=on,wait=on",
                "-monitor", f"unix:{self.monitor},server=on,wait=off"]
        if opt.firmware == "uefi":
            args += ["-drive", "if=pflash,unit=0,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
                     "-drive", f"if=pflash,unit=1,format=raw,file={self.vars}"]
        if iso:
            args += ["-cdrom", str(iso), "-boot", "once=d"]
        print("Starting QEMU", "live ISO" if iso else "installed disk", flush=True)
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=self.log)
        for _ in range(100):
            if self.proc.poll() is not None:
                raise RuntimeError((self.work / "qemu.log").read_text()[-1700:])
            if self.serial.exists():
                return
            time.sleep(.1)
        raise TimeoutError("QEMU did not create its serial socket: " + (self.work / "qemu.log").read_text()[-1700:])

    def hmp(self, command):
        with connect(self.monitor, 3) as client:
            client.settimeout(3)
            buf = b""
            while b"(qemu)" not in buf:
                buf += client.recv(4096)
            client.sendall(command.encode() + b"\n")
            buf = b""
            while b"(qemu)" not in buf:
                block = client.recv(4096)
                if not block:
                    break
                buf += block

    def screen(self, path):
        ppm = self.work / "screen.ppm"
        self.hmp(f"screendump {ppm}")
        with Image.open(ppm) as image:
            image.save(path)

    def acpi(self):
        self.hmp("system_powerdown")

    def off(self):
        self.proc.wait(timeout=85)
        assert self.proc.returncode == 0, self.proc.returncode

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self.log.close()


class VBox:
    def __init__(self, work, options):
        self.work = work
        self.name = "ZackOS-" + secrets.token_hex(3)
        self.serial = work / "serial.sock"
        disk = work / "disk.vdi"
        self.vb("createvm", "--name", self.name, "--ostype", "Linux_64", "--basefolder", work, "--register")
        self.vb("modifyvm", self.name, "--memory", "3072", "--cpus", "2", "--ioapic", "on",
                "--firmware", "efi64" if options.firmware == "uefi" else "bios",
                "--graphicscontroller", options.video, "--vram", "64", "--accelerate3d", "off",
                "--nic1", "nat", "--nictype1", "82540EM", "--uart1", "0x3f8", "4",
                "--uartmode1", "server", self.serial)
        self.vb("createmedium", "disk", "--filename", disk, "--size", "24576")
        self.vb("storagectl", self.name, "--name", "SATA", "--add", "sata", "--controller", "IntelAhci")
        self.vb("storageattach", self.name, "--storagectl", "SATA", "--port", "0", "--device", "0",
                "--type", "hdd", "--medium", disk)

    def vb(self, *args, **kwargs):
        return call("VBoxManage", *args, **kwargs)

    def boot(self, iso=None):
        self.vb("storageattach", self.name, "--storagectl", "SATA", "--port", "1", "--device", "0",
                "--type", "dvddrive", "--medium", iso or "emptydrive")
        self.vb("modifyvm", self.name, "--boot1", "dvd" if iso else "disk", "--boot2", "disk" if iso else "none")
        self.vb("startvm", self.name, "--type", "headless")

    def screen(self, path):
        self.vb("controlvm", self.name, "screenshotpng", path)
        path.chmod(0o644)

    def acpi(self):
        self.vb("controlvm", self.name, "acpipowerbutton")

    def off(self):
        for _ in range(85):
            state = subprocess.run(["VBoxManage", "showvminfo", self.name, "--machinereadable"], capture_output=True, text=True)
            if state.returncode == 0 and 'VMState="poweroff"' in state.stdout:
                return
            time.sleep(1)
        raise TimeoutError("VirtualBox did not power down: " + state.stderr[-400:])

    def close(self):
        subprocess.run(["VBoxManage", "controlvm", self.name, "poweroff"], capture_output=True)
        self.vb("unregistervm", self.name, "--delete", capture_output=True)


def capture_boot(vm, path, done):
    # Poll the actual emulated display and select a rendered splash frame by its palette.
    frame = vm.work / "boot-frame.png"
    while not done.is_set():
        try:
            vm.screen(frame)
            with Image.open(frame) as source:
                img = source.convert("RGB")
                if img.getpixel((2, 2)) == (16, 25, 28) and (248, 249, 238) in img.getdata():
                    img.save(path)
                    return
        except (OSError, TimeoutError, subprocess.SubprocessError):
            pass
        done.wait(.2)


def check_console_terminals(disk):
    print(disk.run("test \"$(ps -p $$ -o tty= | xargs)\" = ttyS0; "
                   "ps -C agetty -o tty=,args= | grep -E '^[[:space:]]*tty1[[:space:]].*agetty .*tty1'"))


def install_as_live(live, desktop, init, root_pass, user_pass):
    live.write_script((HERE / "tests/install-as-live.py").read_text(), "/tmp/zack-test-install.py")
    live.run("chmod 644 /tmp/zack-test-install.py")
    options = json.dumps(dict(desktop=desktop, init=init, root_password=root_pass, user_password=user_pass))
    print(live.run("echo " + shlex.quote(options) + " | su -s /bin/sh -c '/usr/bin/python3 /tmp/zack-test-install.py' live", 950), flush=True)


def check_vm(vm, opt):
    root_pass, user_pass = secrets.token_hex(12), secrets.token_hex(10)
    start = time.monotonic()
    vm.boot(opt.iso)
    live = Serial(vm.serial, "zackos", LIVE_AUTH)
    firmware = "test -d /sys/firmware/efi" if opt.firmware == "uefi" else "test ! -d /sys/firmware/efi"
    live.run(firmware)
    live.wait("pgrep -xu live i3 && xdotool search --onlyvisible --name '^Install ZackOS$'")
    print(f"Live i3 and installer visible in {time.monotonic()-start:.1f}s.", flush=True)
    vm.screen(opt.output / "live-installer.png")
    print(live.run("ls -l /dev/dri; xdotool getdisplaygeometry; compgen -G '/dev/dri/card[0-9]*'; "
                   "grep -m1 'Using input driver.*libinput' /var/log/Xorg.0.log; "
                   "for f in /mnt/zackos-base/{build.log,build_ch8.log,ch5_output.log,ch6_output.log,ch8_output.log,cmake_build.log}; do test ! -e \"$f\"; done"))
    kernel = live.run("cmp /mnt/cdrom/boot/vmlinuz /mnt/zackos-base/boot/vmlinuz-*; "
                      "sha256sum /mnt/cdrom/boot/vmlinuz | cut -d' ' -f1")
    assert re.fullmatch(r"[0-9a-f]{64}", kernel)
    install_as_live(live, "i3", opt.init, root_pass, user_pass)
    print("Shutting down the live session with the virtual ACPI button.", flush=True)
    vm.acpi()
    vm.off()
    live.sock.close()

    vm.boot()
    done = threading.Event()
    grab = None
    if opt.video == "virtio" and opt.accel == "kvm":
        grab = threading.Thread(target=capture_boot, args=(vm, opt.output / "boot-splash.png", done), daemon=True)
        grab.start()
    try:
        disk = Serial(vm.serial, root_pass, DISK_AUTH)
    finally:
        done.set()
        if grab:
            grab.join(5)
    disk.run(firmware)
    digest = disk.run("sha256sum /boot/vmlinuz-* | cut -d' ' -f1")
    assert digest == kernel, (digest, kernel)
    print("Matching live and installed kernel SHA-256", digest, flush=True)
    if opt.move_to_sata:
        assert disk.run("findmnt -no SOURCE /") == "/dev/sda1"
    print(disk.run("findmnt /; uname -r; id tester; if [ -d /sys/firmware/efi ]; then "
                   "test -s /boot/efi/EFI/BOOT/BOOTX64.EFI; findmnt /boot/efi; fi"))
    disk.wait("ip -4 route show default | grep -q '^default' && test -S /run/zackos-power/control && pgrep -x acpid")
    disk.wait("xdotool search --onlyvisible --class lightdm-gtk-greeter && grep -q 'Prompt greeter with' /var/log/lightdm/lightdm.log")
    vm.screen(opt.output / "login.png")
    state = "/var/lib/lightdm/.cache/lightdm-gtk-greeter/state"
    disk.run(f"if ! grep -qx last-user=tester {state}; then n=$(wc -l </etc/passwd); xdotool key --repeat $n Up; "
             f"for i in $(seq 1 $n); do grep -qx last-user=tester {state} && break; xdotool key Down; sleep .2; done; fi; "
             f"grep -qx last-user=tester {state}; xdotool type --clearmodifiers {user_pass}; xdotool key Return")
    disk.wait("pgrep -xu tester i3 && xdotool search --onlyvisible --name '^ZackOS Panel$'")
    disk.run("xdotool key Super_L+d")
    disk.wait("xdotool search --onlyvisible --name '^ZackOS Applications$'")
    vm.screen(opt.output / "launcher.png")
    disk.run("xdotool key Escape; xdotool key Super_L+Return")
    disk.wait("xdotool search --onlyvisible --class kitty && pgrep -xu tester zsh")
    time.sleep(1)
    vm.screen(opt.output / "kitty.png")
    print("Signed in to i3, opened the launcher and Kitty.", flush=True)
    if opt.init == "runit":
        check_console_terminals(disk)
        print(disk.run("shutdown -c; shutdown -r +5; zackos-power status | grep reboot; "
                       "shutdown -c; zackos-power status | grep none; "
                       "if shutdown -x; then exit 1; fi; if su -s /bin/sh -c '/sbin/shutdown -h +1' tester; then exit 1; fi"))
    disk.run("if su -s /bin/sh -c '/usr/local/bin/zackos-power poweroff' nobody; then exit 1; fi; "
             "su -s /bin/sh -c '/usr/local/bin/zackos-power status' tester")
    reboots = 2 if opt.platform == "virtualbox" and opt.firmware == "uefi" else 1
    for _ in range(reboots):
        boot_id = disk.run("cat /proc/sys/kernel/random/boot_id")
        disk.send("su -s /bin/sh -c '/usr/local/bin/zackos-power reboot' tester")
        # Ignore getty output from the old system while init stops its services.
        disk.expect((b"GNU GRUB", b"Start ZackOS", b"Linux version 6.",
                     b"enter stage: /etc/runit/1", b"INIT: version"), 180)
        disk.login(root_pass, DISK_AUTH)
        assert disk.run("cat /proc/sys/kernel/random/boot_id") != boot_id, "The VM did not restart"
        disk.wait("test -S /run/zackos-power/control && pgrep -x acpid")
        if opt.init == "runit":
            check_console_terminals(disk)
    if opt.shutdown == "acpi":
        vm.acpi()
    else:
        disk.send("su -s /bin/sh -c '/usr/local/bin/zackos-power poweroff' tester")
    vm.off()
    disk.sock.close()
    print(f"Verified user reboot and installed {opt.shutdown} poweroff.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=lambda s: Path(s).resolve())
    parser.add_argument("platform", choices=("qemu", "virtualbox"))
    parser.add_argument("video", choices=("virtio", "std", "vmware", "vmsvga", "vboxsvga"))
    parser.add_argument("firmware", choices=("bios", "uefi"))
    parser.add_argument("--init", choices=("runit", "sysvinit"), default="runit")
    parser.add_argument("--accel", choices=("kvm", "tcg"), default="kvm")
    parser.add_argument("--shutdown", choices=("user", "acpi"), default="user")
    parser.add_argument("--move-to-sata", action="store_true", help="Install on VirtIO, then boot the disk on SATA")
    parser.add_argument("--output", type=Path, required=True)
    opt = parser.parse_args()
    opt.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zackvm-") as folder:
        vm = (Qemu if opt.platform == "qemu" else VBox)(Path(folder), opt)
        try:
            check_vm(vm, opt)
        finally:
            vm.close()


if __name__ == "__main__":
    main()
