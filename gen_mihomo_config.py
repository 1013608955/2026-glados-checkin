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
import sys
import time
import urllib.error
import urllib.request

# 当初抓 Cookie 用的节点（出口 IP 与 cf_clearance 对齐）；可用 W42_SUB_NODE 覆盖
DEFAULT_NODE = "新加坡高速 05| CTCM"


def fetch_subscription(sub):
    """返回 (订阅正文, 响应头 dict, HTTP 状态码)。

    机场侧 403 / 429 很常见（限流或按来源 IP 拒绝），这里**不抛异常**，
    而是把状态码带回给 main 统一决定是否重试 —— 否则会抛出一串裸 traceback，
    连订阅账户信息都来不及打印。
    """
    req = urllib.request.Request(
        sub,
        headers={"User-Agent": "clash-verge/1.10.0", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace"), dict(r.headers), r.status
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return body, dict(e.headers or {}), e.code


def parse_userinfo(headers):
    """解析 subscription-userinfo，返回 (可读文本, 问题列表)。

    机场普遍按 Clash 约定回传这个头：upload/download/total/expire。
    有了它就不用靠猜：账号过期 / 流量用尽 / 只是这次没给节点，一眼可分。
    """
    ui = ""
    for k, v in (headers or {}).items():
        if k.lower() == "subscription-userinfo":
            ui = v or ""
            break
    if not ui:
        return "", []

    d = {}
    for kv in ui.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[k.strip().lower()] = v.strip()

    def gb(x):
        try:
            return f"{int(x) / 1024 ** 3:.2f}GB"
        except Exception:
            return str(x)

    problems = []
    exp = d.get("expire", "")
    exp_txt = "未知"
    if exp.isdigit():
        exp_i = int(exp)
        exp_txt = time.strftime("%Y-%m-%d", time.localtime(exp_i))
        if exp_i < time.time():
            problems.append("套餐已过期")
    total = int(d.get("total", "0") or 0)
    download = int(d.get("download", "0") or 0)
    if total and download >= total:
        problems.append("流量已用尽")

    txt = (f"流量 {gb(d.get('upload', '0'))}↑ / {gb(d.get('download', '0'))}↓ "
           f"共 {gb(d.get('total', '0'))}，到期 {exp_txt}")
    return txt, problems


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


def count_proxies(raw):
    """统计订阅里 `proxies:` 段的代理条数（用于找不到节点时给排障线索）。

    必须限定在 proxies 段内：proxy-groups 里的条目同样是 `- name: ...` 开头。
    且不能要求同行含 `server:` —— 多行 YAML 里 server 在后续缩进行，会漏数成 0。
    """
    n, in_proxies = 0, False
    for line in raw.splitlines():
        if not line.strip():
            continue
        if not line[:1].isspace():          # 顶格键 = 进入/离开某个段落
            in_proxies = line.strip().startswith("proxies:")
            continue
        if in_proxies and line.strip().startswith("-"):
            n += 1
    return n


def _write_config(node, entry, block):
    """把抽取到的单个节点写成 mihomo 配置（单节点隧道 + MATCH 全走 w42）。"""
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


def main():
    node = (os.environ.get("W42_SUB_NODE") or "").strip() or DEFAULT_NODE

    # ★ 兜底通道（默认优先）：本机同步上来的单节点配置，见 w42_sync_node.py。
    #
    # 为什么默认不拉订阅：机场明确限流——「短时间内超出限制，或检测到订阅被分享，
    # 会自动重置订阅凭证」，旧链接随即失效。CI 每天拉两次属于不必要的消耗，
    # 真正的更新通道是本机的 w42_sync_node.py（频率可控、IP 是你自己的）。
    # 因此只有在「同步的节点连不上」时，才由 run_checkin.sh 带
    # --force-subscription 回来拉一次订阅，这就是我们唯一主动拉订阅的时机。
    force_sub = "--force-subscription" in sys.argv
    preset = "" if force_sub else (os.environ.get("W42_NODE_YAML") or "").strip()
    if force_sub:
        print("[gen] --force-subscription：忽略本机同步的节点，改为重新拉取订阅")
    if preset:
        if "server:" not in preset:
            raise SystemExit("[gen] W42_NODE_YAML 缺少 server: 字段，配置无效")
        print(f"[gen] 使用本机同步的节点配置（跳过订阅下载，{len(preset)} 字符）")
        if preset.lstrip().startswith("-"):
            _write_config(node, preset, None)
        else:
            _write_config(node, None, preset.splitlines())
        return

    sub = (os.environ.get("W42_SUB") or "").strip()
    if not sub:
        raise SystemExit("W42_SUB 未设置，且没有 W42_NODE_YAML 兜底，无法生成 mihomo 配置")
    try:
        from urllib.parse import urlparse
        host = urlparse(sub).netloc or sub.split('/')[0]
    except Exception:
        host = sub.split('/')[0]
    print(f"[gen] 下载订阅: {host} （已脱敏，不打印完整链接以避免泄露订阅 token）")
    def _parse(text):
        e = extract_node(text, node)
        b = None
        if not e:
            b = extract_node_block(text, node)
        return e, b

    def _report(raw_text, headers, status):
        print(f"[gen] 订阅大小: {len(raw_text)} 字符  HTTP {status}")
        ui_txt, problems = parse_userinfo(headers)
        if ui_txt:
            print(f"[gen] 订阅账户: {ui_txt}")
        for p in problems:
            print(f"[gen] ⚠️ 订阅账户问题：{p}")
        return ui_txt

    raw, headers, status = fetch_subscription(sub)
    ui_txt = _report(raw, headers, status)
    entry, block = _parse(raw)

    # 两种情况都值得先重试一次再定性，避免把「机场没给节点 / 拒绝请求」
    # 误报成「节点名写错了」：
    #   ① HTTP 非 200（403/429 = 限流或按来源 IP 拒绝）
    #   ② 200 但 0 个节点（机场针对来源 IP 过滤了节点列表）
    need_retry, reason = False, ""
    if status != 200:
        need_retry = True
        reason = f"订阅返回 HTTP {status}（403/429 通常是限流或来源 IP 被拒）"
    elif not entry and not block and count_proxies(raw) == 0:
        need_retry = True
        reason = "本次订阅返回 0 个节点（可能按来源 IP 过滤）"

    if need_retry:
        print(f"[gen] {reason}，10 秒后重试一次")
        time.sleep(10)
        try:
            raw, headers, status = fetch_subscription(sub)
            print("[gen] 重试结果：")
            ui_txt = _report(raw, headers, status)
            if status == 200:
                entry, block = _parse(raw)
        except Exception as e:
            print(f"[gen] 重试失败：{type(e).__name__}: {e}")

    if status != 200:
        raise SystemExit(
            f"[gen] 无法获取订阅：HTTP {status}。\n"
            f"      订阅账户：{ui_txt or '响应头未提供 subscription-userinfo'}\n"
            f"      常见原因：① 机场限流（403/429，稍后可能自动恢复）；\n"
            f"      ② 按来源 IP 拒绝（GitHub Actions 是机房 IP）；③ 订阅链接失效。\n"
            f"      处理：确认订阅链接是否仍有效；若持续被拒，需要更换订阅或出口。"
        )

    if entry:
        print(f"[gen] 已抽取单节点(单行写法): {node}")
    elif block:
        print(f"[gen] 已抽取单节点(多行写法, {len(block)} 行定义): {node}")
    else:
        # 统计订阅里解析到的代理定义数量，给排障一个量级线索。
        # 刻意不打印节点名清单：订阅内容偏敏感，且节点多时会刷屏。
        n = count_proxies(raw)
        if n == 0:
            raise SystemExit(
                f"[gen] 订阅本次返回 0 个代理定义 —— 这不是节点名的问题。\n"
                f"      订阅账户：{ui_txt or '响应头未提供 subscription-userinfo'}\n"
                f"      常见原因：① 机场限流（通常稍后自动恢复）；\n"
                f"      ② 按来源 IP 过滤节点（GitHub Actions 是机房 IP，部分机场不给节点）；\n"
                f"      ③ 订阅链接失效。\n"
                f"      处理：先手动 Run 一次确认是否瞬时限流；若持续为空，需要更换订阅或出口。"
            )
        raise SystemExit(
            f"[gen] 在订阅中找不到节点 '{node}'（订阅共解析到 {n} 个代理定义，账户正常）。\n"
            f"      常见原因：① 该节点已下架或改名；② W42_SUB_NODE 与订阅里的节点名\n"
            f"      不完全一致（区分空格、竖线、大小写）。\n"
            f"                处理：把 W42_SUB_NODE 改成订阅里仍存在的节点名。"
        )
    _write_config(node, entry, block)


if __name__ == "__main__":
    main()
