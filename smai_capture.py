#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMAI 凭证抓取 + 实时验证（替代一堆临时诊断脚本）。

前置：Chrome 以远程调试模式启动（默认 ws://127.0.0.1:9222）。
用法：
  1) 在浏览器登录 api.smai.ai（必须处于登录态，且 new_api_refresh cookie 存在）
  2) 运行：python smai_capture.py
  3) 脚本会：
       - 通过 CDP 读取 api.smai.ai 的 session / new_api_refresh / smai_api_device
       - 写入 smai_session.txt（→ 粘贴到 Secret SMAI_SESSION）
                 smai_refresh.txt（→ 粘贴到 Secret SMAI_REFRESH）
       - 实时调用 refresh → checkin，确认“刷新后续期 + 签到”整条链路可用
"""
import sys, os, socket, base64, json, struct, urllib.request, urllib.error

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

HOST, PORT = "127.0.0.1", 9222
API = "https://api.smai.ai"


class WS:
    def __init__(self, url):
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        path = "/" + path
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
            port = int(port)
        else:
            host, port = HOST, PORT
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               "Origin: http://127.0.0.1:9222\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        assert b"101" in buf.partition(b"\r\n")[0], "bad WS handshake"

    def _read_frame(self):
        def rec(n):
            d = b""
            while len(d) < n:
                c = self.sock.recv(n - len(d))
                if not c:
                    raise ConnectionError("WS closed")
                d += c
            return d
        b0, b1 = rec(2)
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", rec(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", rec(8))[0]
        p = rec(length)
        if masked:
            m = rec(4)
            p = bytes(p[i] ^ m[i % 4] for i in range(len(p)))
        return (b0 & 0x80) != 0, b0 & 0x0F, p

    def _pong(self, p):
        m = os.urandom(4)
        mk = bytes(p[i] ^ m[i % 4] for i in range(len(p)))
        self.sock.sendall(bytes([0x8A, 0x80 | (len(p) & 0x7F)]) + m + mk)

    def recv(self):
        frags = []
        while True:
            fin, op, p = self._read_frame()
            if op == 1 or op == 0:
                frags.append(p)
            elif op == 9:
                self._pong(p); continue
            elif op == 8:
                return None
            if fin:
                return b"".join(frags).decode("utf-8", "replace")

    def send(self, msg):
        d = msg.encode()
        m = os.urandom(4)
        mk = bytes(d[i] ^ m[i % 4] for i in range(len(d)))
        h = bytes([0x81, 0x80 | (len(d) & 0x7F)])
        if len(d) > 125:
            h = bytes([0x81, 0xFE]) + struct.pack(">H", len(d))
        self.sock.sendall(h + m + mk)


def get_smai_cookies():
    with urllib.request.urlopen(f"http://{HOST}:{PORT}/json/version", timeout=10) as r:
        ver = json.loads(r.read().decode())
    ws = WS(ver["webSocketDebuggerUrl"])
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
    ws.sock.close()
    return [c for c in cookies if "smai" in c.get("domain", "")]


def call(method, url, cookie=None, headers=None, data=None):
    h = {"Accept": "application/json", "User-Agent": "Mozilla/5.0",
         "Origin": API, "Referer": API + "/"}
    if cookie:
        h["Cookie"] = cookie
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode(errors="replace")[:500], dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500], dict(e.headers)


def main():
    ck = get_smai_cookies()
    by = {c["name"]: c["value"] for c in ck}
    session = by.get("session")
    refresh = by.get("new_api_refresh")
    device = by.get("smai_api_device")

    print("=== 抓到的 SMAI cookie ===")
    print("  session          :", (session or "❌ 缺失（未登录？）")[:70])
    print("  new_api_refresh  :", (refresh or "❌ 缺失（必须重新登录 api.smai.ai 获取）")[:70])
    print("  smai_api_device  :", (device or "—")[:70])

    if session:
        with open("smai_session.txt", "w", encoding="utf-8") as f:
            f.write(session)
        print("\n✅ 已写入 smai_session.txt  → 粘贴到 Secret SMAI_SESSION")
    if refresh:
        with open("smai_refresh.txt", "w", encoding="utf-8") as f:
            f.write(refresh)
        print("✅ 已写入 smai_refresh.txt  → 粘贴到 Secret SMAI_REFRESH")

    if not (session and refresh):
        print("\n⚠️ 缺少 session 或 new_api_refresh，无法验证。请先在浏览器登录 api.smai.ai 后重跑本脚本。")
        return

    print("\n=== 实时验证：refresh → checkin ===")
    st, body, hdr = call("POST", f"{API}/api/user/auth/refresh",
                         cookie=f"new_api_refresh={refresh}")
    print("  refresh HTTP", st, "|", body[:120])
    if st != 200:
        print("  ❌ refresh 失败，请重新登录 api.smai.ai 并重抓 new_api_refresh")
        return
    new_refresh = None
    sc = hdr.get("Set-Cookie", "")
    if "new_api_refresh=" in sc:
        new_refresh = sc.split("new_api_refresh=")[1].split(";")[0]
        print("  ↻ 新 refresh token（下次请用这个）:", new_refresh[:40], "...")
    try:
        access_token = json.loads(body).get("data", {}).get("access_token", "")
    except Exception:
        access_token = ""
    uid = json.loads(body).get("data", {}).get("session", {}).get("sub") or "1207"

    # 用续期后的 session cookie + Bearer 做签到
    st2, body2, _ = call("POST", f"{API}/api/user/checkin",
                         cookie=f"session={session}",
                         headers={"Smai-Api-User": str(uid), "Content-Type": "application/json",
                                   "Authorization": f"Bearer {access_token}"}, data=b"{}")
    print("  checkin  HTTP", st2, "|", body2[:200])
    if st2 == 200 and '"success":true' in body2.replace(" ", ""):
        print("\n🎉 整条链路验证通过：GitHub Actions 里加上 SMAI_REFRESH 后 SMAI 签到即可成功。")
    else:
        print("\n⚠️ checkin 未成功，请检查输出（可能需要 SMAI_USER_ID 或账号状态）。")


if __name__ == "__main__":
    main()
