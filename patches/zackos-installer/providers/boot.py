"""Bootloader provider contract."""
from .profile import InstallerProfile


class BootProvider:
    def __init__(self, name):
        self.name = name

    def validate(self, profile: InstallerProfile):
        return [] if profile.bootloader == self.name else []

    def install(self, context):
        raise NotImplementedError(f"{self.name} bootloader provider is not implemented yet")

    def write_config(self, context):
        raise NotImplementedError(f"{self.name} boot configuration is not implemented yet")
