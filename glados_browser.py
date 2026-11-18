#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLaDOS 签到——无头浏览器通道（Turnstile 必需）。

为什么不能用纯 requests
----------------------
2026-11-19 实测：`POST /api/user/checkin` 在纯 HTTP 客户端下恒定返回
    {"code":5,"reason":"turnstile","retryable":true}
即使 cookie 有效、出口 IP 相同。而同一 cookie 放进**无头 Chrome** 里，
接口立刻正常返回 `{"code":1,...}`。
→ Turnstile 判定的是「是否有真实浏览器环境」（TLS 指纹 + JS 执行 + 浏览器指纹），
  不是 IP。所以本模块用 Chrome --headless=new + CDP 代替 requests。

已验证环境：
- 本机 Windows Chrome 133 + Clash 出口 → 通过
- GitHub Actions ubuntu-latest + Chrome 157 + 机房 IP → 通过（code=1 repeated）

依赖：仅标准库 + 仓库内 `_ws.py`（纯 stdlib 的 CDP WebSocket 客户端）。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ws import WS

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
CHECKIN_URL = 'https://glados.cloud/console/checkin'


def _find_chrome():
    for cand in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        if shutil.which(cand):
            return cand
    for p in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
              r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'):
        if os.path.isfile(p):
            return p
    return None


def parse_cookie(raw):
    """把 `k=v; k2=v2` 拆成 CDP 需要的 cookie 结构。"""
    out = []
    for part in (raw or '').split(';'):
        if '=' not in part:
            continue
        k, v = part.split('=', 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        out.append({'name': k, 'value': v, 'domain': 'glados.cloud', 'path': '/'})
    return out


def checkin(cookie, timeout=90):
    """返回 (message, ok)。任何异常都降级成 (错误描述, False)，不抛出。"""
    cookies = parse_cookie(cookie)
    if not cookies:
        return '未提供 Cookie', False

    chrome = _find_chrome()
    if not chrome:
        return '找不到 Chrome（无法通过 Turnstile）', False

    port = 9333
    profile = '/tmp/glados-chrome-%d' % int(time.time() * 1000)
    proc = subprocess.Popen(
        [chrome, '--headless=new', '--no-sandbox', '--disable-gpu',
         '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
         f'--remote-debugging-port={port}', f'--user-data-dir={profile}', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        # 等 DevTools 端点就绪（CI 冷启动约 3~7 秒）
        version = None
        for _ in range(60):
            try:
                version = json.loads(
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2).read())
                break
            except Exception:
                time.sleep(0.5)
        if not version:
            return '浏览器启动超时', False

        tabs = json.loads(urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/list', timeout=10).read())
        page = next(t for t in tabs if t.get('type') == 'page')
        ws = WS(page['webSocketDebuggerUrl'])
        seq = [0]

        def send(method, params=None):
            seq[0] += 1
            ws.send(json.dumps({'id': seq[0], 'method': method, 'params': params or {}}))
            while True:
                raw = ws.recv()
                if raw is None:
                    return None
                o = json.loads(raw)
                if o.get('id') == seq[0]:
                    return o

        def js(expr):
            r = send('Runtime.evaluate',
                     {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
            res = (r or {}).get('result', {})
            if 'exceptionDetails' in res:
                return None
            return res.get('result', {}).get('value')

        send('Network.enable')
        send('Page.enable')
        send('Network.setUserAgentOverride', {'userAgent': UA, 'platform': 'Win32'})
        send('Network.setCookies', {'cookies': cookies})
        send('Page.navigate', {'url': CHECKIN_URL})

        # 等页面出现（最多 ~30 秒）。被 CF 挑战时标题会是 Just a moment...
        for _ in range(30):
            time.sleep(1)
            title = js('document.title') or ''
            if title and 'just a moment' not in title.lower():
                break
        title = js('document.title') or ''
        if 'just a moment' in title.lower():
            return '被 Cloudflare 挑战页拦截（浏览器未能自动通过）', False

        body = js("""(async () => {
          try {
            const r = await fetch('/api/user/checkin', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({token: 'glados.cloud'}), credentials: 'include'
            });
            return await r.text();
          } catch (e) { return 'EXC: ' + e.message; }
        })()""")
        if not body or body.startswith('EXC:'):
            return f'浏览器内请求失败：{body}', False

        try:
            data = json.loads(body)
        except Exception:
            return f'响应非 JSON：{body[:120]}', False

        msg = data.get('message') or str(data)[:120]
        code = data.get('code')
        # code=1 + repeated=true 表示「今天已签」——也算成功
        ok = bool(data.get('repeated')) or 'logged' in msg.lower() \
            or 'already' in msg.lower() or 'points' in msg.lower()
        if code == 5:
            return f'Turnstile 未通过：{msg}', False
        return msg, ok
    except Exception as e:
        return f'{type(e).__name__}: {str(e)[:120]}', False
    finally:
        try:
            if ws:
                ws.sock.close()
        except Exception:
            pass
        proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    raw = os.environ.get('GLADOS_COOKIE', '')
    print(checkin(raw))
