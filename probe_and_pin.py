#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动探测可用节点：遍历订阅节点，逐个切到 w42 组，用真实 Cookie 请求
42w 的 /api/user/self；命中 HTTP 200 + JSON + success 即钉住该节点并退出。

为什么需要探测：cf_clearance 与出口 IP 绑定，只有出口 IP 与当初获取 Cookie 时
一致（或该 IP 未被 Cloudflare 挑战）的节点才能通过，靠名字猜不可靠。

环境变量：
  W42_COOKIE     必需，用于真实校验（取第一个账号的 cookie）
  W42_UID        可选，New-Api-User 头
  W42_SUB_NODE   可选，优先先试这个节点名（命中则省时间）
  W42_NODE_FILTER 可选，只试名字含该关键词的节点（如 "新加坡"），逗号分隔多个
  W42_PROBE_MAX  可选，最多试多少个节点（默认 25，防止工作流超时）
仅用标准库。
"""
import os
import json
import time
import urllib.request

API = "http://127.0.0.1:9090"
PROXY = "http://127.0.0.1:7890"
TARGET = "https://api.42w.shop/api/user/self"
GROUP = "w42"


def api_call(method, path, data=None, timeout=6):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except Exception as e:
        print(f"  [api] {method} {path} 失败: {e}")
        return None


def get_nodes():
    """取订阅 provider 里的节点名列表"""
    for path in ('/proxies/sub', f'/proxies/{GROUP}'):
        d = api_call('GET', path)
        if d:
            names = d.get('all') or d.get('data', {}).get('all') or []
            # 过滤掉组名本身与内置策略
            names = [n for n in names if n not in (GROUP, 'sub', 'DIRECT', 'REJECT', 'PASS')]
            if names:
                return names
    return []


def switch(node):
    return api_call('PATCH', f'/proxies/{GROUP}', {'name': node}) is not None


def test_42w(cookie, uid, timeout=12):
    """经本地代理请求 42w，返回 (ok, 说明)"""
    handler = urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY})
    opener = urllib.request.build_opener(handler)
    headers = {
        'Accept': 'application/json',
        'Cookie': cookie,
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    }
    if uid:
        headers['New-Api-User'] = uid
    req = urllib.request.Request(TARGET, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as r:
            ctype = r.headers.get('Content-Type', '')
            body = r.read().decode('utf-8', errors='replace')
            if 'application/json' not in ctype.lower():
                return False, f"HTTP {r.status} 非JSON({ctype[:30]})"
            try:
                j = json.loads(body)
            except Exception:
                return False, f"HTTP {r.status} JSON解析失败"
            if j.get('success'):
                d = j.get('data', {})
                who = d.get('display_name') or d.get('username') or d.get('id')
                return True, f"OK 用户={who}"
            return False, f"HTTP {r.status} success=false: {str(j.get('message'))[:40]}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}（Cloudflare 拦截/未授权）"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:40]}"


def main():
    raw_ck = (os.environ.get('W42_COOKIE') or '').strip()
    if not raw_ck:
        print("  [probe] 未设 W42_COOKIE，跳过探测（将用默认节点）")
        return
    cookie = [c.strip() for c in (raw_ck.split('\n') if '\n' in raw_ck else raw_ck.split('&')) if c.strip()][0]
    raw_uid = (os.environ.get('W42_UID') or '').strip()
    uid = [u.strip() for u in (raw_uid.split('\n') if '\n' in raw_uid else raw_uid.split('&')) if u.strip()]
    uid = uid[0] if uid else ''

    nodes = get_nodes()
    if not nodes:
        print("  [probe] 未取到节点列表（订阅可能拉取失败），跳过探测")
        return
    print(f"  [probe] 订阅节点数: {len(nodes)}")

    # 关键词过滤
    flt = [k.strip() for k in (os.environ.get('W42_NODE_FILTER') or '').split(',') if k.strip()]
    if flt:
        picked = [n for n in nodes if any(k in n for k in flt)]
        if picked:
            print(f"  [probe] 关键词 {flt} 命中 {len(picked)} 个节点")
            nodes = picked
        else:
            print(f"  [probe] 关键词 {flt} 未命中任何节点，改用全部")

    # 优先节点提到最前
    pref = (os.environ.get('W42_SUB_NODE') or '').strip()
    if pref and pref in nodes:
        nodes.remove(pref)
        nodes.insert(0, pref)
        print(f"  [probe] 优先试: {pref}")
    elif pref:
        print(f"  [probe] 优先节点 '{pref}' 不在列表中，忽略")

    try:
        limit = int(os.environ.get('W42_PROBE_MAX') or 25)
    except ValueError:
        limit = 25
    nodes = nodes[:limit]

    for i, node in enumerate(nodes, 1):
        if not switch(node):
            print(f"  [{i}/{len(nodes)}] {node} -> 切换失败，跳过")
            continue
        time.sleep(1.2)  # 等连接切换生效
        ok, why = test_42w(cookie, uid)
        print(f"  [{i}/{len(nodes)}] {node} -> {why}")
        if ok:
            print(f"  ✅ [probe] 命中可用节点并已钉住: {node}")
            print(f"  💡 建议把它填进 Secret W42_SUB_NODE 以加速后续运行: {node}")
            return
    print("  ⛔ [probe] 所试节点均无法通过 Cloudflare 校验。")
    print("     可能原因：cf_clearance 已与旧出口 IP 绑定 / 订阅节点 IP 均被 CF 挑战。")
    print("     处置：本机重新抓一次 Cookie（cdp_get_cookies.py）后更新 W42_COOKIE，")
    print("           并确保本机 Clash 用的节点也在该订阅内。")


if __name__ == '__main__':
    main()
