"""Libc provider contract."""
from .profile import InstallerProfile


class LibcProvider:
    def __init__(self, name):
        self.name = name

    def validate(self, profile: InstallerProfile):
        return [] if profile.libc == self.name else []

    def build(self, context):
        raise NotImplementedError(f"{self.name} is a build-time LFS provider")

    def install(self, context):
        raise NotImplementedError(f"{self.name} must be selected before the LFS toolchain is built")
