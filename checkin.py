#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI)
功能：
- GLaDOS 全自动签到 + 积分查询
- ikuuu.nl 全自动签到
- SMAI.AI 全自动签到
- wpush 微信推送
- 智能跳过：上午全部成功则下午跳过签到+推送
"""

import requests
import json
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

# Fix Windows Unicode Output
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 全局配置 =================
GLADOS_DOMAINS = [
    "https://glados.cloud",
    "https://glados.rocks",
    "https://glados.network",
]

COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}

SMAI_API = "https://api.smai.ai"

# 状态文件路径（GitHub Actions 中相对于工作目录）
STATE_FILE = os.environ.get("CHECKIN_STATE_FILE", ".checkin_state.json")

# ================= 工具函数 =================
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def extract_cookie(raw: str):
    """提取 GLaDOS Cookie，支持 Cookie-Editor 冒号格式"""
    if not raw:
        return None
    raw = raw.strip()
    if 'koa:sess=' in raw or 'koa:sess.sig=' in raw:
        return raw
    if raw.startswith('{'):
        try:
            return 'koa.sess=' + json.loads(raw).get('token')
        except:
            pass
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50:
        return 'koa:sess=' + raw
    return raw

def get_glados_cookies():
    """获取 GLaDOS Cookie 列表"""
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw:
        log("⚠️ 未配置 GLADOS_COOKIE，跳过 GLaDOS 签到")
        return []
    sep = '\n' if '\n' in raw else '&'
    return [extract_cookie(c) for c in raw.split(sep) if c.strip()]

# ================= 状态管理（上午成功则下午跳过） =================
def load_state():
    """加载今日签到状态"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
                if state.get('date') == str(date.today()):
                    return state
    except:
        pass
    return {'date': str(date.today()), 'morning': {}, 'skip_afternoon': False}

def save_state(state):
    """保存签到状态"""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        log(f"💾 状态已保存到 {STATE_FILE}")
    except Exception as e:
        log(f"⚠️ 保存状态失败: {e}")

def should_skip_afternoon(state):
    """判断下午是否应该跳过（上午全部成功）"""
    if state.get('skip_afternoon'):
        return True
    morning = state.get('morning', {})
    if not morning:
        return False
    # 检查所有配置了的平台是否都成功
    platforms = []
    if os.environ.get("GLADOS_COOKIE"):
        platforms.append('glados')
    if os.environ.get("IKUUU_EMAIL") and os.environ.get("IKUUU_PASSWORD"):
        platforms.append('ikuuu')
    if os.environ.get("SMAI_SESSION"):
        platforms.append('smai')
    if not platforms:
        return False
    return all(morning.get(p) == 'success' for p in platforms)

# ================= wpush 推送函数 =================
def wpush(apikey, title, content, channel="wechat", topic_code=""):
    if not apikey:
        return
    try:
        url = "https://api.wpush.cn/api/v1/send"
        payload = {"apikey": apikey, "title": title, "content": content, "channel": channel}
        if topic_code:
            payload["topic_code"] = topic_code
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        log("✅ wpush 推送成功" if resp.status_code == 200 else f"❌ wpush 推送失败: {resp.text}")
    except Exception as e:
        log(f"❌ wpush 推送异常: {e}")

