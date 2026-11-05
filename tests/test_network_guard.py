"""The suite-wide network guard in conftest.py."""

import http.server
import socket
import threading
import urllib.request

import pytest


def test_non_loopback_connection_is_refused_and_recorded(network_guard):
    with pytest.raises(OSError):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    with pytest.raises(OSError):
        socket.getaddrinfo("registry-1.docker.io", 443)
    assert network_guard == ["192.0.2.1", "registry-1.docker.io"]
    network_guard.clear()  # provoked on purpose: do not fail this test at teardown


def test_loopback_server_still_works(network_guard):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5) as resp:
            assert resp.read() == b"ok"
    finally:
        server.shutdown()
    assert network_guard == []
