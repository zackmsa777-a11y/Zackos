"""LFS base provider contract."""
from .profile import InstallerProfile


class LFSBaseProvider:
    name = "lfs"

    def validate(self, profile: InstallerProfile):
        return [] if profile.base == self.name else ["ZackOS requires the LFS base provider"]

    def build(self, context):
        raise NotImplementedError("The LFS build pipeline is supplied by scripts/ and scripts_chroot/")

    def install(self, context):
        raise NotImplementedError("The live installer copies the prepared LFS rootfs")
