#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI + 42w.shop)
功能：
- GLaDOS / ikuuu / SMAI.AI / 42w.shop 全自动签到
- 多账号支持（& 分隔）
- 按账号级别：上午成功 → 下午跳过该账号
- Token 过期自动检测 + 警告推送
"""

import requests
import json
import os
import sys
import re
import traceback
from datetime import date  # 仅顶层需要 date（W42 Cookie 龄期）；datetime/timedelta 在各函数内局部导入

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 全局配置 =================
GLADOS_DOMAINS = ["https://glados.cloud", "https://glados.rocks", "https://glados.network"]
IKUUU_DOMAINS = ["https://ikuuu.win", "https://ikuuu.fyi"]  # .win 为主，.fyi 为备用
COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}
SMAI_API = "https://api.smai.ai"
W42_API = "https://api.42w.shop"
STATE_FILE = os.environ.get("CHECKIN_STATE_FILE", ".checkin_state.json")

# ================= 工具函数 =================
def get_beijing_time():
    """获取当前北京时间"""
    from datetime import datetime, timezone, timedelta
    # GitHub Actions runner 通常是 UTC 时区
    # 正确方式：从 UTC 转换为北京时间 (UTC+8)
    utc_now = datetime.now(timezone.utc)
    beijing_tz = timezone(timedelta(hours=8))
    return utc_now.astimezone(beijing_tz)

def log(msg):
    beijing_time = get_beijing_time()
    print(f"[{beijing_time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def log_traceback(context):
    """把当前正在处理的异常完整堆栈逐行写入日志（CI 排障用），
    不改变调用方「优雅降级」的控制流——只是不再静默吞掉根因。"""
    for ln in traceback.format_exc(limit=8).rstrip().splitlines():
        log(f"  🐞 [{context}] {ln}")

def extract_cookie(raw):
    if not raw: return None
    raw = raw.strip()
    if 'koa:sess=' in raw or 'koa:sess.sig=' in raw: return raw
    if raw.startswith('{'):
        try: return 'koa.sess=' + json.loads(raw).get('token')
        except Exception: pass
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50: return 'koa:sess=' + raw
    return raw

def get_glados_cookies():
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw: return []
    return [extract_cookie(c) for c in (raw.split('\n') if '\n' in raw else raw.split('&')) if c.strip()]

def _load_ikuuu_cookie_file(path="ikuuu_cookie.json"):
    """读取 ikuuu_login.py 生成的 cookie JSON，返回 [cookie_str] 或 []。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookie = data.get("cookie", "")
        if cookie:
            return [cookie]
    except Exception:
        pass
    return []

def get_ikuuu_accounts():
    """取 ikuuu Cookie 的优先级：**ikuuu_cookie.json > IKUUU_COOKIE > 账号密码**。

    文件优先的原因：CI 里这个文件由 ikuuu-cookie.yml 无头登录后经 Actions Cache
    传入，永远是最新的；而 IKUUU_COOKIE Secret 通常是手动填的旧快照，
    若优先用它会拿过期 Cookie 覆盖掉刚刷新的，导致签到失败。
    """
    cookies = _load_ikuuu_cookie_file()
    if not cookies:
        cookie_raw = os.environ.get('IKUUU_COOKIE', '')
        cookies = [c.strip() for c in (cookie_raw.split('\n') if '\n' in cookie_raw else cookie_raw.split('&')) if c.strip()] if cookie_raw else []
    if cookies:
        return [('cookie', c) for c in cookies]
    # 回退到账号密码
    accounts_raw = os.environ.get('IKUUU_ACCOUNTS', '')
    if accounts_raw:
        accounts = []
        for item in accounts_raw.split('&'):
            item = item.strip()
            if ':' in item:
                email, pwd = item.split(':', 1)
                accounts.append((email.strip(), pwd.strip()))
        return [('pwd', (email, pwd)) for email, pwd in accounts]
    email, pwd = os.environ.get('IKUUU_EMAIL', ''), os.environ.get('IKUUU_PASSWORD', '')
    return [('pwd', (email, pwd))] if email and pwd else []

def get_ikuuu_cookies():
    """获取 ikuuu Cookie 列表：优先 ikuuu_cookie.json，其次 IKUUU_COOKIE。"""
    from_file = _load_ikuuu_cookie_file()
    if from_file:
        return from_file
    cookie_raw = os.environ.get('IKUUU_COOKIE', '')
    if not cookie_raw:
        return []
    return [c.strip() for c in (cookie_raw.split('\n') if '\n' in cookie_raw else cookie_raw.split('&')) if c.strip()]

