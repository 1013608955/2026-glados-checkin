#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMAI 凭证抓取 + 实时验证（替代一堆临时诊断脚本）。

前置：Chrome 以远程调试模式启动（默认 ws://127.0.0.1:9222）。
用法：
  1) 在浏览器登录 api.smai.ai（必须处于登录态，且 new_api_refresh cookie 存在）
  2) 运行：python smai_capture.py
  3) 脚本会：
       - 通过 CDP 读取 api.smai.ai 的 session / new_api_refresh / smai_api_device
       - 实时 refresh（这会【轮换并吊销】写入文件前的旧 new_api_refresh）
       - 把【轮换后的】新 new_api_refresh 写回 smai_refresh.txt
         （旧 token 已作废，绝不能再用）
       - 把刷新后下发的 session 写回 smai_session.txt
       - 实时调用 refresh → checkin，确认整条链路可用
  4) 把两个 txt 内容分别粘贴到 GitHub Secrets：
       smai_session.txt  → SMAI_SESSION
       smai_refresh.txt  → SMAI_REFRESH
"""
import sys
import os
import re
import json
import urllib.request
import urllib.error

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from _ws import WS, get_browser_ws

HOST, PORT = "127.0.0.1", 9222
API = "https://api.smai.ai"


def get_smai_cookies():
    ws = WS(get_browser_ws())
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
        if isinstance(cookie, dict):
            cookie = "; ".join(f"{k}={v}" for k, v in cookie.items())
        h["Cookie"] = cookie
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode(errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), dict(e.headers)


def _ok(body2):
    cleaned = body2.replace(" ", "")
    return '"success":true' in cleaned or '已签到' in body2 or '"code":0' in cleaned


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

    if not (session and refresh):
        print("\n⚠️ 缺少 session 或 new_api_refresh，无法验证。请先在浏览器登录 api.smai.ai 后重跑本脚本。")
        return

    print("\n=== 实时验证：refresh → checkin ===")
    st, body, hdr = call("POST", f"{API}/api/user/auth/refresh",
                         cookie={"new_api_refresh": refresh, "session": session})
    print("  refresh HTTP", st, "|", body[:120])
    if st != 200:
        print("  ❌ refresh 失败，请重新登录 api.smai.ai 并重抓 new_api_refresh")
        return

    access_token = ""
    new_refresh = None
    new_session = None
    try:
        access_token = json.loads(body).get("data", {}).get("access_token", "")
    except Exception:
        pass
    sc = hdr.get("Set-Cookie", "")
    m = re.search(r'new_api_refresh=([^;,\s]+)', sc)
    if m:
        new_refresh = m.group(1)
        print("  ↻ 新 refresh token（轮换后）:", new_refresh[:40], "...")
    ssm = re.search(r'(?:^|,\s*)session=([^;,\s]+)', sc)
    if ssm:
        new_session = ssm.group(1)
        print("  ↻ 刷新下发的 session:", new_session[:40], "...")

    # 取 uid（与 checkin.py 一致：GET /api/user/self 带 Bearer）
    uid = ""
    for sess_candidate in [new_session or session, session]:
        try:
            st_s, body_s, _ = call("GET", f"{API}/api/user/self",
                                   cookie={"session": sess_candidate},
                                   headers={"Authorization": f"Bearer {access_token}"})
            info = json.loads(body_s)
            if info.get("success") and info.get("data", {}).get("id"):
                uid = str(info["data"]["id"])
                print("  SMAI 用户 ID:", uid, "| username:", info["data"].get("username", ""))
                break
        except Exception as e:
            print("  self 取 uid 失败（尝试下一 session）:", e)
    if not uid:
        uid = "1207"
        print("  ⚠️ 未取到 uid，兜底用 1207（建议 Secrets 加 SMAI_USER_ID）")

    # 用【刷新下发的 session】打 checkin（浏览器行为：用 refresh 后下发的 session）
    use_session = new_session or session
    st2, body2, _ = call("POST", f"{API}/api/user/checkin",
                         cookie={"session": use_session},
                         headers={"Smai-Api-User": str(uid), "Content-Type": "application/json",
                                  "Authorization": f"Bearer {access_token}"}, data=b"{}")
    print("  checkin (新session) HTTP", st2, "|", body2[:200])
    ok_new = (st2 == 200 and _ok(body2))

    # 也测一下【原 session】（验证 checkin.py 现有逻辑是否够用）
    ok_orig = False
    if new_session and new_session != session:
        st3, body3, _ = call("POST", f"{API}/api/user/checkin",
                             cookie={"session": session},
                             headers={"Smai-Api-User": str(uid), "Content-Type": "application/json",
                                      "Authorization": f"Bearer {access_token}"}, data=b"{}")
        print("  checkin (原session) HTTP", st3, "|", body3[:200])
        ok_orig = (st3 == 200 and _ok(body3))

    if ok_new:
        print("\n🎉 整条链路验证通过（使用轮换后的 session + Bearer）。")
    else:
        print("\n⚠️ checkin 未成功，请检查输出。")

    # ---------- 写文件（关键：写【轮换后】的新值） ----------
    final_session = new_session or session
    with open("smai_session.txt", "w", encoding="utf-8") as f:
        f.write(final_session)
    print("\n✅ 已写入 smai_session.txt  → 粘贴到 Secret SMAI_SESSION")

    if new_refresh:
        with open("smai_refresh.txt", "w", encoding="utf-8") as f:
            f.write(new_refresh)
        print("✅ 已写入 smai_refresh.txt（轮换后的新 token，旧 token 已吊销）→ 粘贴到 Secret SMAI_REFRESH")
    else:
        print("⚠️ 未解析到新 refresh token，smai_refresh.txt 未更新（旧 token 可能已吊销，需重新登录抓取）")

    print(f"\n核对：原 session 路径 checkin={'通过' if ok_orig else '失败'} | 新 session 路径 checkin={'通过' if ok_new else '失败'}")


if __name__ == "__main__":
    main()
