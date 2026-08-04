"""Provider metadata and support validation for the LFS-based installer."""
from dataclasses import dataclass
from .profile import InstallerProfile


@dataclass(frozen=True)
class Provider:
    kind: str
    name: str
    status: str
    notes: str


PROVIDERS = {
    "base": [Provider("base", "lfs", "stable", "Common ZackOS foundation")],
    "libc": [
        Provider("libc", "glibc", "stable", "Current verified build family"),
        Provider("libc", "musl", "scaffold", "Requires a dedicated LFS/musl build profile"),
    ],
    "package_manager": [
        Provider("package_manager", "nix", "stable", "Available in the current live build"),
        Provider("package_manager", "apt", "scaffold", "Must be built for LFS; never imports Debian"),
        Provider("package_manager", "emerge", "scaffold", "Portage provider for LFS"),
        Provider("package_manager", "xbps", "scaffold", "XBPS provider for LFS"),
    ],
    "init": [
        Provider("init", "sysvinit", "stable", "Current QEMU-verified init"),
        Provider("init", "runit", "experimental", "Existing experimental provider"),
        Provider("init", "openrc", "scaffold", "OpenRC provider planned"),
        Provider("init", "systemd", "scaffold", "Systemd provider planned"),
    ],
    "bootloader": [
        Provider("bootloader", "grub", "stable", "Current QEMU-verified bootloader"),
        Provider("bootloader", "limine", "scaffold", "BIOS/UEFI provider planned"),
        Provider("bootloader", "systemd-boot", "scaffold", "UEFI-only provider planned"),
        Provider("bootloader", "refind", "scaffold", "UEFI provider planned"),
    ],
    "desktop": [
        Provider("desktop", "none", "stable", "Console-only"),
        Provider("desktop", "i3", "stable", "Current X11 desktop path"),
        Provider("desktop", "hyprland", "scaffold", "Wayland provider planned"),
    ],
}


def provider_status(kind, name):
    for provider in PROVIDERS[kind]:
        if provider.name == name:
            return provider.status
    return "unknown"


def validate_profile(profile: InstallerProfile, require_implemented=False):
    errors = list(profile.validate_choices())
    if require_implemented:
        for kind, name in profile.to_dict().items():
            if kind == "persistence":
                continue
            if provider_status(kind, name) in ("scaffold", "unknown"):
                errors.append(f"{kind} provider {name!r} is scaffold-only; it cannot install yet")
    return errors
