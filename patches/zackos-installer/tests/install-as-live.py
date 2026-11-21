"""Exercise the same local installation service used by the GTK live installer."""
import json
import os
import socket
import sys
import time


def request(action, **fields):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(12)
        client.connect("/run/zackos-install.sock")
        client.sendall(json.dumps(dict(action=action, **fields)).encode() + b"\n")
        result = json.loads(client.makefile("rb").readline())
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def main():
    if os.getuid() == 0:
        raise RuntimeError("Run this check as the live account")
    options = json.load(sys.stdin)
    options.setdefault("desktop", "i3")
    disks = request("disks")["disks"]
    if len(disks) != 1 or disks[0]["bytes"] != 24 * 1024**3:
        raise RuntimeError("Expected the single 24 GiB disposable disk")
    disk = disks[0]
    print("Installer target", disk["path"], disk["model"], flush=True)
    started = request("install", disk=disk["path"], identity=disk["identity"], erase=True,
                      hostname="zackos", username="tester", **options)
    assert started.get("started"), started
    previous = ""
    for _ in range(900):
        state = request("status")
        if state["message"] != previous:
            print(state["message"], flush=True)
            previous = state["message"]
        if state["phase"] == "failed":
            raise RuntimeError("\n".join(state["log"]))
        if state["phase"] == "done":
            return
        time.sleep(1)
    raise TimeoutError("The installer did not finish")


if __name__ == "__main__":
    main()
