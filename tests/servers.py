"""Loopback HTTP and SOCKS5 fixtures; these tests never contact the Internet."""

import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class HTTPFixture:
    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.stalled = threading.Event()
        self.active = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                with fixture.lock:
                    fixture.requests.append((self.path, dict(self.headers)))
                    fixture.active += 1
                try:
                    path = urlsplit(self.path).path
                    if path == "/error":
                        self.send_response(503)
                        self.send_header("Content-Length", "0")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        return
                    length = 0 if path == "/empty" else (32768 if path == "/finite" else 64 * 1024 * 1024)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                    if path == "/stall":
                        fixture.stalled.set()
                        fixture.halt.wait(4)
                        return
                    chunk = b"x" * (16 if path == "/slow" else 16384)
                    sent = 0
                    while sent < length and not fixture.halt.is_set():
                        data = chunk[: min(len(chunk), length - sent)]
                        self.wfile.write(data)
                        self.wfile.flush()
                        sent += len(data)
                        if fixture.halt.wait(0.02 if path == "/slow" else 0.005):
                            break
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
                    pass
                finally:
                    with fixture.lock:
                        fixture.active -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.halt.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class SocksFixture:
    def __init__(self, *, authenticated=False):
        self.destinations = []
        self.credentials = []
        self.halt = threading.Event()
        fixture = self

        class Handler(socketserver.BaseRequestHandler):
            def read_exact(self, size):
                result = b""
                while len(result) < size:
                    data = self.request.recv(size - len(result))
                    if not data:
                        raise ConnectionError("closed")
                    result += data
                return result

            def handle(self):
                self.request.settimeout(2)
                try:
                    version, count = self.read_exact(2)
                    methods = self.read_exact(count)
                    method = 2 if authenticated else 0
                    if version != 5 or method not in methods:
                        return
                    self.request.sendall(bytes((5, method)))
                    if authenticated:
                        self.read_exact(1)
                        username = self.read_exact(self.read_exact(1)[0]).decode()
                        password = self.read_exact(self.read_exact(1)[0]).decode()
                        fixture.credentials.append((username, password))
                        self.request.sendall(b"\x01\x00")
                    version, command, reserved, address_type = self.read_exact(4)
                    if address_type == 3:
                        destination = self.read_exact(self.read_exact(1)[0]).decode()
                    elif address_type == 1:
                        destination = socket.inet_ntoa(self.read_exact(4))
                    else:
                        destination = socket.inet_ntop(socket.AF_INET6, self.read_exact(16))
                    port = int.from_bytes(self.read_exact(2), "big")
                    fixture.destinations.append((address_type, destination, port))
                    self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                    headers = b""
                    while b"\r\n\r\n" not in headers:
                        headers += self.read_exact(1)
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1048576\r\nConnection: close\r\n\r\n")
                    for _ in range(64):
                        if fixture.halt.is_set():
                            break
                        self.request.sendall(b"s" * 16384)
                        time.sleep(0.005)
                except (OSError, ConnectionError):
                    pass

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.halt.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