# ================= GLaDOS 签到逻辑 =================
class GLaDOS:
    def __init__(self, cookie):
        self.cookie = cookie
        self.domain = GLADOS_DOMAINS[0]
        self.email = "未知账号"
        self.left_days = "?"
        self.points = "?"
        self.points_change = "?"
        self.exchange_info = ""
        self.checkin_msg = "执行失败"
        self.success = False

    def req(self, method, path, data=None):
        for d in GLADOS_DOMAINS:
            try:
                url = f"{d}{path}"
                h = COMMON_HEADERS.copy()
                h['Cookie'] = self.cookie
                h['Origin'] = d
                h['Referer'] = f"{d}/console/checkin"
                if method == 'GET':
                    resp = requests.get(url, headers=h, timeout=10)
                else:
                    resp = requests.post(url, headers=h, json=data, timeout=10)
                if resp.status_code == 200:
                    self.domain = d
                    return resp.json()
            except Exception as e:
                log(f"⚠️ {d} 请求失败: {e}")
                continue
        return None

    def get_status(self):
        res = self.req('GET', '/api/user/status')
        if res and 'data' in res:
            d = res['data']
            self.email = d.get('email', '未知账号')
            self.left_days = str(d.get('leftDays', '?')).split('.')[0]
            return True
        return False

    def get_points(self):
        res = self.req('GET', '/api/user/points')
        if res and 'points' in res:
            self.points = str(res.get('points', '0')).split('.')[0]
            history = res.get('history', [])
            if history:
                change = str(history[0].get('change', '0')).split('.')[0]
                if not change.startswith('-'):
                    change = '+' + change
                self.points_change = change
            plans = res.get('plans', {})
            pts = int(self.points) if self.points.isdigit() else 0
            lines = []
            for _, p in plans.items():
                need, days = p['points'], p['days']
                status = "可兑换" if pts >= need else f"差{need - pts}分"
                lines.append(f"{'✅' if pts >= need else '❌'} {need}分→{days}天 ({status})")
            self.exchange_info = "\n".join(lines)
            return True
        return False

    def checkin(self):
        res = self.req('POST', '/api/user/checkin', {'token': 'glados.cloud'})
        if res:
            self.checkin_msg = res.get('message', '签到失败')
            self.success = "Checkin" in self.checkin_msg and "already" not in self.checkin_msg.lower()
        else:
            self.checkin_msg = "网络错误/域名不可用"
        return self.checkin_msg

    def get_result_text(self):
        return (
            f"### 🖥️ GLaDOS - {self.email}\n"
            f"• 当前积分：{self.points} ({self.points_change})\n"
            f"• 剩余天数：{self.left_days} 天\n"
            f"• 签到结果：{self.checkin_msg}\n\n"
            f"🎁 积分兑换：\n{self.exchange_info if self.exchange_info else '暂无兑换信息'}"
        )

# ================= ikuuu 签到逻辑 =================
def get_ikuuu_accounts():
    """获取 ikuuu 账号列表，支持多账号（用 & 分隔 email:password）"""
    # 新格式：IKUUU_ACCOUNTS = email1:password1&email2:password2
    accounts_raw = os.environ.get('IKUUU_ACCOUNTS', '')
    if accounts_raw:
        accounts = []
        for item in accounts_raw.split('&'):
            item = item.strip()
            if ':' in item:
                email, pwd = item.split(':', 1)
                accounts.append((email.strip(), pwd.strip()))
        return accounts

    # 兼容旧格式：单账号
    email = os.environ.get('IKUUU_EMAIL', '')
    pwd = os.environ.get('IKUUU_PASSWORD', '')
    if email and pwd:
        return [(email, pwd)]
    return []

def ikuuu_checkin_one(email, passwd):
    """单个 ikuuu 账号签到"""
    session = requests.session()
    header = {'origin': 'https://ikuuu.nl', 'user-agent': COMMON_HEADERS['User-Agent']}
    try:
        resp = session.post('https://ikuuu.nl/auth/login',
                           headers=header, data={'email': email, 'passwd': passwd}, timeout=10)
        login_res = resp.json()
        log(f"  ikuuu 登录 [{email}]: {login_res['msg']}")
        checkin_res = session.post('https://ikuuu.nl/user/checkin', headers=header, timeout=10).json()
        log(f"  ikuuu 签到 [{email}]: {checkin_res['msg']}")
        success = "成功" in checkin_res['msg'] or "获得" in checkin_res['msg']
        return checkin_res['msg'], success
    except Exception as e:
        log(f"❌ ikuuu [{email}] 异常: {e}")
        return str(e), False

def ikuuu_checkin():
    """执行 ikuuu 签到（支持多账号）"""
    accounts = get_ikuuu_accounts()
    if not accounts:
        log("⚠️ 未配置 IKUUU，跳过")
        return "未配置", False

    results = []
    all_ok = True
    for email, pwd in accounts:
        msg, ok = ikuuu_checkin_one(email, pwd)
        results.append(f"{email}: {msg}")
        if not ok:
            all_ok = False
    return " | ".join(results), all_ok

# ================= SMAI.AI 签到逻辑 =================
def get_smai_sessions():
    """获取 SMAI session 列表，支持多账号（用 & 分隔）"""
    raw = os.environ.get('SMAI_SESSION', '')
    if not raw:
        return []
    sep = '\n' if '\n' in raw else '&'
    return [s.strip() for s in raw.split(sep) if s.strip()]

