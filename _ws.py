#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Chrome DevTools Protocol WebSocket client (pure stdlib).

Shared by the CDP-based helper scripts (cdp_get_cookies.py, smai_capture.py, ...)
so we keep a single, correct implementation of the WebSocket handshake,
fragmented text frames, and ping/pong. Previously this class was copy-pasted
into 4-5 scripts and had already started to drift (some copies ignored the
FIN bit, others didn't answer pings).
"""
import os
import socket
import base64
import json
import struct
import urllib.request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222


class WS:
    def __init__(self, url, host=DEFAULT_HOST, port=DEFAULT_PORT):
        # url like ws://127.0.0.1:9222/devtools/browser/xxx
        assert url.startswith("ws://"), "only ws:// is supported"
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        path = "/" + path
        if ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://127.0.0.1:9222\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        header = buf.split(b"\r\n", 1)[0]
        assert b"101" in header, f"bad WS handshake: {header[:80]}"

    def _read_frame(self):
        def rec(n):
            data = b""
            while len(data) < n:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    raise ConnectionError("WS closed")
                data += chunk
            return data

        b0, b1 = rec(2)
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", rec(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", rec(8))[0]
        payload = rec(length)
        if masked:
            mask = rec(4)
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return fin, opcode, payload

    def _send_pong(self, payload):
        mask = os.urandom(4)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        header = bytes([0x8A, 0x80 | (len(payload) & 0x7F)])
        self.sock.sendall(header + mask + masked)

    def recv(self):
        fragments = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 1:  # text
                fragments.append(payload)
            elif opcode == 0:  # continuation
                fragments.append(payload)
            elif opcode == 9:  # ping -> reply pong
                self._send_pong(payload)
                continue
            elif opcode == 0xA:  # pong
                continue
            elif opcode == 8:  # close
                return None
            else:
                continue
            if fin:
                return b"".join(fragments).decode("utf-8", "replace")

    def send(self, msg):
        data = msg.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(data[i] ^ mask[i % 4] for i in range(len(data)))
        header = bytes([0x81, 0x80 | (len(data) & 0x7F)])
        if len(data) > 125:
            header = bytes([0x81, 0xFE]) + struct.pack(">H", len(data))
        self.sock.sendall(header + mask + masked)


def get_browser_ws(port=DEFAULT_PORT, host=DEFAULT_HOST):
    """Return the browser-level debugger WebSocket URL from the DevTools endpoint."""
    with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=10) as r:
        return json.loads(r.read().decode())["webSocketDebuggerUrl"]
