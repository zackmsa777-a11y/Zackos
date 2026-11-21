#!/usr/bin/env python3
"""Install the offline ZackOS system tools from the mounted update disc."""
import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


def trusted_dir(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError(f"Expected a root-owned system directory at {path}")


def replace_file(source, dest, mode):
    # Publish the new inode only after its contents, owner and mode are ready.
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".zackos-", delete=False) as output:
        tmp = Path(output.name)
        try:
            with source.open("rb") as inp:
                shutil.copyfileobj(inp, output)
            output.flush()
            os.fchown(output.fileno(), 0, 0)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)


def install(root, media):
    if os.geteuid() != 0:
        raise SystemExit("Sign in to the installed system's root console and run this update there.")
    if root == Path("/") and not (root / "etc/zackos/profile.toml").is_file():
        raise SystemExit("Run this update on the installed ZackOS disk.")
    # Older kernel archives carried the build user's numeric owner in these paths.
    # Correct the original directories before enabling any privileged programs.
    for name in ("usr", "usr/lib", "usr/lib/modules", "boot"):
        path = root / name
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchown(fd, 0, 0)
            os.fchmod(fd, 0o755)
        finally:
            os.close(fd)
    modules = root / "usr/lib/modules/6.16.1"
    if modules.is_dir() and not modules.is_symlink():
        for directory, dirs, files in os.walk(modules):
            for path in [Path(directory), *(Path(directory) / name for name in dirs + files)]:
                os.chown(path, 0, 0, follow_symlinks=False)
    for name in ("vmlinuz-6.16.1-lfs-12.4", "config-6.16.1-lfs-12.4", "System.map-6.16.1"):
        path = root / "boot" / name
        if path.exists():
            os.chown(path, 0, 0, follow_symlinks=False)
    for name in ("etc", "usr/bin", "usr/sbin", "usr/libexec", "usr/share"):
        trusted_dir(root / name)
    for name in ("shadow", "gshadow", "shadow-", "gshadow-"):
        path = root / "etc" / name
        if path.exists():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fchown(fd, 0, 0)
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)

    payload = media / "files"
    for name in ("usr/libexec/sudo", "usr/share/zackos-tools", "etc/sudoers.d"):
        dest = root / name
        if not dest.exists():
            dest.mkdir()
            dest.chmod(0o755)
        trusted_dir(dest)
    for source in sorted((payload / "usr/libexec/sudo").iterdir()):
        replace_file(source, root / "usr/libexec/sudo" / source.name, 0o755 if source.name == "sesh" else 0o644)
    for source in sorted((payload / "usr/share/zackos-tools").iterdir()):
        replace_file(source, root / "usr/share/zackos-tools" / source.name, 0o644)
    for name in ("usr/bin/curl", "usr/bin/wget", "usr/sbin/visudo"):
        replace_file(payload / name, root / name, 0o755)
    policy = root / "etc/sudoers"
    if not os.path.lexists(policy):
        replace_file(media / "sudoers", policy, 0o440)
    subprocess.run(["/usr/sbin/chroot", str(root), "/usr/sbin/visudo", "-c"], check=True)
    for name in ("su", "passwd", "sudo"):
        replace_file(payload / "usr/bin" / name, root / "usr/bin" / name, 0o4755)
    for name in ("curl", "wget", "sudo"):
        subprocess.run(["/usr/sbin/chroot", str(root), f"/usr/bin/{name}", "--version"],
                       stdout=subprocess.DEVNULL, check=True)
    print("Installed curl, wget and sudo, and restored su and passwd. Check sudo -v from your desktop account.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"), help="extracted ZackOS root for image builders")
    args = parser.parse_args()
    install(args.root.resolve(), Path(__file__).resolve().parent)
