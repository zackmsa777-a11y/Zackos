"""Inspect the running desktop over its user's Plasma session bus."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

account = pwd.getpwnam("tester")
pid = subprocess.check_output(["pgrep", "-o", "-u", account.pw_name, "-f", "/[.]?plasmashell(-wrapped)?( |$)"], text=True).strip()
environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
os.initgroups(account.pw_name, account.pw_gid)
os.setgid(account.pw_gid)
os.setuid(account.pw_uid)
os.environ.clear()
for entry in environment:
    if b"=" in entry:
        key, value = entry.split(b"=", 1)
        os.environb[key] = value
os.chdir(account.pw_dir)


def plasma(method, *args):
    return subprocess.check_output(["dbus-send", "--session", "--print-reply=literal",
                                    "--dest=org.kde.plasmashell", "/PlasmaShell",
                                    "org.kde.PlasmaShell." + method, *args], text=True).strip()


def setting(file, group, key):
    reader = shutil.which("kreadconfig6") or next(Path("/nix/store").glob("*/bin/kreadconfig6"))
    return subprocess.check_output([reader, "--file", file, "--group", group, "--key", key], text=True).strip()


if sys.argv[1] == "layout":
    theme = (Path.home() / ".config/kdedefaults/package").read_text().strip()
    assert theme == "org.zackos.orbit.desktop", theme
    assert setting("kdeglobals", "General", "ColorScheme") == "ZackOSOrbit"
    assert setting("konsolerc", "Desktop Entry", "DefaultProfile") == "ZackOS Orbit.profile"
    layout = json.loads(plasma("evaluateScript", """string:print(JSON.stringify(panels().map(p => ({
        edge: p.location, floating: p.floating, length: p.lengthMode,
        widgets: p.widgets().map(w => {
            w.currentConfigGroup = ["General"];
            return {type: w.type, icon: w.readConfig("icon", ""), launchers: w.readConfig("launchers", [])};
        })
    }))))"""))
    print(json.dumps(layout), flush=True)
    assert sorted(p["edge"] for p in layout) == ["bottom", "top"], layout
    assert all(p["floating"] for p in layout), layout
    top = next(p for p in layout if p["edge"] == "top")
    top_widgets = {w["type"]: w for w in top["widgets"]}
    assert top_widgets["org.kde.plasma.kickoff"]["icon"] == "zackos-orbit"
    assert "org.kde.plasma.digitalclock" in top_widgets and "org.kde.plasma.systemtray" in top_widgets
    dock = next(p for p in layout if p["edge"] == "bottom")
    assert dock["length"] == "fit", dock
    pins = next(w["launchers"] for w in dock["widgets"] if w["type"] == "org.kde.plasma.icontasks")
    for app in ("org.kde.dolphin", "org.kde.konsole", "systemsettings"):
        assert "applications:" + app + ".desktop" in pins, pins
elif sys.argv[1] == "launcher":
    plasma("activateLauncherMenu")
elif sys.argv[1] == "konsole":
    subprocess.Popen(["konsole", "--separate"], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    raise ValueError("Unknown session check")