def get_smai_sessions():
    raw = os.environ.get('SMAI_SESSION', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

def get_smai_user_ids():
    raw = os.environ.get('SMAI_USER_ID', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

def get_smai_refresh():
    """读取 SMAI 长期刷新令牌 new_api_refresh（用于签到前续期）。"""
    raw = os.environ.get('SMAI_REFRESH', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

def persist_smai_refresh(new_list):
    """把轮换后的 new_api_refresh 写回文件，若配置了 PAT 则直接更新 GitHub Secret。
    注意：SMAI 的 refresh 会【轮换】令牌（旧 token 失效），故必须把新 token 持久化，
    否则下次运行会因 AUTH_SESSION_REVOKED 而失败。"""
    # 明文落盘默认开启（无 PAT 时便于手动恢复 Secret）；设 SMAI_PERSIST_FILE=0 可关闭。
    # 该文件已在 .gitignore 中，但仍是明文敏感文件——恢复完成后建议手动删除。
    if os.environ.get('SMAI_PERSIST_FILE', '1') != '0':
        try:
            with open('smai_refresh_latest.txt', 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_list))
            log("  SMAI 新 refresh token 已写入 smai_refresh_latest.txt（⚠️ 明文敏感文件，恢复后建议删除）")
        except Exception as e:
            log(f"  ⚠️ 写入 smai_refresh_latest.txt 失败: {e}")
            log_traceback('persist_smai_refresh(写文件)')
    pat = os.environ.get('SMAI_PERSIST_PAT') or os.environ.get('REPO_PAT')
    if pat:
        try:
            import subprocess
            env = dict(os.environ)
            env['GH_TOKEN'] = pat
            env['GITHUB_TOKEN'] = pat
            subprocess.run(['gh', 'secret', 'set', 'SMAI_REFRESH', '--body', '\n'.join(new_list)],
                           env=env, check=True, timeout=90,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            log("  ✅ SMAI_REFRESH 已自动持久化到 GitHub Secret（无需手动更新）")
        except Exception as e:
            log(f"  ⚠️ 自动持久化失败（请手动把 smai_refresh_latest.txt 内容更新到 SMAI_REFRESH Secret）：{e}")
            log_traceback('persist_smai_refresh(gh secret set)')
    else:
        log("  ℹ️ 未配置 PAT（SMAI_PERSIST_PAT/REPO_PAT）：请手动把 smai_refresh_latest.txt 内容更新到 SMAI_REFRESH Secret，或重新抓取 new_api_refresh")

def get_w42_cookies():
    raw = os.environ.get('W42_COOKIE', '')
    if not raw: return []
    return [c.strip() for c in (raw.split('\n') if '\n' in raw else raw.split('&')) if c.strip()]

def get_w42_uids():
    raw = os.environ.get('W42_UID', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

# ================= 状态管理 =================
def load_state():
    legacy_watch = {}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                # 跨天数据：W42 Cookie 龄期监控需跨天保留；每日签到记录按天重置
                w = state.get('w42_cookie_watch')
                if isinstance(w, dict):
                    legacy_watch = {'w42_cookie_watch': w}
                # 用北京时间判断日期
                beijing_date = get_beijing_time().date()
                if state.get('date') == str(beijing_date):
                    # 清洗旧格式：morning 下所有 value 必须是 dict
                    for k, v in state.get('morning', {}).items():
                        if not isinstance(v, dict):
                            state['morning'][k] = {}
                    return state
    except Exception:
        log_traceback('load_state(读旧状态失败，改用全新状态)')
    # 用北京时间初始化日期（跨天时仅回填跨天的 w42_cookie_watch）
    beijing_date = get_beijing_time().date()
    fresh = {'date': str(beijing_date), 'morning': {}}
    fresh.update(legacy_watch)
    return fresh

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        log("💾 状态已保存")
    except Exception as e:
        log(f"⚠️ 保存失败: {e}")
        log_traceback('save_state')

def record_success(state, platform, key):
    morning = state.setdefault('morning', {})
    if not isinstance(morning.get(platform), dict):
        morning[platform] = {}
    morning[platform][key] = 'success'

def is_skipped(state, platform, key, is_morning):
    if is_morning: return False
    p = state.get('morning', {}).get(platform, {})
    return isinstance(p, dict) and p.get(key) == 'success'

def all_done(state, config):
    """config = {'glados': [keys], 'ikuuu': [keys], 'smai': [keys]}"""
    morning = state.get('morning', {})
    for platform, keys in config.items():
        if not keys: continue
        for k in keys:
            if morning.get(platform, {}).get(k) != 'success': return False
    return True

# ================= Token 过期检测 =================
EXPIRED_KEYWORDS = {
    'glados': ['unauthorized', 'login', '请重新登录', 'invalid token', '401'],
    'ikuuu': ['密码错误', '用户不存在', '登录失败', 'unauthorized', '401', '403', '405'],
    'smai': ['未登录', '无权', 'unauthorized', '401', 'expired', '过期', '未提供'],
    'w42': ['未授权', 'unauthorized', '401', '登录失效', 'expired', '过期', '无效'],
}

def is_expired(platform, msg):
    if not msg or msg == '未配置': return False
    return any(k in msg.lower() for k in EXPIRED_KEYWORDS.get(platform, []))

def diagnose_ikuuu_error(msg):
    """ikuuu 错误智能诊断 - 给用户提供 actionable 的建议"""
    if '405' in msg:
        return "⚠️ HTTP 405 (方法不允许) — Cookie 可能失效或 API 已变更，建议刷新 Cookie"
    elif '401' in msg:
        return "⚠️ HTTP 401 (未授权) — Cookie 已过期，请重新获取"
    elif '403' in msg:
        return "⚠️ HTTP 403 (被拦截) — 可能是 Cloudflare 防护，尝试账号密码模式"
    elif '超时' in msg:
        return "⏰ 网络超时 — GitHub Actions runner 可能在中国大陆，建议本地运行"
    return None

# ================= 推送 =================
def wpush(apikey, title, content):
    if not apikey: return
    try:
        r = requests.post("https://api.wpush.cn/api/v1/send",
            json={"apikey": apikey, "title": title, "content": content, "channel": "wechat"},
            headers={"Content-Type": "application/json"}, timeout=10)
        log(f"💬 推送成功" if r.status_code == 200 else f"⚠️ 推送返回 {r.status_code}")
    except requests.exceptions.ConnectionError as e:
        log(f"⚠️ 推送网络不可达（GitHub Actions runner 可能在中国大陆）")
    except requests.exceptions.Timeout:
        log(f"⚠️ 推送超时")
    except Exception as e:
        log(f"⚠️ 推送异常：{type(e).__name__}: {str(e)[:80]}")
        log_traceback('wpush')

# ================= GLaDOS =================
class GLaDOS:
    def __init__(self, cookie):
        self.cookie = cookie
        self.email = "未知账号"; self.left_days = "?"; self.points = "?"
        self.points_change = "?"; self.exchange_info = ""; self.checkin_msg = "执行失败"
        self.success = False

    def req(self, method, path, data=None):
        for d in GLADOS_DOMAINS:
            try:
                h = COMMON_HEADERS.copy()
                h.update({'Cookie': self.cookie, 'Origin': d, 'Referer': f'{d}/console/checkin'})
                r = requests.request(method, f'{d}{path}', headers=h, json=data, timeout=10)
                if r.status_code == 200: return r.json()
            except Exception as e:
                log(f"  GLaDOS 请求失败 @ {d}: {type(e).__name__}: {str(e)[:80]}")
                continue
        return None

    def checkin(self):
        r = self.req('POST', '/api/user/checkin', {'token': 'glados.cloud'})
        if r:
            self.checkin_msg = r.get('message', '签到失败')
            # 优化点1：GLaDOS 已签到也判定为成功
            self.success = "Checkin" in self.checkin_msg or "already" in self.checkin_msg.lower() or "logged" in self.checkin_msg.lower() or "points" in self.checkin_msg.lower()
        else:
            self.checkin_msg = "网络错误"

    def load_info(self):
        r = self.req('GET', '/api/user/status')
        if r and 'data' in r:
            self.email = r['data'].get('email', '?')
            self.left_days = str(r['data'].get('leftDays', '?')).split('.')[0]
        r = self.req('GET', '/api/user/points')
        if r and 'points' in r:
            self.points = str(r.get('points', '0')).split('.')[0]
            h = r.get('history', [])
            if h:
                c = str(h[0].get('change', '0')).split('.')[0]
                self.points_change = '+' + c if not c.startswith('-') else c
            pts = int(self.points) if self.points.isdigit() else 0
            lines = []
            for _, p in r.get('plans', {}).items():
                n, d = p['points'], p['days']
                lines.append(f"{'✅' if pts >= n else '❌'} {n}分→{d}天 (差{n-pts}分)" if pts < n else f"✅ {n}分→{d}天 (可兑换)")
            self.exchange_info = "\n".join(lines)

    def text(self):
        return f"### 🖥️ GLaDOS - {self.email}\n• 积分：{self.points} ({self.points_change})\n• 剩余：{self.left_days}天\n• 结果：{self.checkin_msg}\n\n🎁 兑换：\n{self.exchange_info or '暂无'}"
# ================= ikuuu =================
def ikuuu_pwd_login(email, pwd):
    """账号密码模式（可能被验证码拦截）- 多域名尝试"""
    last_error = ""
    
    for domain in IKUUU_DOMAINS:
        s = requests.session()
        base = domain.rstrip('/')
        h = {
            'origin': base,
            'user-agent': COMMON_HEADERS['User-Agent'],
            'referer': f'{base}/auth/login',
        }
        try:
            # 登录
            r = s.post(f'{base}/auth/login', headers=h, data={'email': email, 'passwd': pwd}, timeout=15)
            if r.status_code != 200:
                last_error = f"{domain.split('://')[-1]} 返回 HTTP {r.status_code}"
                continue
            
            result = r.json()
            if result.get('msg') != '登录成功':
                log(f"  ikuuu 登录 [{email}] @ {domain}: {result.get('msg')}")
                last_error = result.get('msg', '未知错误')
                continue
            
            # 签到
            c = s.post(f'{base}/user/checkin', headers=h, timeout=15).json()
            log(f"  ikuuu 登录签到成功 @ {domain}")
            msg = c.get('msg', c.get('message', '未知结果'))
            ok = "成功" in msg or "获得" in msg or "已经签到" in msg or "似乎已经签到过了" in msg
            return msg, ok
        except Exception as e:
            last_error = f"{domain.split('://')[-1]} 异常：{str(e)[:30]}"
            log(f"  ⚠️ ikuuu 登录异常 @ {domain}: {last_error}")
            continue
    
    return f"所有域名均失败：{last_error}", False


def ikuuu_checkin_cookie(cookie_str):
    """Cookie 模式签到：直接 POST /user/checkin，多域名容错"""
    h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
        'X-Requested-With': 'XMLHttpRequest',
        'Cookie': cookie_str,
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
    }
    
    for i, domain in enumerate(IKUUU_DOMAINS):
        base = domain.rstrip('/')
        h['Origin'] = base
        h['Referer'] = f'{base}/user'
        
        try:
            r = requests.post(f'{base}/user/checkin', headers=h, data='', timeout=15, verify=True)
            
            if r.status_code == 200:
                try:
                    data = r.json()
                    msg = data.get('msg', data.get('message', '未知结果'))
                    log(f"  ✅ ikuuu Cookie 签到成功 @ {domain}")
                    ok = "成功" in msg or "获得" in msg or "已经签到" in msg or "似乎已经签到过了" in msg or "已签到" in msg
                    return msg, ok
                except Exception as json_err:
                    log(f"  ikuuu @ {domain} 返回非 JSON: {r.text[:100]}")
                    if i < len(IKUUU_DOMAINS) - 1:
                        continue
                    return f"非 JSON 响应", False
            elif r.status_code == 403:
                if i < len(IKUUU_DOMAINS) - 1:
                    log(f"  ⚠️ {domain} 被 Cloudflare 拦截，尝试下一个...")
                    continue
                return "被 Cloudflare 拦截 (403)", False
            else:
                if i < len(IKUUU_DOMAINS) - 1:
                    log(f"  ⚠️ {domain} HTTP {r.status_code}, 尝试下一个...")
                    continue
                return f"HTTP {r.status_code}", False
                
        except requests.exceptions.SSLError as e:
            if i < len(IKUUU_DOMAINS) - 1:
                log(f"  ⚠️ ikuuu {domain} SSL 握手失败，尝试备用域名...")
                continue
            log(f"  ⚠️ ikuuu Cookie 模式：所有域名 SSL 均失败")
            return "SSL 握手失败（建议使用账号密码模式或本地运行）", False
            
        except requests.exceptions.Timeout:
            if i < len(IKUUU_DOMAINS) - 1:
                continue
            return "请求超时", False
            
        except Exception as e:
            if i < len(IKUUU_DOMAINS) - 1:
                log(f"  ⚠️ {domain} 异常：{type(e).__name__}: {str(e)[:80]}, 跳过")
                continue
            log_traceback('ikuuu_checkin_cookie')
            return f"{type(e).__name__}: {str(e)[:80]}", False
    
    return "所有域名均不可用", False

# ================= SMAI =================
def smai_one(session, uid_hint='', refresh_token=''):
    """单个 SMAI 账号签到 - 返回 (msg, ok, detail)

    鉴权机制（实测自浏览器真实流量）：
    - session 是短效 Flask 签名 cookie，单独打 API 必 401（"invalid access token"）。
    - 签到前必须先 POST /api/user/auth/refresh（携带长期 new_api_refresh cookie）
      续期；续期成功后，原 session cookie 才会被服务端接受（与浏览器行为一致）。
    - refresh 会【轮换】new_api_refresh（旧 token 失效），新 token 通过 Set-Cookie
      返回；调用方需把它持久化回 Secret（见 persist_smai_refresh）。
    detail 中可能携带 'new_refresh' 供上层持久化。
    """
    detail = {'username': uid_hint or '未知'}
    try:
        if session.startswith('session='):
            session = session[8:].strip()

        base_headers = {
            'Accept': 'application/json',
            'User-Agent': COMMON_HEADERS['User-Agent'],
            'Origin': SMAI_API,
            'Referer': SMAI_API + '/',
        }

        # ---------- 1) refresh 续期 ----------
        access_token = ''
        if refresh_token:
            if refresh_token.startswith('new_api_refresh='):
                refresh_token = refresh_token[len('new_api_refresh='):].strip()
            try:
                r = requests.post(
                    f'{SMAI_API}/api/user/auth/refresh',
                    cookies={'new_api_refresh': refresh_token, 'session': session},
                    headers=base_headers, timeout=15,
                )
                log(f"  SMAI refresh HTTP {r.status_code}")
                sc = r.headers.get('Set-Cookie', '')
                m = re.search(r'new_api_refresh=([^;,\s]+)', sc)
                if m:
                    detail['new_refresh'] = m.group(1)
                    log(f"  SMAI 已取得新 refresh token（待持久化）")
                try:
                    access_token = r.json().get('data', {}).get('access_token', '')
                except Exception:
                    pass
                if r.status_code != 200:
                    log(f"  ⚠️ SMAI refresh 失败：{r.text[:160]}")
            except Exception as e:
                log(f"  ⚠️ SMAI refresh 异常：{type(e).__name__}: {e}")
        else:
            log("  ⚠️ 未配置 SMAI_REFRESH：无法续期，签到大概率 401（请在 Secrets 补充 new_api_refresh）")

        auth_headers = dict(base_headers)
        auth_headers['Content-Type'] = 'application/json'
        if access_token:
            auth_headers['Authorization'] = f'Bearer {access_token}'

        # ---------- 2) 确定 user_id ----------
        uid = uid_hint
        username = uid_hint or '未知'
        if not uid:
            try:
                r = requests.get(f'{SMAI_API}/api/user/self',
                                 cookies={'session': session},
                                 headers=auth_headers, timeout=15)
                info = r.json()
                if info.get('success') and info.get('data', {}).get('id'):
                    uid = str(info['data']['id'])
                    username = info['data'].get('username', uid)
                    log(f"  SMAI 用户: {username} (ID: {uid})")
                else:
                    msg = info.get('message', '未知错误')
                    log(f"  SMAI 获取用户信息失败: {msg}")
                    log(f"  💡 请在 GitHub Secrets 中添加 SMAI_USER_ID (你的用户ID)")
                    return msg, False, detail
            except Exception as e:
                log(f"  SMAI 获取用户异常: {e}")
                return str(e), False, detail
        detail['username'] = username

        # ---------- 3) 查询签到状态 ----------
        try:
            r = requests.get(f'{SMAI_API}/api/user/checkin?year={get_beijing_time().year}',
                             cookies={'session': session},
                             headers={**auth_headers, 'Smai-Api-User': uid}, timeout=15)
            stats = r.json()
            if stats.get('success') and stats.get('data', {}).get('checked_in_today'):
                return "今日已签到", True, detail
        except Exception as e:
            log(f"  SMAI 查状态异常: {e}")

        # ---------- 4) 执行签到 ----------
        r = requests.post(f'{SMAI_API}/api/user/checkin',
                          cookies={'session': session},
                          headers={**auth_headers, 'Smai-Api-User': uid},
                          json={}, timeout=15)
        try:
            result = r.json()
        except Exception:
            result = {}

        if result.get('success'):
            # 提取签到详情
            data = result.get('data', {})
            earned_kb = data.get('quota_awarded', 0)  # 本次获得的点数

            # 查询最新统计
            try:
                r2 = requests.get(f'{SMAI_API}/api/user/checkin?year={get_beijing_time().year}',
                                  cookies={'session': session},
                                  headers={**auth_headers, 'Smai-Api-User': uid}, timeout=15)
                stats2 = r2.json()
            except Exception:
                stats2 = {}
            st = stats2.get('data', {}).get('stats', {})
            total_days = st.get('total_checkins', '?')
            total_quota_kb = st.get('total_quota', 0)

            # 格式化额度 (美元，通常精度较高)
            def fmt_quota(val):
                if val is None: return '?'
                try:
                    val = float(val)
                    # 转换单位：API返回的是quota，1 quota = 0.000002 USD
                    val = val * 0.000002
                    if val == 0: return '$0'
                    return f"${val:.6f}".rstrip('0').rstrip('.')
                except Exception:
                    return str(val)

            earned_str = fmt_quota(earned_kb)
            total_str = fmt_quota(total_quota_kb)
            msg = f"签到成功 +{earned_str}，累计 {total_str}，共 {total_days} 天"
            detail['earned'] = earned_kb
            detail['total_days'] = total_days
            detail['total_quota'] = total_quota_kb
            return msg, True, detail

        msg = result.get('message', '签到失败')
        return msg, "已签到" in msg, detail
    except Exception as e:
        log_traceback('smai_one')
        return f"{type(e).__name__}: {str(e)[:120]}", False, detail


# ================= 42w.shop (New API) =================
def w42_one(cookie, uid_hint=''):
    """单个 42w.shop 账号签到 - 返回 (msg, ok, detail)

    鉴权：session Cookie + New-Api-User(数字 uid) 请求头。
    代理：读取 W42_PROXY 环境变量（仅本平台使用，避免影响其它平台）；
          未设置时强制直连（trust_env=False，不读 runner 自带的环境代理）。
    """
    try:
        from datetime import datetime as _dt
        proxy = os.environ.get('W42_PROXY', '').strip()
        proxies = {'http': proxy, 'https': proxy} if proxy else None
        # 强制直连，忽略 runner 可能自带的环境代理（HTTP_PROXY/HTTPS_PROXY），
        # 只认可选的 W42_PROXY，避免被未知代理劫持导致空响应。
        s = requests.Session()
        s.trust_env = False
        h = {
            'User-Agent': COMMON_HEADERS['User-Agent'],
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Cookie': cookie,
        }
        if uid_hint:
            h['New-Api-User'] = uid_hint

        def _get_json(url, method='GET', data=None):
            r = s.request(method, url, headers=h, proxies=proxies, timeout=15, data=data)
            body = r.text or ''
            ctype = r.headers.get('Content-Type', '')
            if not body.strip():
                # 空响应：通常是被网络层/代理拦掉，或 Cloudflare 重置
                raise ValueError(f"HTTP {r.status_code} 空响应 server={r.headers.get('server','?')}"
                                 f" cf-ray={r.headers.get('cf-ray','?')}")
            if 'application/json' not in ctype.lower():
                # 非 JSON：极可能是 Cloudflare 挑战页 / 拦截页
                snippet = ' '.join(body[:160].split())
                raise ValueError(f"HTTP {r.status_code} 非JSON(ct={ctype}) "
                                 f"cf-ray={r.headers.get('cf-ray','?')} :: {snippet}")
            return r.json()

        # 1) 校验登录态并解析 uid / 用户名
        info = _get_json(f'{W42_API}/api/user/self')
        if not info.get('success'):
            return info.get('message', '未授权/登录失效'), False, {}
        d = info.get('data', {})
        uid = uid_hint or str(d.get('id', ''))
        username = d.get('display_name') or d.get('username') or uid
        if not uid_hint:
            h['New-Api-User'] = uid

        # 2) 执行签到
        res = _get_json(f'{W42_API}/api/user/checkin', method='POST', data='{}')

        # 3) 拉取统计用于汇总
        st = _get_json(f'{W42_API}/api/user/checkin').get('data', {}).get('stats', {})
        today_q = (st.get('records') or [{}])[0].get('quota_awarded')
        total_days = st.get('total_checkins')
        total_q = st.get('total_quota')
        detail = {'username': username, 'uid': uid,
                  'total_days': total_days, 'total_quota': total_q, 'today_quota': today_q}

        if res.get('success'):
            earned = res.get('data', {}).get('quota_awarded', today_q)
            return f"签到成功 +{earned}，累计 {total_q}，共 {total_days} 天", True, detail
        msg = res.get('message', '签到失败')
        if '已签到' in msg:
            return f"今日已签到 (今日 +{today_q}，累计 {total_q}，共 {total_days} 天)", True, detail
        return msg, False, detail
    except Exception as e:
        log_traceback('w42_one')
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        # Cloudflare 在 GitHub 数据中心 IP 上直接 403 拦截（cf-ray 头可见），
        # Cookie 本身没问题，是出口 IP 被拦。给出可操作的提示而非笼统报错。
        if 'cf-ray' in msg or ('403' in msg and '非JSON' in msg):
            msg = ("⛔ 42w 被 Cloudflare 拦截 (HTTP 403)：GitHub 数据中心 IP 通常被拦，"
                   "Cookie 本身有效。解决：① 配置 W42_PROXY 指向 Cloudflare 信任的出口 IP"
                   "（本机 Clash 无法从 GitHub 访问）；② 或改用本机定时任务跑 42w。")
        return msg, False, {}


def _drive(platform, units, is_morning, state, expired, results):
    """通用平台驱动：跑完一个平台的所有账号，返回 (success, total)。

    每个 unit 为 dict：
      - key:        记录/跳过判定用的 key
      - display:    默认显示名（作用于通用跳过行与默认 warn）
      - run():      执行单个账号签到，返回 (msg, ok, lines)
                    lines 为要追加到 results 的结果文本列表
      - skip_lines: (可选) 可调用，返回跳过时的结果行列表；
                    缺省用通用 '• {display}: 上午已签，跳过'
      - warn:       (可选) 过期警告文本；缺省用 '{display} 可能过期'
    """
    total = len(units)
    if not total:
        return 0, 0
    success = 0
    for u in units:
        if is_skipped(state, platform, u['key'], is_morning):
            sl = u.get('skip_lines')
            if callable(sl):
                sl = sl()
            results.extend(sl or [f"• {u['display']}: 上午已签，跳过"])
            success += 1
            continue
        try:
            msg, ok, lines = u['run']()
        except Exception as e:
            # 单账号意外异常不应炸掉整个任务（其它平台还要继续跑、结果还要推送）
            log_traceback(f"_drive/{platform}")
            msg = f"{type(e).__name__}: {str(e)[:100]}"
            ok = False
            lines = [f"• {u.get('display', '未知')}: {msg}"]
        if ok:
            record_success(state, platform, u['key'])
            success += 1
        elif is_expired(platform, msg):
            expired.append(u.get('warn') or f"{u['display']} 可能过期")
        results.extend(lines)
    return success, total


def _glados_run(g):
    g.checkin()
    g.load_info()
    return g.checkin_msg, g.success, [g.text()]


def _glados_skip(g):
    g.checkin_msg = "上午已签，跳过"
    g.success = True
    g.load_info()
    return [g.text()]


def _smai_unit(sess, uid, refresh, fallback, i, new_refresh_list, key):
    log(f"  SMAI 签到... ({key})")
    msg, ok, detail = smai_one(sess, uid, refresh)
    uname = detail.get('username', fallback)
    nr = detail.get('new_refresh')
    if nr and nr != refresh:
        new_refresh_list[i] = nr
    return msg, ok, [f"• {uname}: {msg}"]


def _w42_unit(ck, uid, fallback, key):
    log(f"  42w 签到... ({key})")
    msg, ok, detail = w42_one(ck, uid)
    uname = detail.get('username', fallback)
    return msg, ok, [f"• {uname}: {msg}"]


def main():
    # 获取北京时间
    beijing_time = get_beijing_time()
    is_morning = beijing_time.hour < 15  # 北京时间 0-15 点为上午，15-24 点为下午

    log("=" * 50)
    log(f"🚀 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI + 42w.shop)")
    log(f"⏰ {beijing_time.strftime('%Y-%m-%d %H:%M:%S')} {'(上午)' if is_morning else '(下午)'}")
    log("=" * 50)

    state = load_state()
    expired = []  # token 过期警告
    results = []

    # 统计配置的账号 key
    glados_cookies = get_glados_cookies()
    ikuuu_accounts = get_ikuuu_accounts()
    smai_sessions = get_smai_sessions()
    smai_user_ids = get_smai_user_ids()
    w42_cookies = get_w42_cookies()
    w42_uids = get_w42_uids()
    config = {
        'glados': [f"account_{i+1}" for i in range(len(glados_cookies))],
        # ikuuu: cookie 用 "cookie_N" 做 key，密码用 email 做 key
        'ikuuu': [f"cookie_{i+1}" if mode == 'cookie' else email for i, (mode, val) in enumerate(ikuuu_accounts) for email in [val[0] if isinstance(val, tuple) else val]],
        'smai': [s[:20]+"..." for s in smai_sessions],
        'w42': [c[:20]+"..." for c in w42_cookies],
    }

    # 下午：全部成功则跳过
    if not is_morning and all_done(state, config):
        log("🎉 上午全部成功，下午跳过！")
        log("SKIP_AFTERNOON=true")
        return

    # ========== W42 Cookie 龄期预警（实测 session 有效期约 26 天）==========
    # 原理：对 W42_COOKIE 内容取指纹存入跨天持久的 state；同值连续使用 ≥18 天
    # 就在推送里预警，避免「26 天后静默失效才发现」。换新 Cookie 自动重置计时。
    if w42_cookies:
        import hashlib
        fp = hashlib.sha256('|'.join(w42_cookies).encode('utf-8')).hexdigest()[:16]
        today = get_beijing_time().date()
        watch = state.setdefault('w42_cookie_watch', {})
        if watch.get('hash') != fp:
            watch['hash'] = fp
            watch['first_seen'] = str(today)
        else:
            try:
                age = (today - date.fromisoformat(watch.get('first_seen', str(today)))).days
            except Exception:
                age = 0
                watch['first_seen'] = str(today)
            if age >= 18:
                warn = f"🔷 42w Cookie 已连续使用 {age} 天（实测有效期约 26 天），请尽快刷新 W42_COOKIE"
                expired.append(warn)
                log(f"⚠️ {warn}")

    # ========== GLaDOS ==========
    if glados_cookies:
        units = []
        for i, ck in enumerate(glados_cookies):
            g = GLaDOS(ck)
            units.append({
                'key': f"account_{i+1}",
                'display': f"账号{i+1}",
                'warn': f"🖥️ GLaDOS [账号{i+1}] Cookie 可能过期",
                'skip_lines': lambda g=g: _glados_skip(g),
                'run': lambda g=g: _glados_run(g),
            })
        g_success, g_total = _drive('glados', units, is_morning, state, expired, results)
    else:
        results.append("### 🖥️ GLaDOS\n未配置，跳过")
        g_success, g_total = 0, 0

    # ========== ikuuu ==========
    results.append("\n### 📶 ikuuu 签到结果")
    i_success = 0; i_total = len(ikuuu_accounts)
    if ikuuu_accounts:
        msgs = []
        for i, acct in enumerate(ikuuu_accounts):
            mode, val = acct
            if mode == 'cookie':
                display = f"cookie_{i+1}"
                key = display
            else:
                email_val = val[0]
                display = email_val
                key = email_val
            if is_skipped(state, 'ikuuu', key, is_morning):
                msgs.append(f"{display}: 上午已签，跳过")
                i_success += 1
            elif mode == 'cookie':
                msg, ok = ikuuu_checkin_cookie(val)
                msgs.append(f"• {display}: {msg}")
                if ok:
                    record_success(state, 'ikuuu', key)
                    i_success += 1
                elif is_expired('ikuuu', msg):
                    # 智能诊断
                    diagnosis = diagnose_ikuuu_error(msg)
                    if diagnosis:
                        expired.append(f"📶 ikuuu [{display}] {diagnosis}")
                    else:
                        expired.append(f"📶 ikuuu [{display}] Cookie 可能过期")
            else:
                email_val, pwd_val = val
                msg, ok = ikuuu_pwd_login(email_val, pwd_val)
                msgs.append(f"{display}: {msg}")
                if ok:
                    record_success(state, 'ikuuu', key)
                    i_success += 1
                elif is_expired('ikuuu', msg):
                    expired.append(f"📶 ikuuu [{display}] 账号可能失效")
        results.append(f"• 结果：{' | '.join(msgs)}")
    else:
        results.append("• 未配置，跳过")

    # ========== SMAI ==========
    results.append("\n### ✅ SMAI.AI 签到结果")
    smai_user_ids = get_smai_user_ids()
    smai_refreshes = get_smai_refresh()
    s_total = len(smai_sessions)
    if smai_sessions:
        new_refresh_list = [smai_refreshes[i] if i < len(smai_refreshes) else '' for i in range(s_total)]
        units = []
        for i, sess in enumerate(smai_sessions):
            key = sess[:20] + "..."
            uid = smai_user_ids[i] if i < len(smai_user_ids) else ''
            refresh = smai_refreshes[i] if i < len(smai_refreshes) else ''
            fallback = uid or f"账号{i+1}"
            units.append({
                'key': key,
                'display': fallback,
                'warn': f"✅ SMAI [{key}] Session 可能过期",
                'skip_lines': [f"• {fallback}: 上午已签，跳过"],
                'run': lambda sess=sess, uid=uid, refresh=refresh, fallback=fallback, i=i, nr=new_refresh_list, key=key: _smai_unit(sess, uid, refresh, fallback, i, nr, key),
            })
        s_success, _ = _drive('smai', units, is_morning, state, expired, results)
        # 若 refresh token 发生轮换，持久化新值（避免下次 AUTH_SESSION_REVOKED）
        if any(new_refresh_list[i] != (smai_refreshes[i] if i < len(smai_refreshes) else '') for i in range(len(new_refresh_list))):
            persist_smai_refresh(new_refresh_list)
    else:
        results.append("• 未配置，跳过")
        s_success = 0

    # ========== 42w.shop ==========
    results.append("\n### 🔷 42w.shop 签到结果")
    w42_uids = get_w42_uids()
    w_total = len(w42_cookies)
    if w42_cookies:
        units = []
        for i, ck in enumerate(w42_cookies):
            key = ck[:20] + "..."
            uid = w42_uids[i] if i < len(w42_uids) else ''
            fallback = uid or f"账号{i+1}"
            units.append({
                'key': key,
                'display': fallback,
                'warn': f"🔷 42w [{key}] Cookie 可能过期",
                'skip_lines': [f"• {fallback}: 上午已签，跳过"],
                'run': lambda ck=ck, uid=uid, fallback=fallback, key=key: _w42_unit(ck, uid, fallback, key),
            })
        w_success, _ = _drive('w42', units, is_morning, state, expired, results)
    else:
        results.append("• 未配置，跳过")
        w_success = 0

    save_state(state)

    # ========== 推送 ==========
    # 汇总统计
    total_done = g_success + i_success + s_success + w_success
    total_all = (g_total if g_total else 0) + (i_total if i_total else 0) + (s_total if s_total else 0) + (w_total if w_total else 0)
    summary = f"📊 汇总：{total_done}/{total_all} 成功"
    if g_total: summary += f" | GLaDOS {g_success}/{g_total}"
    if i_total: summary += f" | ikuuu {i_success}/{i_total}"
    if s_total: summary += f" | SMAI {s_success}/{s_total}"
    if w_total: summary += f" | 42w {w_success}/{w_total}"

    body = ""
    if expired:
        body += "⚠️ **Token 过期警告**\n" + "\n".join(f"  🔴 {w}" for w in expired) + "\n  👉 请更新对应 Secret\n\n"
    body += "\n".join(results) + f"\n\n---\n{summary}\n⏰ {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}"  # 修改：用北京时间

    prefix = "⚠️ " if expired else ""
    title = f"{prefix}多平台签到 {total_done}/{total_all}"

    wpush(os.environ.get("WPUSH_APIKEY"), title, body)

    log("\n" + "=" * 50)
    log("📋 结果：\n" + body)
    if expired: log(f"\n🔴 {len(expired)} 个 token 可能过期！")
    log("=" * 50)

if __name__ == '__main__':
    main()
