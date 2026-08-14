#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据订阅链接 W42_SUB 生成 mihomo (Clash Meta) 配置。
mihomo 会自己拉取并解析订阅，全流量经 w42 代理组出口。
无需第三方依赖（仅标准库）。
"""
import os

def main():
    sub = (os.environ.get('W42_SUB') or '').strip()
    if not sub:
        raise SystemExit('W42_SUB 未设置，无法生成 mihomo 配置')

    # YAML 单引号包裹；若订阅含单引号则转义为 ''
    url_yaml = "'" + sub.replace("'", "''") + "'"

    cfg = f"""# 由 gen_mihomo_config.py 自动生成
mixed-port: 7890
mode: rule
allow-lan: false
log-level: info
external-controller: 127.0.0.1:9090
proxy-providers:
  sub:
    type: http
    url: {url_yaml}
    interval: 86400
    path: ./sub_cache.yaml
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
    with open('mihomo_config.yaml', 'w', encoding='utf-8') as f:
        f.write(cfg)
    print('[gen] 已生成 mihomo_config.yaml，订阅节点将由 mihomo 拉取')

if __name__ == '__main__':
    main()
