"""Exercise the two installed download commands as the desktop account."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import subprocess
import sys
import threading

assert os.getuid() != 0
body = b"ZackOS download check\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def fetch(url):
    return [subprocess.check_output(command + [url], timeout=30) for command in (
        ["curl", "-q", "--fail", "--silent", "--show-error", "--max-time", "20"],
        ["wget", "--timeout=20", "--tries=1", "-qO-"],
    )]


with HTTPServer(("127.0.0.1", 0), Handler) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert fetch(f"http://127.0.0.1:{server.server_port}/check") == [body, body]
    finally:
        server.shutdown()
        thread.join()

if "--https" in sys.argv[1:]:
    results = fetch("https://cache.nixos.org/nix-cache-info")
    assert all(b"StoreDir: /nix/store" in data for data in results)
    print("Verified HTTPS downloads with the system trust store.")
for name in ("curl", "wget"):
    print(subprocess.check_output([name, "--version"], text=True).splitlines()[0])
