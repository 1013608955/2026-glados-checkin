#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mihomo 启动后：通过 127.0.0.1:9090 控制 API
1) 列出订阅节点名（供用户在 W42_SUB_NODE 里钉选）
2) 若设了 W42_SUB_NODE，则把 w42 组钉到该节点
仅用标准库（urllib）。
"""
import os
import json
import urllib.request

API = "http://127.0.0.1:9090"


def call(method, path, data=None, timeout=5):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        API + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  [mihomo] API {method} {path} 失败: {e}")
        return None


def main():
    # 1) 列出订阅节点
    prov = call('GET', '/proxies/sub')
    names = []
    if prov:
        names = prov.get('data', {}).get('all', []) or []
        print(f"  [mihomo] 订阅节点数: {len(names)}")
        for n in names[:60]:
            print(f"    - {n}")
    else:
        print("  [mihomo] 未能读取订阅节点（mihomo 可能还没就绪或订阅拉取失败）")

    # 2) 钉选节点
    node = (os.environ.get('W42_SUB_NODE') or '').strip()
    if node:
        if names and node not in names:
            print(f"  [mihomo] 警告：W42_SUB_NODE='{node}' 不在节点列表中，将忽略钉选")
        else:
            r = call('PATCH', '/proxies/w42', {'name': node})
            if r is not None:
                print(f"  [mihomo] 已钉选节点: {node}")
            else:
                print(f"  [mihomo] 钉选节点失败: {node}")
    else:
        print("  [mihomo] 未设 W42_SUB_NODE，使用 w42 组默认（首个）节点")


if __name__ == '__main__':
    main()
