#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Chrome DevTools Protocol client (pure stdlib) to read cookies."""
import sys, os, socket, base64, json, struct, urllib.request

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

PORT = 9222
TARGET_HOST = "127.0.0.1"

class WS:
    def __init__(self, url):
        # url like ws://127.0.0.1:9222/devtools/browser/xxx
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        path = "/" + path
        if ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = TARGET_HOST, PORT
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
        # read headers
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        # verify 101
        header, _, _ = buf.partition(b"\r\n")
        assert b"101" in header, f"bad WS handshake: {header[:80]}"
        self._buf = b""

    def _read_frame(self):
        # read one frame
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
        # simple: read frames until we get a text frame fully (handles continuation)
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


def main():
    with urllib.request.urlopen(f"http://{TARGET_HOST}:{PORT}/json/version", timeout=10) as r:
        ver = json.loads(r.read().decode())
    ws = WS(ver["webSocketDebuggerUrl"])
    print("connected:", ver["webSocketDebuggerUrl"][:55])
    ws.send(json.dumps({"id": 1, "method": "Storage.enable"}))
    ws.send(json.dumps({"id": 2, "method": "Storage.getCookies"}))
    cookies = []
    while True:
        msg = ws.recv()
        if msg is None:
            break
        try:
            obj = json.loads(msg)
        except Exception:
            continue
        if obj.get("id") == 2:
            cookies = obj.get("result", {}).get("cookies", [])
            break

    print(f"total cookies: {len(cookies)}")
    out = {}
    for c in cookies:
        if "42w" in c.get("domain", ""):
            out[c["name"]] = c["value"]
            print(f"  {c['domain']} {c['name']} = {c['value'][:60]}")
    cookie_str = "; ".join(f"{k}={v}" for k, v in out.items())
    with open("cookie_live.txt", "w", encoding="utf-8") as f:
        f.write(cookie_str)
    print("Saved cookie_live.txt with", list(out.keys()))
    ws.sock.close()


if __name__ == "__main__":
    main()
