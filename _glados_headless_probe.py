#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时探针：在 CI 里用无头 Chrome 调 GLaDOS 签到接口，判断 Turnstile 是否放行。

判定：
  code=5 / reason=turnstile  → 被拦（CI 环境不行）
  code=1 / repeated=true     → 通过（今天已签）—— 说明浏览器环境足够
  其它                        → 打印原文
测完即删。
"""
import json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, '.')
from _ws import WS

PORT = 9333
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

raw = (os.environ.get('GLADOS_COOKIE') or '').strip()
if not raw:
    raise SystemExit('GLADOS_COOKIE 未设置')
cookies = []
for part in raw.split(';'):
    if '=' not in part:
        continue
    k, v = part.split('=', 1)
    cookies.append({'name': k.strip(), 'value': v.strip(), 'domain': 'glados.cloud', 'path': '/'})
print('cookie 条目:', [c['name'] for c in cookies])

chrome = None
for cand in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']:
    try:
        subprocess.run([cand, '--version'], capture_output=True, timeout=10, check=True)
        chrome = cand
        break
    except Exception:
        continue
if not chrome:
    raise SystemExit('找不到 Chrome')


proc = subprocess.Popen(
    [chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
     f'--remote-debugging-port={PORT}', '--user-data-dir=/tmp/chrome-probe', 'about:blank'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

ver = None
for _ in range(60):
    try:
        ver = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/version', timeout=2).read())
        break
    except Exception:
        time.sleep(0.5)
if not ver:
    proc.kill()
    raise SystemExit('Chrome 启动失败')
print('浏览器:', ver.get('Browser'))

tabs = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=10).read())
page = next(t for t in tabs if t.get('type') == 'page')
ws = WS(page['webSocketDebuggerUrl'])
_id = [0]


def send(method, params=None):
    _id[0] += 1
    ws.send(json.dumps({'id': _id[0], 'method': method, 'params': params or {}}))
    while True:
        r = ws.recv()
        if r is None:
            return None
        o = json.loads(r)
        if o.get('id') == _id[0]:
            return o


def js(expr):
    r = send('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True})
    res = (r or {}).get('result', {})
    if 'exceptionDetails' in res:
        return 'ERR: ' + str(res['exceptionDetails'])[:150]
    return res.get('result', {}).get('value')


send('Network.enable')
send('Page.enable')
send('Network.setUserAgentOverride', {'userAgent': UA, 'platform': 'Win32'})
send('Network.setCookies', {'cookies': cookies})
send('Page.navigate', {'url': 'https://glados.cloud/console/checkin'})
time.sleep(12)

print('页面标题:', js('document.title'))
print('CF 挑战页:', js("!!document.title.match(/Just a moment|Attention Required/i)"))
print('webdriver:', js('navigator.webdriver'))

res = js("""(async () => {
  try {
    const r = await fetch('/api/user/checkin', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: 'glados.cloud'}), credentials: 'include'
    });
    return r.status + ' :: ' + (await r.text()).slice(0, 320);
  } catch (e) { return 'EXC: ' + e.message; }
})()""")
print()
print('=== RESULT ===')
print(res)

ws.sock.close()
proc.kill()
