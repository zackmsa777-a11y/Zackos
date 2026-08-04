#!/usr/bin/env python3
"""Interactive ZackInstaller for composing an LFS-based ZackOS profile."""
import getpass
import sys

sys.path.insert(0, "/usr/local/lib/zackos-installer")
import core
from providers.profile import InstallerProfile
from providers.registry import PROVIDERS, provider_status, validate_profile


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else (default or "")


def choose(kind, default):
    options = PROVIDERS[kind]
    print(f"\nChoose {kind.replace('_', ' ')}:")
    for index, provider in enumerate(options, 1):
        print(f"  {index}) {provider.name:<12} [{provider.status}] — {provider.notes}")
    raw = ask("Choice", default)
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1].name
    names = {p.name for p in options}
    if raw in names:
        return raw
    raise ValueError(f"Invalid {kind} choice: {raw}")


def main():
    print("=" * 68)
    print(" ZackInstaller — compose ZackOS from a common LFS foundation")
    print(" No Debian/Void/Gentoo base is selected; only LFS providers are used.")
    print(" Every command is shown before it runs. Ctrl-C aborts safely.")
    print("=" * 68)

    try:
        libc = choose("libc", "glibc")
        package_manager = choose("package_manager", "nix")
        init = choose("init", "sysvinit")
        bootloader = choose("bootloader", "grub")
        desktop = choose("desktop", "i3")
    except ValueError as exc:
        print(f"Invalid profile: {exc}")
        sys.exit(1)

    profile = InstallerProfile(
        libc=libc,
        package_manager=package_manager,
        init=init,
        bootloader=bootloader,
        desktop=desktop,
        persistence="overlay",
    )
    errors = validate_profile(profile, require_implemented=True)
    print("\n--- ZackOS LFS profile ---")
    for key, value in profile.to_dict().items():
        print(f"{key:16} {value}")
    if errors:
        print("\nThis profile is recorded as a design target but is not installable yet:")
        for error in errors:
            print(f"  - {error}")
        print("No disk has been modified. Implement its providers before selecting it.")
        sys.exit(2)

    disks = core.list_disks()
    if not disks:
        print("No installable disks found. Aborting.")
        sys.exit(1)

    print("\nAvailable disks:")
    for i, (dev, size, model) in enumerate(disks, 1):
        label = ""
        try:
            import subprocess
            label = subprocess.check_output(
                ["blkid", "-s", "LABEL", "-o", "value", dev],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            pass
        suffix = f"  [{label} - DO NOT WIPE]" if label == "ZACKPERSIST" else ""
        print(f"  {i}) {dev}  {size}  {model}{suffix}")
    try:
        disk = disks[int(ask("Select target disk number", "1")) - 1][0]
    except (ValueError, IndexError):
        print("Invalid selection. Aborting.")
        sys.exit(1)

    if not ask(f"Wipe {disk} and install ZackOS? This will DESTROY all data", "no").lower() in ("yes", "y"):
        print("Aborted.")
        sys.exit(0)

    hostname = ask("Hostname", "zackos")
    root_password = getpass.getpass("Root password: ")
    if root_password != getpass.getpass("Confirm root password: "):
        print("Passwords do not match. Aborting.")
        sys.exit(1)
    username = ask("Create a normal user? (leave blank to skip)", "")
    user_password = getpass.getpass(f"Password for {username}: ") if username else None

    print("\nProfile validated. Starting installation...")
    try:
        core.full_install(
            disk=disk,
            hostname=hostname,
            root_password=root_password,
            username=username or None,
            user_password=user_password,
            profile=profile,
        )
    except core.InstallError as exc:
        print(f"\nInstall FAILED: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
