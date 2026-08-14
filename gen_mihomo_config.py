#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据订阅链接 W42_SUB 生成 mihomo (Clash Meta) 配置。

关键点：多数订阅（如 ziyoufly）返回的是“完整配置”（含 mixed-port/dns/
rules/proxy-groups 等），而 mihomo 的 proxy-provider 只需要 proxies 列表。
若直接把完整配置当 provider，mihomo 会因顶层出现 mixed-port 等字段而解析失败。
因此这里先把订阅下载下来，抽取出 proxies: 段写成 sub_cache.yaml（file 类型），
再用 w42 选择组引用它，全流量经选中节点出口。仅用标准库。
"""
import os
import urllib.request

SUB_CACHE = "sub_cache.yaml"


def fetch_subscription(sub):
    req = urllib.request.Request(
        sub,
        headers={
            "User-Agent": "clash-verge/1.10.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def extract_proxies(raw):
    """从完整配置中抽取 proxies: 段（含其缩进子项），直到下一个顶层键为止。"""
    lines = raw.splitlines()
    out = []
    capturing = False
    for ln in lines:
        if ln.startswith("proxies:"):
            capturing = True
            out.append(ln)
            continue
        if capturing:
            stripped = ln.strip()
            # 顶层键（列 0 起、以冒号结尾）且不是 proxies: 本身 → 段结束
            if ln and not ln[0].isspace() and stripped.endswith(":") \
                    and not stripped.startswith("proxies:"):
                break
            out.append(ln)
    return "\n".join(out).strip() + "\n"


def main():
    sub = (os.environ.get("W42_SUB") or "").strip()
    if not sub:
        raise SystemExit("W42_SUB 未设置，无法生成 mihomo 配置")

    print(f"[gen] 下载订阅: {sub[:64]}...")
    raw = fetch_subscription(sub)
    print(f"[gen] 订阅大小: {len(raw)} 字符")

    prox = extract_proxies(raw)
    if "proxies:" not in prox or len(prox) < 20:
        print("[gen] 未找到标准 proxies: 段，整份当作 provider 文件写入")
        prox = raw
    with open(SUB_CACHE, "w", encoding="utf-8") as f:
        f.write(prox)
    n = prox.count("name:")  # 粗略计数（含组名等，仅供日志）
    print(f"[gen] 已写出 {SUB_CACHE}（约 {n} 处 name）")

    cfg = f"""# 由 gen_mihomo_config.py 自动生成
mixed-port: 7890
mode: rule
allow-lan: false
log-level: info
external-controller: 127.0.0.1:9090
proxy-providers:
  sub:
    type: file
    path: ./{SUB_CACHE}
    health-check:
      enable: true
      url: https://api.42w.shop
      interval: 600
proxies: []
proxy-groups:
  - name: w42
    type: select
    proxies:
      - sub
rules:
  - MATCH,w42
"""
    with open("mihomo_config.yaml", "w", encoding="utf-8") as f:
        f.write(cfg)
    print("[gen] 已生成 mihomo_config.yaml")


if __name__ == "__main__":
    main()
