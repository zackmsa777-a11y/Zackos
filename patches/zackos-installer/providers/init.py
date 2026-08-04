"""Init-system provider contract."""
from .profile import InstallerProfile


class InitProvider:
    def __init__(self, name):
        self.name = name

    def validate(self, profile: InstallerProfile):
        return [] if profile.init == self.name else []

    def install(self, context):
        raise NotImplementedError(f"{self.name} init provider is not implemented yet")

    def configure(self, context):
        raise NotImplementedError(f"{self.name} init configuration is not implemented yet")
