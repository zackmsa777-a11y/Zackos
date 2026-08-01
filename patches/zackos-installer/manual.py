#!/usr/bin/env python3
"""
ZackOS Manual Installer - "Medium" tier (Arch-manual-style).
Every command core.run() executes is echoed to the terminal first, so the
user sees exactly what's happening at each step. Plain sequential Q&A,
no curses.
"""
import sys
sys.path.insert(0, "/usr/local/lib/zackos-installer")
import core


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else (default or "")


def ask_yesno(prompt, default="no"):
    val = ask(prompt + " (yes/no)", default).lower()
    return val in ("yes", "y")


def main():
    print("=" * 60)
    print(" ZackOS Manual Installer")
    print(" Every command is shown before it runs. Ctrl-C to abort.")
    print("=" * 60)
    print()

    disks = core.list_disks()
    if not disks:
        print("No installable disks found. Aborting.")
        sys.exit(1)

    print("Available disks:")
    for i, (dev, size, model) in enumerate(disks, 1):
        print(f"  {i}) {dev}  {size}  {model}")
    choice = ask("Select target disk number", "1")
    try:
        idx = int(choice) - 1
        disk = disks[idx][0]
    except (ValueError, IndexError):
        print("Invalid selection. Aborting.")
        sys.exit(1)

    if not ask_yesno(f"Wipe {disk} and install ZackOS? This will DESTROY all data"):
        print("Aborted.")
        sys.exit(0)

    print()
    print("Window manager:")
    print("  1) i3 (i3-wm + xterm + dmenu, via Nix)")
    print("  2) none (console-only)")
    wm_raw = ask("Choice", "none")
    wm_choice = "i3" if wm_raw.strip().lower() in ("1", "i3", "i3-wm") else "none"

    print()
    print("Init system:")
    print("  1) sysvinit (default)")
    print("  2) runit")
    init_raw = ask("Choice", "sysvinit")
    init_choice = "runit" if init_raw.strip().lower() in ("2", "runit") else "sysvinit"

    hostname = ask("Hostname", "zackos")

    import getpass
    root_password = getpass.getpass("Root password: ")
    confirm = getpass.getpass("Confirm root password: ")
    if root_password != confirm:
        print("Passwords do not match. Aborting.")
        sys.exit(1)

    username = ask("Create a normal user? (leave blank to skip)", "")
    user_password = None
    if username:
        user_password = getpass.getpass(f"Password for {username}: ")

    print()
    print("--- Summary ---")
    print(f"Disk: {disk} (will be wiped)")
    print(f"WM: {wm_choice}")
    print(f"Init: {init_choice}")
    print(f"Hostname: {hostname}")
    print(f"User: {username if username else '(none, root only)'}")

    if not ask_yesno("Start installation now"):
        print("Aborted.")
        sys.exit(0)

    try:
        core.full_install(disk, wm_choice, init_choice, hostname, root_password,
                           username=username or None, user_password=user_password)
    except core.InstallError as e:
        print(f"\nInstall FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