def smai_checkin_one(session, user_id_hint=''):
    """单个 SMAI 账号签到"""
    def smai_req(method, path, body=None, uid=None):
        url = f"{SMAI_API}{path}"
        headers = {
            'Accept': 'application/json',
            'New-Api-User': uid or user_id_hint,
            'Cookie': f'session={session}',
            'User-Agent': COMMON_HEADERS['User-Agent'],
            'Referer': f'{SMAI_API}/console/checkin',
            'Origin': SMAI_API
        }
        if body:
            headers['Content-Type'] = 'application/json'
        try:
            if method == 'GET':
                resp = requests.get(url, headers=headers, timeout=10)
            else:
                resp = requests.post(url, headers=headers, json=body or {}, timeout=10)
            return resp.json()
        except Exception as e:
            return {'success': False, 'message': str(e)}

    try:
        # 自动获取 user_id
        uid = user_id_hint
        if not uid:
            try:
                resp = requests.get(f"{SMAI_API}/api/user/self",
                    headers={'Accept': 'application/json', 'Cookie': f'session={session}',
                             'User-Agent': COMMON_HEADERS['User-Agent']}, timeout=10)
                info = resp.json()
                if info.get('success') and info.get('data', {}).get('id'):
                    uid = str(info['data']['id'])
                    username = info['data'].get('username', uid)
                    log(f"  SMAI 账号: {username} (ID: {uid})")
            except:
                return "无法获取 User ID", False

        # 查询状态
        year = datetime.now().year
        stats = smai_req('GET', f'/api/user/checkin?year={year}', uid=uid)
        if stats.get('success') and stats.get('data', {}).get('checked_in_today'):
            return "今日已签到", True

        # 执行签到
        result = smai_req('POST', '/api/user/checkin', uid=uid)
        if result.get('success'):
            return "签到成功", True
        else:
            msg = result.get('message', '签到失败')
            return msg, "已签到" in msg
    except Exception as e:
        return str(e), False

def smai_checkin():
    """执行 SMAI.AI 签到（支持多账号）"""
    sessions = get_smai_sessions()
    if not sessions:
        log("⚠️ 未配置 SMAI_SESSION，跳过")
        return "未配置", False

    user_id_hint = os.environ.get('SMAI_USER_ID', '')
    results = []
    all_ok = True
    for sess in sessions:
        # 只显示前20字符作为标识
        sess_short = sess[:20] + "..."
        log(f"📝 SMAI 签到中... (session: {sess_short})")
        msg, ok = smai_checkin_one(sess, user_id_hint)
        results.append(msg)
        if not ok:
            all_ok = False
    return " | ".join(results), all_ok

# ================= Token 过期检测 =================
# 各平台认证失败关键词
TOKEN_EXPIRED_KEYWORDS = {
    'glados': ['Unauthorized', 'login', '请重新登录', 'authentication', '未登录', 'invalid token', '401'],
    'ikuuu': ['密码错误', '用户不存在', '登录失败', '认证失败', 'unauthorized', '401', '邮箱或密码错误'],
    'smai': ['未登录', '无权', 'unauthorized', '401', 'token', 'expired', '过期', '请重新', '未提供'],
}

def is_token_expired(platform, msg):
    """检测消息是否表示 token/session 过期"""
    if not msg or msg == '未配置':
        return False
    msg_lower = msg.lower()
    for keyword in TOKEN_EXPIRED_KEYWORDS.get(platform, []):
        if keyword.lower() in msg_lower:
            return True
    return False

