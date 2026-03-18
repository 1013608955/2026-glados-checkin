#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI)
功能：
- GLaDOS / ikuuu / SMAI.AI 全自动签到
- 多账号支持（& 分隔）
- 按账号级别：上午成功 → 下午跳过该账号
- Token 过期自动检测 + 警告推送
"""

import requests
import json
import os
import sys
from datetime import datetime, date, timedelta  # 新增：导入 timedelta

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 全局配置 =================
GLADOS_DOMAINS = ["https://glados.cloud", "https://glados.rocks", "https://glados.network"]
COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}
SMAI_API = "https://api.smai.ai"
STATE_FILE = os.environ.get("CHECKIN_STATE_FILE", ".checkin_state.json")

# ================= 工具函数 =================
def log(msg):
    # 修改：使用北京时间打印日志
    beijing_time = datetime.now() + timedelta(hours=8)
    print(f"[{beijing_time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def extract_cookie(raw):
    if not raw: return None
    raw = raw.strip()
    if 'koa:sess=' in raw or 'koa:sess.sig=' in raw: return raw
    if raw.startswith('{'):
        try: return 'koa.sess=' + json.loads(raw).get('token')
        except: pass
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50: return 'koa:sess=' + raw
    return raw

def get_glados_cookies():
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw: return []
    return [extract_cookie(c) for c in (raw.split('\n') if '\n' in raw else raw.split('&')) if c.strip()]

def get_ikuuu_accounts():
    accounts_raw = os.environ.get('IKUUU_ACCOUNTS', '')
    if accounts_raw:
        accounts = []
        for item in accounts_raw.split('&'):
            item = item.strip()
            if ':' in item:
                email, pwd = item.split(':', 1)
                accounts.append((email.strip(), pwd.strip()))
        return accounts
    email, pwd = os.environ.get('IKUUU_EMAIL', ''), os.environ.get('IKUUU_PASSWORD', '')
    return [(email, pwd)] if email and pwd else []

def get_smai_sessions():
    raw = os.environ.get('SMAI_SESSION', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

def get_smai_user_ids():
    raw = os.environ.get('SMAI_USER_ID', '')
    if not raw: return []
    return [s.strip() for s in (raw.split('\n') if '\n' in raw else raw.split('&')) if s.strip()]

# ================= 状态管理 =================
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                # 修改：用北京时间判断日期
                beijing_date = (datetime.now() + timedelta(hours=8)).date()
                if state.get('date') == str(beijing_date):
                    # 清洗旧格式：morning 下所有 value 必须是 dict
                    for k, v in state.get('morning', {}).items():
                        if not isinstance(v, dict):
                            state['morning'][k] = {}
                    return state
    except: pass
    # 修改：用北京时间初始化日期
    beijing_date = (datetime.now() + timedelta(hours=8)).date()
    return {'date': str(beijing_date), 'morning': {}}

def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        log("💾 状态已保存")
    except Exception as e:
        log(f"⚠️ 保存失败: {e}")

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
    'ikuuu': ['密码错误', '用户不存在', '登录失败', 'unauthorized', '401'],
    'smai': ['未登录', '无权', 'unauthorized', '401', 'expired', '过期', '未提供'],
}

def is_expired(platform, msg):
    if not msg or msg == '未配置': return False
    return any(k in msg.lower() for k in EXPIRED_KEYWORDS.get(platform, []))

# ================= 推送 =================
def wpush(apikey, title, content):
    if not apikey: return
    try:
        r = requests.post("https://api.wpush.cn/api/v1/send",
            json={"apikey": apikey, "title": title, "content": content, "channel": "wechat"},
            headers={"Content-Type": "application/json"}, timeout=10)
        log("✅ 推送成功" if r.status_code == 200 else f"❌ 推送失败: {r.text}")
    except Exception as e:
        log(f"❌ 推送异常: {e}")

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
            except: continue
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
def ikuuu_one(email, pwd):
    s = requests.session()
    h = {'origin': 'https://ikuuu.nl', 'user-agent': COMMON_HEADERS['User-Agent']}
    try:
        r = s.post('https://ikuuu.nl/auth/login', headers=h, data={'email': email, 'passwd': pwd}, timeout=10).json()
        log(f"  ikuuu 登录 [{email}]: {r['msg']}")
        # 登录失败直接返回
        if r['msg'] != '登录成功':
            return r['msg'], False
        c = s.post('https://ikuuu.nl/user/checkin', headers=h, timeout=10).json()
        log(f"  ikuuu 签到 [{email}]: {c['msg']}")
        # 优化点2：ikuuu 已签到也判定为成功
        ok = "成功" in c['msg'] or "获得" in c['msg'] or "已经签到" in c['msg'] or "似乎已经签到过了" in c['msg']
        return c['msg'], ok
    except Exception as e:
        return str(e), False

# ================= SMAI =================
def smai_one(session, uid_hint=''):
    """单个 SMAI 账号签到 - 返回详细信息"""
    try:
        import subprocess

        # 确定 user_id
        uid = uid_hint
        username = uid_hint or '未知'
        if not uid:
            r = subprocess.run([
                'curl', '-s', '--max-time', '15',
                f'{SMAI_API}/api/user/self',
                '-H', 'Accept: application/json',
                '-H', f'Cookie: session={session}',
                '-H', 'User-Agent: Mozilla/5.0'
            ], capture_output=True, timeout=20)
            info = json.loads(r.stdout.decode('utf-8', errors='replace'))
            if info.get('success') and info.get('data', {}).get('id'):
                uid = str(info['data']['id'])
                username = info['data'].get('username', uid)
                log(f"  SMAI 用户: {username} (ID: {uid})")
            else:
                msg = info.get('message', '未知错误')
                log(f"  SMAI 获取用户信息失败: {msg}")
                log(f"  💡 请在 GitHub Secrets 中添加 SMAI_USER_ID (你的用户ID)")
                return msg, False, {}

        # 查询签到状态
        r = subprocess.run([
            'curl', '-s', '--max-time', '15',
            f'{SMAI_API}/api/user/checkin?year={(datetime.now() + timedelta(hours=8)).year}',
            '-H', 'Accept: application/json',
            '-H', f'New-Api-User: {uid}',
            '-H', f'Cookie: session={session}',
            '-H', 'User-Agent: Mozilla/5.0'
        ], capture_output=True, timeout=20)
        stats = json.loads(r.stdout.decode('utf-8', errors='replace'))
        if stats.get('success') and stats.get('data', {}).get('checked_in_today'):
            return "今日已签到", True, {'username': username}

        # 执行签到
        r = subprocess.run([
            'curl', '-s', '--max-time', '15', '-X', 'POST',
            f'{SMAI_API}/api/user/checkin',
            '-H', 'Accept: application/json',
            '-H', 'Content-Type: application/json',
            '-H', f'New-Api-User: {uid}',
            '-H', f'Cookie: session={session}',
            '-H', 'User-Agent: Mozilla/5.0',
            '-H', f'Origin: {SMAI_API}',
            '-d', '{}'
        ], capture_output=True, timeout=20)
        result = json.loads(r.stdout.decode('utf-8', errors='replace'))

        detail = {'username': username}

        if result.get('success'):
            # 提取签到详情
            data = result.get('data', {})
            earned_kb = data.get('quota_awarded', 0)  # 本次获得的点数

            # 查询最新统计
            r2 = subprocess.run([
                'curl', '-s', '--max-time', '15',
                f'{SMAI_API}/api/user/checkin?year={(datetime.now() + timedelta(hours=8)).year}',
                '-H', 'Accept: application/json',
                '-H', f'New-Api-User: {uid}',
                '-H', f'Cookie: session={session}',
                '-H', 'User-Agent: Mozilla/5.0'
            ], capture_output=True, timeout=20)
            stats2 = json.loads(r2.stdout.decode('utf-8', errors='replace'))
            st = stats2.get('data', {}).get('stats', {})
            total_days = st.get('total_checkins', '?')
            total_quota_kb = st.get('total_quota', 0)

            # 格式化额度 (美元，通常精度较高)
            def fmt_quota(val):
                if val is None: return '?'
                try:
                    val = float(val)
                    if val == 0: return '$0'
                    return f"${val:.6f}".rstrip('0').rstrip('.')
                except:
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
        return str(e), False, {}

# ================= 主程序 =================
def main():
    # 修改：获取北京时间，替代本地时间
    beijing_time = datetime.now() + timedelta(hours=8)
    is_morning = beijing_time.hour < 15  # 北京时间 0-15 点为上午，15-24 点为下午

    log("=" * 50)
    log(f"🚀 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI)")
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
    config = {
        'glados': [f"account_{i+1}" for i in range(len(glados_cookies))],
        'ikuuu': [e for e, _ in ikuuu_accounts],
        'smai': [s[:20]+"..." for s in smai_sessions],
    }

    # 下午：全部成功则跳过
    if not is_morning and all_done(state, config):
        log("🎉 上午全部成功，下午跳过！")
        log("SKIP_AFTERNOON=true")
        return

    # ========== GLaDOS ==========
    g_success = 0; g_total = len(glados_cookies)
    if glados_cookies:
        for i, ck in enumerate(glados_cookies):
            key = f"account_{i+1}"
            g = GLaDOS(ck)
            if is_skipped(state, 'glados', key, is_morning):
                g.checkin_msg = "上午已签，跳过"; g.success = True
            else:
                g.checkin()
                if g.success: record_success(state, 'glados', key)
                elif is_expired('glados', g.checkin_msg):
                    expired.append(f"🖥️ GLaDOS [账号{i+1}] Cookie 可能过期")
            g.load_info()
            results.append(g.text())
            if g.success: g_success += 1
    else:
        results.append("### 🖥️ GLaDOS\n未配置，跳过")

    # ========== ikuuu ==========
    results.append("\n### 📶 ikuuu 签到结果")
    i_success = 0; i_total = len(ikuuu_accounts)
    if ikuuu_accounts:
        msgs = []
        for email, pwd in ikuuu_accounts:
            if is_skipped(state, 'ikuuu', email, is_morning):
                msgs.append(f"{email}: 上午已签，跳过")
                i_success += 1
            else:
                msg, ok = ikuuu_one(email, pwd)
                msgs.append(f"{email}: {msg}")
                if ok:
                    record_success(state, 'ikuuu', email)
                    i_success += 1
                elif is_expired('ikuuu', msg): expired.append(f"📶 ikuuu [{email}] 账号可能失效")
        results.append(f"• 结果：{' | '.join(msgs)}")
    else:
        results.append("• 未配置，跳过")

    # ========== SMAI ==========
    results.append("\n### ✅ SMAI.AI 签到结果")
    smai_user_ids = get_smai_user_ids()
    s_success = 0; s_total = len(smai_sessions)
    if smai_sessions:
        for i, sess in enumerate(smai_sessions):
            key = sess[:20] + "..."
            uid = smai_user_ids[i] if i < len(smai_user_ids) else ''
            if is_skipped(state, 'smai', key, is_morning):
                results.append(f"• {uid or '账号'+str(i+1)}: 上午已签，跳过")
                s_success += 1
            else:
                log(f"  SMAI 签到... ({key})")
                msg, ok, detail = smai_one(sess, uid)
                uname = detail.get('username', uid or f'账号{i+1}')
                if ok:
                    record_success(state, 'smai', key)
                    s_success += 1
                    results.append(f"• {uname}: {msg}")
                elif is_expired('smai', msg):
                    expired.append(f"✅ SMAI [{key}] Session 可能过期")
                    results.append(f"• {uname}: {msg}")
                else:
                    results.append(f"• {uname}: {msg}")
    else:
        results.append("• 未配置，跳过")

    save_state(state)

    # ========== 推送 ==========
    # 汇总统计
    total_done = g_success + i_success + s_success
    total_all = (g_total if g_total else 0) + (i_total if i_total else 0) + (s_total if s_total else 0)
    summary = f"📊 汇总：{total_done}/{total_all} 成功"
    if g_total: summary += f" | GLaDOS {g_success}/{g_total}"
    if i_total: summary += f" | ikuuu {i_success}/{i_total}"
    if s_total: summary += f" | SMAI {s_success}/{s_total}"

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
