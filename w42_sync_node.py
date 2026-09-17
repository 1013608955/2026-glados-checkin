#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把订阅里的【单个节点】从本机同步到 GitHub Secrets，作为 CI 的兜底通道。

为什么需要它
------------
CI 直接从机场拉订阅时，机场常返回 403（限流 / 按来源 IP 拒绝），
或 200 但 `proxies: []`（针对机房 IP 过滤节点列表）—— 两种情况下
gen_mihomo_config.py 都拿不到节点，42w 随即因直连被 Cloudflare 拦成 403。

但**本机可以正常拉取同一份订阅**。于是：本机抽好那一个节点 → 写进
Secret `W42_NODE_YAML` → CI 直接用，不再下载订阅。
节点本身的连接不受影响（机场拒绝的是拉订阅，不是连节点），
而且只要还是同一个节点，出口 IP 不变，cf_clearance 依然对得上。

用法
----
    python w42_sync_node.py                 # 抽取并写入 Secret
    python w42_sync_node.py --dry-run       # 只看抽取结果，不写 Secret
    python w42_sync_node.py --sub <URL>     # 手动指定订阅链接
    python w42_sync_node.py --node "新加坡高速 05| CTCM"

订阅链接优先取环境变量 W42_SUB；没设则从 Clash Verge 的 profiles.yaml
里自动识别（本机路径可用 W42_CV_DIR 覆盖）。
"""
import argparse
import os
import re
import subprocess
import sys

import gen_mihomo_config as g

SECRET_NAME = "W42_NODE_YAML"
# 本机缓存多久算「新鲜」：Clash Verge 默认每天更新一次订阅，
# 3 天内的缓存足够可信，直接用就不必再拉订阅了。
CACHE_MAX_AGE_DAYS = 3
_APPDATA = os.environ.get("APPDATA", r"C:\Users\Admin\AppData\Roaming")
# 本机可能同时装着多个 Clash 客户端，按顺序都找一遍：
#   Clash Verge / FlClash（包名 com.follow.clash）
LOCAL_CLIENT_DIRS = [
    os.path.join(_APPDATA, "io.github.clash-verge-rev.clash-verge-rev"),
    os.path.join(_APPDATA, "com.follow", "clash"),
]
DEFAULT_CV_DIR = LOCAL_CLIENT_DIRS[0]
# 本机拉订阅通常要走 Clash（直连多半不通）；可用 W42_SYNC_PROXY 覆盖
DEFAULT_PROXY = "http://127.0.0.1:7897"


def find_sub_from_clash(cv_dir):
    """从 Clash Verge 的 profiles.yaml 里找订阅链接。"""
    path = os.path.join(cv_dir, "profiles.yaml")
    if not os.path.isfile(path):
        return None
    try:
        t = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None
    urls = re.findall(r'https?://[^\s"\',]+', t)
    # 订阅链接一般带 /feed/ 或含 token 的长路径；挑最长的那个通常就是
    cands = [u for u in urls if "/feed/" in u or "sub" in u.lower()]
    if not cands:
        cands = urls
    return max(cands, key=len) if cands else None


def extract_from_local_profiles(cv_dir, node):
    """从 Clash Verge 本地缓存的 profile 里找节点。

    在线拉订阅被限流 / 拒绝时的兜底：Clash Verge 会定期把订阅落盘到
    profiles/*.yaml，节点就在里面。按修改时间从新到旧找。
    返回 (节点定义, 来源文件, 修改时间)；找不到返回 (None, None, None)。
    """
    pdir = os.path.join(cv_dir, "profiles")
    if not os.path.isdir(pdir):
        return None, None, None
    files = [os.path.join(pdir, f) for f in os.listdir(pdir)
             if f.lower().endswith((".yaml", ".yml"))]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in files:
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        e, b = g.extract_any(t, node)
        if e or b:
            return (e or "\n".join(b)), p, os.path.getmtime(p)
    return None, None, None


def fetch(url):
    """本机拉取订阅：先直连，不通再走 Clash 代理。"""
    import requests
    ua = {"User-Agent": "clash-verge/1.10.0", "Accept": "*/*"}
    proxy = (os.environ.get("W42_SYNC_PROXY") or "").strip() or DEFAULT_PROXY

    last_err = None
    for label, proxies in (("直连", None), ("Clash 代理", {"http": proxy, "https": proxy})):
        try:
            s = requests.Session()
            s.trust_env = False
            r = s.get(url, headers=ua, proxies=proxies, timeout=30)
            if r.status_code == 200 and r.text.strip():
                print(f"  订阅获取：{label} 成功（{len(r.text)} 字符）")
                return r.text, dict(r.headers), r.status_code
            last_err = f"{label} → HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{label} → {type(e).__name__}"
    raise SystemExit(f"订阅获取失败：{last_err}\n"
                     f"  可尝试设置 W42_SYNC_PROXY 指定可用的代理。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", default="", help="订阅链接（默认取 W42_SUB 或 Clash Verge 配置）")
    ap.add_argument("--node", default="", help="节点名（默认取 W42_SUB_NODE 或内置默认值）")
    ap.add_argument("--dry-run", action="store_true", help="只显示抽取结果，不写 Secret")
    ap.add_argument("--online", action="store_true",
                    help="强制在线拉订阅（默认优先用本机客户端缓存，避免拉取过频）")
    ap.add_argument("--file", default="",
                    help="直接从本地某个配置文件里抽取节点（不联网、不查缓存）")
    args = ap.parse_args()

    node = (args.node or os.environ.get("W42_SUB_NODE") or "").strip() or g.DEFAULT_NODE
    sub = (args.sub or os.environ.get("W42_SUB") or "").strip()
    if not sub:
        cv_dir = os.environ.get("W42_CV_DIR") or DEFAULT_CV_DIR
        sub = find_sub_from_clash(cv_dir)
        if sub:
            print(f"  订阅链接：从 Clash Verge 配置识别（{os.path.basename(cv_dir)}）")
    if not sub:
        raise SystemExit("找不到订阅链接：请设置 W42_SUB，或用 --sub 指定，"
                         "或用 W42_CV_DIR 指向 Clash Verge 配置目录")

    host = re.sub(r"^https?://", "", sub).split("/")[0]
    print(f"  目标主机：{host}（订阅 token 不打印）")
    print(f"  目标节点：{node}")

    import time as _t

    def find_local():
        """在本机所有 Clash 客户端的 profiles 里找节点，取最新的那份。"""
        override = os.environ.get("W42_CV_DIR")
        dirs = [override] if override else LOCAL_CLIENT_DIRS
        best = (None, None, None)
        for d in dirs:
            txt, s, m = extract_from_local_profiles(d, node)
            if txt and (best[2] is None or m > best[2]):
                best = (txt, s, m)
        return best

    cached, src, mt = (None, None, None)
    if not args.file:
        cached, src, mt = find_local()
    cache_fresh = bool(cached) and (_t.time() - mt) < CACHE_MAX_AGE_DAYS * 86400

    def from_online():
        raw, headers, status = fetch(sub)
        ui_txt, problems = g.parse_userinfo(headers)
        if ui_txt:
            print(f"  订阅账户：{ui_txt}")
        for p in problems:
            print(f"  ⚠️ {p}")
        print(f"  订阅节点数：{g.count_proxies(raw)}")
        e, b = g.extract_any(raw, node)
        return (e or "\n".join(b)) if (e or b) else None

    def from_cache(allow_stale=False):
        if not cached or (not allow_stale and not cache_fresh):
            return None
        age = (_t.time() - mt) / 86400
        try:
            rel = os.path.join(*src.split(os.sep)[-2:])
        except Exception:
            rel = os.path.basename(src)
        print(f"  使用本机客户端缓存：{rel} "
              f"（更新于 {_t.strftime('%Y-%m-%d %H:%M', _t.localtime(mt))}，{age:.1f} 天前）")
        return cached

    text = None
    if args.file:
        # 直接从指定文件抽（不联网、不查缓存）——用于 FlClash / 手工导出的配置
        raw = open(args.file, encoding="utf-8", errors="ignore").read()
        e, b = g.extract_any(raw, node)
        if e or b:
            text = e or "\n".join(b)
            print(f"  使用指定文件：{args.file}")
        else:
            print(f"  ⚠️ 指定文件里没有节点 '{node}'：{args.file}")
    else:
        # 默认优先用本地缓存：客户端自己会定期更新它，
        # 没必要为了同步一个节点再去拉一次订阅（机场会因拉取过频重置凭证）。
        if not args.online:
            text = from_cache()
        if not text:
            try:
                text = from_online()
            except SystemExit as e:
                print(f"  ⚠️ 在线获取不可用：{str(e).splitlines()[0]}")
        if not text:
            text = from_cache(allow_stale=True)
    if not text:
        dirs = "\n    ".join(LOCAL_CLIENT_DIRS)
        raise SystemExit(f"找不到节点 '{node}'（在线与本机缓存都没有）。\n"
                         f"  已翻过的客户端目录：\n    {dirs}\n"
                         f"  可用 --file 指定配置文件，或 --node 换个节点名。")
    if "server:" not in text:
        raise SystemExit("抽取结果缺少 server: 字段，终止")

    masked = re.sub(r"(password|uuid|auth|token|peer|sni)\s*[:=]\s*\S+", r"\1: ***", text)
    print()
    print("  抽取到的节点（敏感字段已打码）：")
    print("   ", masked[:160])

    if args.dry_run:
        print("\n--dry-run：未写入 Secret")
        return

    print(f"\n  写入 Secret {SECRET_NAME}（{len(text)} 字符）...")
    r = subprocess.run(["gh", "secret", "set", SECRET_NAME],
                       input=text, text=True, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"写入失败：{r.stderr.strip()[:300]}")
    print("  ✅ 已同步。下一次 CI 运行会直接使用该节点，不再下载订阅。")


if __name__ == "__main__":
    main()
