"""Local, live-only installation service. GTK runs as the unprivileged live user."""
from collections import deque
import json
import os
from pathlib import Path
import pwd
import re
import socket
import socketserver
import struct
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
from providers.profile import InstallerProfile

SOCKET = "/run/zackos-install.sock"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def validate(job):
    disk = next((d for d in core.disk_inventory() if d["path"] == job.get("disk")), None)
    if not disk or job.get("erase") is not True or disk["identity"] != job.get("identity"):
        raise ValueError("Select the destination again and confirm that it can be erased.")
    if job.get("desktop") not in ("i3", "none", "plasma-x11") or job.get("init") not in ("runit", "sysvinit"):
        raise ValueError("Choose an available desktop and init system.")
    hostname = job["hostname"]
    if len(hostname) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", part)
                                      for part in hostname.split(".")):
        raise ValueError("Enter a valid computer name.")
    username = job["username"]
    reserved = {line.split(":", 1)[0] for line in (core.MEDIA_BASE / "etc/passwd").read_text().splitlines()}
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username) or username in reserved:
        raise ValueError("Choose an unused, lowercase username.")
    for field in ("root_password", "user_password"):
        value = job[field]
        if not isinstance(value, str) or not value or len(value) > 512 or any(c in value for c in "\r\n\x00"):
            raise ValueError("Enter valid passwords for both accounts.")


def worker():
    os.umask(0o022)
    job = json.load(sys.stdin)
    try:
        validate(job)
        def progress(step, message):
            print("@@stage " + json.dumps([step, message]), flush=True)
        core.full_install(disk=job["disk"], identity=job["identity"], hostname=job["hostname"],
                          username=job["username"], user_password=job["user_password"],
                          root_password=job["root_password"], progress=progress,
                          profile=InstallerProfile(desktop=job["desktop"], init=job["init"]))
    except Exception as error:
        print("@@error " + str(error), flush=True)
        return 1
    return 0


class Installer:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"phase": "ready", "step": 0, "message": ""}
        self.log = deque(maxlen=32)

    def snapshot(self):
        with self.lock:
            return {**self.state, "log": list(self.log)}

    def run(self, job):
        error = "Installation stopped. Check the installation details before trying again."
        try:
            with subprocess.Popen([sys.executable, "-u", __file__, "--worker"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  errors="replace") as process:
                json.dump(job, process.stdin)
                process.stdin.close()
                job.clear()
                for raw in process.stdout:
                    line = ANSI.sub("", raw.strip())[-1200:]
                    with self.lock:
                        if line.startswith("@@stage "):
                            step, message = json.loads(line[8:])
                            self.state.update(step=step, message=message)
                        elif line.startswith("@@error "):
                            error = line[8:]
                        elif line:
                            self.log.append(line)
                success = process.wait() == 0
        except (OSError, ValueError) as exc:
            success, error = False, str(exc)
        with self.lock:
            self.state.update(phase="done" if success else "failed",
                              message="Installation complete" if success else error)

    def handle(self, command):
        action = command.get("action")
        if action == "disks":
            return {"disks": core.disk_inventory()}
        if action == "status":
            return self.snapshot()
        if action == "install":
            validate(command)
            with self.lock:
                if self.state["phase"] in ("working", "done"):
                    raise ValueError("An installation has already started.")
                self.log.clear()
                self.state = {"phase": "working", "step": 0, "message": "Checking the destination",
                              "disk": command["disk"], "username": command["username"]}
            threading.Thread(target=self.run, args=(command,), daemon=True).start()
            return {"started": True}
        if action == "reboot" and self.snapshot()["phase"] == "done":
            subprocess.Popen(["/sbin/shutdown", "-r", "now"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"restarting": True}
        raise ValueError("This action is unavailable.")


class Request(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(4)
        _, uid, _ = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid not in (0, self.server.live_uid):
            return
        try:
            message = self.rfile.readline(16385)
            if len(message) > 16384:
                raise ValueError("The request is too large.")
            result = self.server.installer.handle(json.loads(message))
        except (KeyError, TypeError, AttributeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            result = {"error": str(exc)}
        self.wfile.write((json.dumps(result) + "\n").encode())


def serve():
    if "zackos.desktop=1" not in Path("/proc/cmdline").read_text().split() or not core.MEDIA_BASE.is_mount():
        raise SystemExit("The graphical installer is available on ZackOS live media")
    user = pwd.getpwnam("live")
    if user.pw_uid < 1000 or user.pw_uid == 65534:
        raise SystemExit("Invalid live desktop account")
    os.umask(0o177)
    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.UnixStreamServer(SOCKET, Request) as server:
        server.live_uid = user.pw_uid
        server.installer = Installer()
        os.chown(SOCKET, user.pw_uid, user.pw_gid)
        os.chmod(SOCKET, 0o600)
        server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        sys.exit(worker())
    serve()