# ================= 主程序 =================
def main():
    now = datetime.now()
    hour = now.hour
    is_morning = hour < 15

    log("=" * 50)
    log(f"🚀 多平台自动签到启动 (GLaDOS + ikuuu + SMAI.AI)")
    log(f"⏰ 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} {'(上午)' if is_morning else '(下午)'}")
    log("=" * 50)

    state = load_state()

    if not is_morning:
        if should_skip_afternoon(state):
            log("🎉 上午全部签到成功，下午跳过签到+推送！")
            log("SKIP_AFTERNOON=true")
            return
        else:
            log("📋 上午有未完成的平台，继续下午签到")

    all_results = []
    platforms_status = {}
    expired_warnings = []  # 收集 token 过期警告

    # ========== 1. GLaDOS 签到 ==========
    glados_cookies = get_glados_cookies()
    glados_success = 0
    glados_total = len(glados_cookies)

    if glados_cookies:
        all_results.append("### 🖥️ GLaDOS 签到结果")
        for i, cookie in enumerate(glados_cookies):
            g = GLaDOS(cookie)
            g.checkin()
            g.get_status()
            g.get_points()
            all_results.append(g.get_result_text())
            if g.success:
                glados_success += 1
            # 检测 token 过期
            elif is_token_expired('glados', g.checkin_msg):
                account = g.email if g.email != "未知账号" else f"账号{i+1}"
                expired_warnings.append(f"🖥️ GLaDOS [{account}] Cookie 可能已过期")
    else:
        all_results.append("### 🖥️ GLaDOS 签到结果\n未配置Cookie，跳过签到")

    platforms_status['glados'] = 'success' if glados_total > 0 and glados_success == glados_total else ('partial' if glados_success > 0 else 'skip')

    # ========== 2. ikuuu 签到 ==========
    all_results.append("\n---\n### 📶 ikuuu 签到结果")
    ikuuu_accounts = get_ikuuu_accounts()
    ikuuu_msg, ikuuu_ok = ikuuu_checkin()
    all_results.append(f"• 签到结果：{ikuuu_msg}")
    if not ikuuu_ok and ikuuu_accounts:
        for email, _ in ikuuu_accounts:
            if email in ikuuu_msg or is_token_expired('ikuuu', ikuuu_msg):
                expired_warnings.append(f"📶 ikuuu [{email}] 账号密码可能失效")
                break
        if not ikuuu_ok and '未配置' not in ikuuu_msg:
            expired_warnings.append(f"📶 ikuuu 账号可能失效，请检查")
    platforms_status['ikuuu'] = 'success' if ikuuu_ok else ('skip' if '未配置' in ikuuu_msg else 'fail')

    # ========== 3. SMAI.AI 签到 ==========
    all_results.append("\n---\n### ✅ SMAI.AI 签到结果")
    smai_sessions = get_smai_sessions()
    smai_msg, smai_ok = smai_checkin()
    all_results.append(f"• 签到结果：{smai_msg}")
    if not smai_ok and '未配置' not in smai_msg:
        if is_token_expired('smai', smai_msg):
            expired_warnings.append(f"✅ SMAI.AI Session 可能已过期，请更新")
    platforms_status['smai'] = 'success' if smai_ok else ('skip' if '未配置' in smai_msg else 'fail')

    # ========== 更新状态 ==========
    state['morning'] = platforms_status
    active_platforms = [k for k, v in platforms_status.items() if v != 'skip']
    if active_platforms and all(platforms_status.get(p) == 'success' for p in active_platforms):
        state['skip_afternoon'] = True
        log("🎉 所有平台签到成功！下午将跳过签到+推送")
    save_state(state)

    # ========== 组装推送内容 ==========
    push_content = ""

    # ⚠️ Token 过期警告（放在最前面）
    if expired_warnings:
        push_content += "⚠️ **Token 过期警告**\n"
        for w in expired_warnings:
            push_content += f"  🔴 {w}\n"
        push_content += "  👉 请更新对应平台的 Secret\n\n"

    push_content += "\n".join(all_results)
    push_content += f"\n\n---\n⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}"

    # 推送标题带警告标识
    warning_prefix = "⚠️ " if expired_warnings else ""
    ikuuu_short = ikuuu_msg[:15] + "..." if len(ikuuu_msg) > 15 else ikuuu_msg
    push_title = f"{warning_prefix}多平台签到 | GLaDOS {glados_success}/{glados_total} | ikuuu: {ikuuu_short} | SMAI: {smai_msg[:10]}"

    wpush_apikey = os.environ.get("WPUSH_APIKEY")
    if wpush_apikey:
        wpush(wpush_apikey, push_title, push_content)
    else:
        log("⚠️ 未配置 WPUSH_APIKEY，跳过推送")

    log("\n" + "=" * 50)
    log("📋 签到完成！结果汇总：")
    log(push_content)
    log("=" * 50)

    if expired_warnings:
        log(f"\n🔴 发现 {len(expired_warnings)} 个 token 可能过期！")

    if state.get('skip_afternoon'):
        log("⏭️ 下次运行(下午)将自动跳过")

if __name__ == '__main__':
    main()
