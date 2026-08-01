#!/usr/bin/env python3
"""
ZackOS Easy Installer - "Easy" tier (Archinstall-style, automated).
Minimal questions, sensible defaults, hides the command-by-command
transparency that manual.py shows. Picks the only disk automatically
if there's just one candidate.
"""
import sys
sys.path.insert(0, "/usr/local/lib/zackos-installer")
import core
import getpass


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else (default or "")


def main():
    print("=" * 60)
    print(" ZackOS Easy Installer")
    print(" Defaults are chosen for you - just confirm the essentials.")
    print("=" * 60)
    print()

    disks = core.list_disks()
    if not disks:
        print("No installable disks found. Aborting.")
        sys.exit(1)

    if len(disks) == 1:
        disk = disks[0][0]
        print(f"Only one disk found: {disk} ({disks[0][1]}) - using it.")
    else:
        print("Multiple disks found:")
        for i, (dev, size, model) in enumerate(disks, 1):
            print(f"  {i}) {dev}  {size}  {model}")
        choice = ask("Select target disk number", "1")
        try:
            disk = disks[int(choice) - 1][0]
        except (ValueError, IndexError):
            print("Invalid selection. Aborting.")
            sys.exit(1)

    hostname = ask("Hostname", "zackos")
    root_password = getpass.getpass("Root password: ")
    confirm = getpass.getpass("Confirm root password: ")
    if root_password != confirm:
        print("Passwords do not match. Aborting.")
        sys.exit(1)

    print()
    print(f"Will wipe {disk}, install ZackOS with i3-wm + sysvinit (defaults),")
    print(f"hostname '{hostname}'. This DESTROYS all data on {disk}.")
    if ask("Type YES to continue", "").strip() != "YES":
        print("Aborted.")
        sys.exit(0)

    try:
        core.full_install(disk, wm_choice="i3", init_choice="sysvinit",
                           hostname=hostname, root_password=root_password)
    except core.InstallError as e:
        print(f"\nInstall FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
