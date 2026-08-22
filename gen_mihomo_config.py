#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据订阅链接 W42_SUB 生成 mihomo (Clash Meta) 配置——只抽取【单个节点】。

为什么是单节点：42w 的 cf_clearance 与抓 Cookie 时的出口 IP 绑定。该订阅里每个
节点的 servername/Host 不同 → 出口 IP 完全不同。只有当初抓 Cookie 用的那个节点
（默认“新加坡高速 05| CTCM”，可用 W42_SUB_NODE 覆盖）出口 IP 才对得上，换了节点
CF 直接重挑战 403。因此不探测、不列节点，直接把那一个节点写死成单节点隧道即可。

实现：下载订阅 → 按节点名抽取其完整定义（含它自己的 servername/uuid 等）→
写成 mihomo_config.yaml（proxies 仅此一个 + w42 选择组 + MATCH 全走 w42）。
仅用标准库。
"""
import os
import re
import urllib.request

# 当初抓 Cookie 用的节点（出口 IP 与 cf_clearance 对齐）；可用 W42_SUB_NODE 覆盖
DEFAULT_NODE = "新加坡高速 05| CTCM"


def fetch_subscription(sub):
    req = urllib.request.Request(
        sub,
        headers={"User-Agent": "clash-verge/1.10.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_node(raw, node_name):
    """从订阅里抽取指定节点名的【代理定义】整行（不是 group 引用）。"""
    pat = re.compile(r"name:\s*['\"]?" + re.escape(node_name) + r"['\"]?")
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith("-"):
            continue
        # 代理定义行含 server:；proxy-group 行（select/url-test）不含，据此区分
        if "server:" not in s:
            continue
        if pat.search(s):
            return s
    return None


def extract_node_block(raw, node_name):
    """块级兜底：多行 YAML 写法（name 独占一行、server 等在后续缩进行）时，
    抓取该节点从 '- name:' 起的整个定义块。返回行列表；找不到返回 None。"""
    pat = re.compile(r"name:\s*['\"]?" + re.escape(node_name) + r"['\"]?\s*(#.*)?$")
    lines = raw.splitlines()
    for i, s in enumerate(lines):
        t = s.strip()
        if not (t.startswith("-") and pat.search(t)):
            continue
        indent = len(s) - len(s.lstrip())
        block = [s]
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                break
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.append(nxt)
        # 必须含 server: 才是代理定义（排除同名的 group 或引用行）
        if any("server:" in b for b in block):
            return block
    return None


def main():
    sub = (os.environ.get("W42_SUB") or "").strip()
    if not sub:
        raise SystemExit("W42_SUB 未设置，无法生成 mihomo 配置")

    node = (os.environ.get("W42_SUB_NODE") or "").strip() or DEFAULT_NODE
    try:
        from urllib.parse import urlparse
        host = urlparse(sub).netloc or sub.split('/')[0]
    except Exception:
        host = sub.split('/')[0]
    print(f"[gen] 下载订阅: {host} （已脱敏，不打印完整链接以避免泄露订阅 token）")
    raw = fetch_subscription(sub)
    print(f"[gen] 订阅大小: {len(raw)} 字符")

    entry = extract_node(raw, node)
    block = None
    if entry:
        print(f"[gen] 已抽取单节点(单行写法): {node}")
    else:
        block = extract_node_block(raw, node)
        if block:
            print(f"[gen] 已抽取单节点(多行写法, {len(block)} 行定义): {node}")
    if not entry and not block:
        raise SystemExit(
            f"[gen] 在订阅中找不到节点 '{node}'。\n"
            f"      请确认 W42_SUB_NODE 与订阅里的节点名完全一致"
            f"（区分空格/竖线/大小写），或更新 W42_SUB 链接。"
        )
    proxies_section = f"  {entry}" if entry else "\n".join(block)
    if "server:" not in proxies_section:
        raise SystemExit("[gen] 抽取结果缺少 server: 字段，生成的配置无效，终止")

    cfg = f"""# 由 gen_mihomo_config.py 自动生成（单节点：{node}）
mixed-port: 7890
mode: rule
allow-lan: false
log-level: info
external-controller: 127.0.0.1:9090
proxies:
{proxies_section}
proxy-groups:
  - name: w42
    type: select
    proxies:
      - {node}
rules:
  - MATCH,w42
"""
    with open("mihomo_config.yaml", "w", encoding="utf-8") as f:
        f.write(cfg)
    print("[gen] 已生成 mihomo_config.yaml（单节点隧道，无需探测）")


if __name__ == "__main__":
    main()
