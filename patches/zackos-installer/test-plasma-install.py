"""Install the online Plasma profile and capture its first ordinary-user session."""
import argparse
from pathlib import Path
import runpy
import secrets
import tempfile
import time
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageOps, ImageStat

HERE = Path(__file__).resolve().parent
VM = runpy.run_path(str(HERE / "test-vm-install.py"))
PROBE = "python3 /tmp/zackos-check-plasma.py "


def key(vm, name):
    vm.hmp("sendkey " + name + " 35")
    time.sleep(.09)


def sign_in(vm, disk, password):
    state = "/var/lib/lightdm/.cache/lightdm-gtk-greeter/state"
    disk.wait("pgrep -f '[l]ightdm-gtk-greeter' && grep -q 'Prompt greeter with' /var/log/lightdm/lightdm.log")
    selected = "grep -qx last-user=tester " + state
    if disk.run("if " + selected + "; then echo yes; fi") != "yes":
        count = int(disk.run("wc -l </etc/passwd"))
        for _ in range(count):
            key(vm, "up")
        for _ in range(count):
            if disk.run("if " + selected + "; then echo yes; fi") == "yes":
                break
            key(vm, "down")
    disk.run(selected)
    for character in password:
        key(vm, character)
    key(vm, "ret")
    disk.wait("pgrep -u tester -f '/[.]?kwin_x11(-wrapped)?( |$)' && pgrep -u tester -f '/[.]?plasmashell(-wrapped)?( |$)'", 100)


def capture_wallpaper(vm, output):
    # Wait for both the wallpaper and the bottom dock to finish appearing.
    with Image.open(HERE / "desktop/login/sign-in.png") as source:
        art = source.convert("RGB")
    until = time.monotonic() + 45
    ready = False
    while time.monotonic() < until:
        vm.screen(output)
        with Image.open(output) as source:
            frame = source.convert("RGB")
        w, h = frame.size
        area = (w//10, h//4, 9*w//10, 3*h//4)
        difference = ImageChops.difference(frame, ImageOps.fit(art, frame.size))
        dock = (w//2-w//16, h-h//14, w//2+w//16, h-4)
        visible = max(ImageStat.Stat(difference.crop(area)).mean) < 10 and max(ImageStat.Stat(difference.crop(dock)).mean) > 18
        if ready and visible:
            return frame
        ready = visible
        time.sleep(.6)
    raise RuntimeError("The Orbit wallpaper did not appear on the guest display")


def check(vm, iso, init, out):
    root_pass, user_pass = secrets.token_hex(12), secrets.token_hex(10)
    vm.boot(iso)
    live = VM["Serial"](vm.serial, "zackos", VM["LIVE_AUTH"])
    live.wait("pgrep -xu live i3 && xdotool search --onlyvisible --name '^Install ZackOS$'")
    live.wait("getent hosts cache.nixos.org")
    try:
        VM["install_as_live"](live, "plasma-x11", init, root_pass, user_pass)
    except Exception:
        vm.screen(out / "installer-display.png")
        print(live.run("free -m; df -h /mnt/target 2>/dev/null || :; "
                       "dmesg | grep -Ei 'out of memory|oom-kill|killed process|I/O error' | tail -16 || :"), flush=True)
        raise
    vm.acpi()
    vm.off()
    live.sock.close()

    vm.boot()
    disk = VM["Serial"](vm.serial, root_pass, VM["DISK_AUTH"])
    try:
        pid1 = disk.run("readlink /proc/1/exe")
        assert Path(pid1).name == {"runit": "runit", "sysvinit": "init"}[init], pid1
        print("PID 1:", pid1, flush=True)
        VM["check_system_tools"](disk, root_pass, user_pass)
        print(disk.run("cat /etc/zackos/profile.toml; grep -qx user-session=zackos-plasma /etc/lightdm/lightdm.conf; "
                       "getent passwd tester | grep -q ':/nix/var/nix/profiles/default/bin/zsh$'; "
                       "test -s /home/tester/.config/kitty/kitty.conf; test -s /home/tester/.config/fastfetch/config.jsonc"), flush=True)
        disk.write_script((HERE / "tests/plasma-session.py").read_text(), "/tmp/zackos-check-plasma.py")
        sign_in(vm, disk, user_pass)
        disk.run("test \"$(stat -c '%u:%a' /tmp/.ICE-unix)\" = 0:1777")
        # Wait for activity creation before inspecting the first layout.
        disk.wait("! pgrep -u tester -f '/[.]?ksplashqml(-wrapped)?( |$)'", 100)
        disk.wait(PROBE + "layout", 70)
        print(disk.run(PROBE + "layout"), flush=True)
        desktop = capture_wallpaper(vm, out / "plasma-desktop.png")

        disk.run(PROBE + "launcher")
        menu = out / "plasma-launcher.png"
        w, h = desktop.size
        region = (0, h//12, w//2, 3*h//4)
        for _ in range(20):
            time.sleep(.4)
            vm.screen(menu)
            with Image.open(menu) as image:
                diff = ImageChops.difference(desktop, image.convert("RGB")).crop(region)
            if max(ImageStat.Stat(diff).mean) > 9:
                break
        else:
            raise RuntimeError("The application launcher did not appear")
        key(vm, "esc")
        disk.run(PROBE + "konsole")
        disk.wait("ls /home/tester/.cache/zsh/zcompdump-* >/dev/null && ! pgrep -u tester -x fastfetch")
        for character in "ps -p 1 -o comm= | tee pid1":
            key(vm, {" ": "spc", "-": "minus", "=": "equal", "|": "shift-backslash"}.get(character, character))
        key(vm, "ret")
        expected = "runit" if init == "runit" else "init"
        disk.wait(f"grep -qx {expected} /home/tester/pid1", 20)
        time.sleep(.4)
        vm.screen(out / "plasma-konsole.png")
        vm.acpi()
        vm.off()
    except Exception:
        vm.screen(out / "last-display.png")
        print(disk.run("tail -30 /var/log/lightdm/lightdm.log || :; "
                       "tail -30 /home/tester/.xsession-errors 2>/dev/null || :; "
                       "cat /home/tester/.config/kdedefaults/package 2>/dev/null || :; "
                       + PROBE + "layout 2>&1 || :"), flush=True)
        raise
    finally:
        disk.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("init", choices=("runit", "sysvinit"))
    parser.add_argument("--memory-mib", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    options = SimpleNamespace(video="virtio", firmware="bios", accel="kvm", move_to_sata=False, memory=args.memory_mib)
    with tempfile.TemporaryDirectory(prefix="zackos-plasma-") as directory:
        vm = VM["Qemu"](Path(directory), options)
        try:
            check(vm, args.iso.resolve(), args.init, args.output)
        finally:
            vm.close()


if __name__ == "__main__":
    main()
