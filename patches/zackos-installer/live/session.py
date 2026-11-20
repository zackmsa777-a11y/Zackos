"""Prepare the temporary graphical account before SysV starts its X session."""
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

PAYLOAD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PAYLOAD))
import core


def main():
    if "zackos.desktop=1" not in Path("/proc/cmdline").read_text().split():
        raise SystemExit("The live desktop requires the graphical installer boot entry")
    profile = Path("/usr/local/share/zackos-media/profile").read_text().strip()
    current = Path("/nix/var/nix/profiles/default")
    current.unlink(missing_ok=True)
    current.symlink_to(profile)
    core.TARGET = "/"
    core.configure_i3_desktop()
    core.write_i3_config()
    fonts = Path("/etc/fonts/conf.d/10-zackos-live.conf")
    fonts.parent.mkdir(parents=True, exist_ok=True)
    fonts.write_text('<fontconfig><dir>/nix/var/nix/profiles/default/share/fonts</dir></fontconfig>\n')
    try:
        user = pwd.getpwnam("live")
    except KeyError:
        subprocess.run(["/usr/sbin/useradd", "-m", "-s", "/bin/bash", "live"], check=True)
        user = pwd.getpwnam("live")
    if user.pw_uid < 1000 or user.pw_uid == 65534:
        raise SystemExit("Invalid live account")
    # Write user-owned configuration with that user's permissions on persistent media.
    config = Path(user.pw_dir, ".config/i3/config")
    settings = (PAYLOAD / "desktop/i3.conf").read_text() + (
        '\nfor_window [class="ZackInstaller"] floating enable, border pixel 0, move position center\n'
        'exec --no-startup-id /usr/local/bin/zackos-install\n')
    subprocess.run(["/usr/bin/python3", "-I", "-c",
                    "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                    "p.parent.mkdir(parents=True,exist_ok=True); p.write_text(sys.stdin.read())", str(config)],
                   input=settings, text=True, user=user.pw_uid, group=user.pw_gid, extra_groups=[], check=True)
    launcher = Path("/usr/local/bin/zackos-install")
    launcher.write_text('#!/bin/sh\nexec /usr/local/share/zackos-media/gtk-python '
                        '/usr/local/lib/zackos-installer/live/gui.py "$@"\n')
    launcher.chmod(0o755)
    apps = Path("/usr/local/share/applications")
    apps.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PAYLOAD / "live/org.zackos.Installer.desktop", apps / "org.zackos.Installer.desktop")
    (apps / "org.zackos.Installer.desktop").chmod(0o644)
    driver = Path("/run/opengl-driver")
    if not driver.exists():
        driver.symlink_to(current)
    Path("/run/dbus").mkdir(exist_ok=True)
    if not Path("/run/dbus/system_bus_socket").exists():
        subprocess.run([str(current / "bin/dbus-daemon"), "--system", "--fork", "--nopidfile"], check=True)


if __name__ == "__main__":
    main()
