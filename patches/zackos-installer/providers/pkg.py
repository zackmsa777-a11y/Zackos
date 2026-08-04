"""Package-manager provider contract.

A provider installs its own metadata and commands on the LFS filesystem; it
never changes the ZackOS base into Debian, Void, Gentoo, or NixOS.
"""
from .profile import InstallerProfile


class PackageProvider:
    def __init__(self, name):
        self.name = name

    def validate(self, profile: InstallerProfile):
        return [] if profile.package_manager == self.name else []

    def build(self, context):
        raise NotImplementedError(f"{self.name} build provider is not implemented yet")

    def install(self, context):
        raise NotImplementedError(f"{self.name} install provider is not implemented yet")
