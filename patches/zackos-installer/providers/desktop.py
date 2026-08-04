"""WM/DE provider contract."""
from .profile import InstallerProfile


class DesktopProvider:
    def __init__(self, name):
        self.name = name

    def validate(self, profile: InstallerProfile):
        return [] if profile.desktop == self.name else []

    def install(self, context):
        raise NotImplementedError(f"{self.name} desktop provider is not implemented yet")

    def configure(self, context):
        raise NotImplementedError(f"{self.name} desktop configuration is not implemented yet")
