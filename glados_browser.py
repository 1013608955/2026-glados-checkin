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


def _click_at(send, x, y):
    """在视口坐标发真实鼠标事件（带移动轨迹）。

    为什么不能只用 JS click：Turnstile 会检查事件 isTrusted，JS 派发的
    合成事件一律被丢弃（表现为按钮永远卡在 Verifying…、getResponse() 为 None）。
    而 CDP 的 Input.dispatchMouseEvent 是浏览器层面的真实输入，isTrusted=true。
    额外补几步 mouseMoved，让轨迹不像"瞬移点击"。
    """
    for i in range(1, 6):
        send('Input.dispatchMouseEvent', {
            'type': 'mouseMoved',
            'x': x - 120 + 120 * i / 5,
            'y': y - 60 + 60 * i / 5,
        })
        time.sleep(0.08)
    send('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': x, 'y': y,
                                      'button': 'left', 'clickCount': 1})
    time.sleep(0.12)
    send('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': x, 'y': y,
                                      'button': 'left', 'clickCount': 1})


def checkin(cookie, timeout=90):
    """返回 (message, ok)。任何异常都降级成 (错误描述, False)，不抛出。

    流程（必须走页面按钮，不能直接 fetch）：
      点 Checkin → Turnstile 弹出交互框 → 用真实鼠标点它 → 页面自动完成签到
    2026-11-23 实测：直接 fetch 接口恒返回 reason=turnstile；而真人点一下
    验证框即成功 —— 所以关键是**真实鼠标事件**，不是伪造请求。
    """
    cookies = parse_cookie(cookie)
    if not cookies:
        return '未提供 Cookie', False

    chrome = _find_chrome()
    if not chrome:
        return '找不到 Chrome（无法通过 Turnstile）', False

    port = 9333
    profile = '/tmp/glados-chrome-%d' % int(time.time() * 1000)
    # 关键：无头模式下 Turnstile **不渲染交互框**（实测 rect 恒为空、无 token），
    # 有显示环境时才会有可点击的验证框。CI 用 xvfb-run 提供 DISPLAY，
    # 这里据此自动切「有头」——无需额外环境变量。
    headless = not os.environ.get('DISPLAY')
    args = [chrome]
    if headless:
        args.append('--headless=new')
    args += ['--no-sandbox', '--disable-gpu',
             '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
             # 视口要够大：Turnstile 验证框常在 x≈900 处，800x600 会落在视口外
             '--window-size=1920,1080',
             f'--remote-debugging-port={port}', f'--user-data-dir={profile}', 'about:blank']
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

        def diag():
            """失败时留下现场：标题 / 按钮清单 / 正文片段。
            CI 日志是唯一的排查入口，没有这些就只能看到一句「按钮为空」。"""
            d = js("""(() => ({
              title: document.title || '',
              btns: Array.from(document.querySelectorAll('button'))
                      .map(e => (e.innerText || '').trim().slice(0, 18)).filter(Boolean).slice(0, 10),
              body: (document.body ? document.body.innerText : '').slice(0, 90).replace(/\\n/g, ' | ')
            }))()""") or {}
            return f"标题={d.get('title','')!r} 按钮={d.get('btns',[])} 正文={d.get('body','')!r}"

        send('Network.enable')
        send('Page.enable')
        send('Network.setUserAgentOverride', {'userAgent': UA, 'platform': 'Win32'})
        send('Network.setCookies', {'cookies': cookies})
        send('Page.navigate', {'url': CHECKIN_URL})

        # 等页面出现（被 CF 挑战时标题会是 Just a moment...）
        for _ in range(30):
            time.sleep(1)
            title = js('document.title') or ''
            if title and 'just a moment' not in title.lower():
                break
        title = js('document.title') or ''
        if 'just a moment' in title.lower():
            return '被 Cloudflare 挑战页拦截（浏览器未能自动通过）', False

        # GLaDOS 是 SPA：标题秒出但按钮要等 React 渲染完。
        # 必须等到签到按钮真正出现，否则会误判成「找不到按钮」。
        for _ in range(40):
            ready = js("""!!Array.from(document.querySelectorAll('button'))
                             .find(e => /Checkin/i.test(e.innerText || ''))""")
            if ready:
                break
            time.sleep(1)
        else:
            body_head = (js('document.body.innerText') or '')[:120].replace('\n', ' | ')
            return f'页面未渲染出签到按钮（可见文本：{body_head}）', False

        # 已签到：直接返回
        btn_text = js("""(() => {
          const b = Array.from(document.querySelectorAll('button'))
            .find(e => /Checkin/.test(e.innerText || ''));
          return b ? b.innerText.trim() : '';
        })()""") or ''
        if 'Got' in btn_text and 'Point' in btn_text:
            return btn_text, True

        # 1) 点 Checkin 按钮（真实鼠标）
        box = js("""(() => {
          const b = Array.from(document.querySelectorAll('button'))
            .find(e => (e.innerText || '').trim() === 'Checkin');
          if (!b) return null;
          b.scrollIntoView({block:'center'});
          const r = b.getBoundingClientRect();
          return {x: r.x + r.width/2, y: r.y + r.height/2, text: b.innerText};
        })()""")
        if not box:
            return f'找不到签到按钮（{diag()}）', False
        _click_at(send, box['x'], box['y'])

        # 2) 等 Turnstile 交互框出现，然后真实鼠标点它
        clicked_turnstile = False
        deadline = time.time() + timeout - 40
        while time.time() < deadline:
            time.sleep(2)
            state = js("""(() => {
              const b = Array.from(document.querySelectorAll('button'))
                .find(e => /Checkin|Checking|Verif/i.test(e.innerText || ''));
              const inp = document.querySelector('input[id^=cf-chl-widget]');
              let rect = null;
              if (inp && inp.parentElement) {
                const r = inp.parentElement.getBoundingClientRect();
                if (r.width > 100 && r.height > 30) rect = {x: r.x + r.width/2, y: r.y + r.height/2};
              }
              const tok = (window.turnstile && turnstile.getResponse) ? turnstile.getResponse() : null;
              return {btn: b ? b.innerText.trim() : '', rect: rect, token: !!tok};
            })()""") or {}
            if 'Got' in state.get('btn', ''):
                return state['btn'], True
            if state.get('token'):
                # token 已就绪，通常页面会自动继续；再点一次按钮兜底
                time.sleep(2)
                if not clicked_turnstile:
                    js("""(() => {
                      const b = Array.from(document.querySelectorAll('button'))
                        .find(e => /Checkin/i.test(e.innerText || ''));
                      if (b && !b.disabled) b.click();
                    })()""")
                    clicked_turnstile = True
            elif state.get('rect') and not clicked_turnstile:
                _click_at(send, state['rect']['x'], state['rect']['y'])
                clicked_turnstile = True

        # 3) 收尾判定
        final = js("""(() => {
          const b = Array.from(document.querySelectorAll('button'))
            .find(e => /Checkin/i.test(e.innerText || ''));
          const t = document.body.innerText || '';
          const streak = (t.match(/STREAK\\s*\\n?\\s*(\\d+)/) || [])[1] || '';
          return {btn: b ? b.innerText.trim() : '', streak: streak};
        })()""") or {}
        btn = final.get('btn', '')
        if 'Got' in btn:
            return btn, True
        token = js("(window.turnstile && turnstile.getResponse) ? turnstile.getResponse() : ''")
        if token:
            return f'Turnstile 已通过但签到未确认（按钮：{btn}）', False
        return f'Turnstile 未通过（按钮：{btn}，无 token；{diag()}）', False
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
