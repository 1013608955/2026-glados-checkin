#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Chrome DevTools Protocol client (pure stdlib) to read cookies."""
import sys
import json

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from _ws import WS, get_browser_ws


def main():
    ws = WS(get_browser_ws())
    print("connected:", get_browser_ws()[:55])
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
