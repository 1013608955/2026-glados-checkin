#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 多平台自动签到 (GLaDOS + ikuuu)
功能：
- GLaDOS 全自动签到 + 积分查询
- ikuuu.nl 全自动签到
- wpush 微信推送（整合两个平台的签到结果）
- 智能多域名切换 (GLaDOS 优先 glados.cloud)
- 支持 Cookie-Editor 导出格式 (GLaDOS)
"""

import requests
import json
import os
import sys
import time
from datetime import datetime

# Fix Windows Unicode Output
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 全局配置 =================
# GLaDOS 域名优先级：Cloud 第一
GLADOS_DOMAINS = [
    "https://glados.cloud",
    "https://glados.rocks", 
    "https://glados.network",
]

# 通用请求头
COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}

# ================= 工具函数 =================
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def extract_cookie(raw: str):
    """提取 GLaDOS Cookie，支持 Cookie-Editor 冒号格式"""
    if not raw: return None
    raw = raw.strip()
    
    # Cookie-Editor 格式 (koa:sess=xxx; koa:sess.sig=yyy)
    if 'koa:sess=' in raw or 'koa:sess.sig=' in raw:
        return raw
        
    # JSON
    if raw.startswith('{'):
        try:
            return 'koa.sess=' + json.loads(raw).get('token')
        except: pass
        
    # JWT Token
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50:
        return 'koa:sess=' + raw
        
    # Standard
    return raw

def get_glados_cookies():
    """获取 GLaDOS Cookie 列表"""
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw:
        log("⚠️ 未配置 GLADOS_COOKIE，跳过 GLaDOS 签到")
        return []
    
    # Split by enter or &
    sep = '\n' if '\n' in raw else '&'
    return [extract_cookie(c) for c in raw.split(sep) if c.strip()]

# ================= wpush 推送函数 =================
def wpush(apikey, title, content, channel="wechat", topic_code=""):
    if not apikey:
        log("❌ 未配置 WPUSH_APIKEY")
        return
    try:
        url = "https://api.wpush.cn/api/v1/send"
        payload = {
            "apikey": apikey,
            "title": title,
            "content": content,
            "channel": channel
        }
        if topic_code:
            payload["topic_code"] = topic_code
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            log("✅ wpush 推送成功")
        else:
            log(f"❌ wpush 推送失败: {resp.text}")
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
        
    def req(self, method, path, data=None):
        """带自动域名切换的请求"""
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
                    self.domain = d # 记录可用域名
                    return resp.json()
            except Exception as e:
                log(f"⚠️ {d} 请求失败: {e}")
                continue
        return None

    def get_status(self):
        """获取 GLaDOS 账号状态：天数、邮箱"""
        res = self.req('GET', '/api/user/status')
        if res and 'data' in res:
            d = res['data']
            self.email = d.get('email', '未知账号')
            self.left_days = str(d.get('leftDays', '?')).split('.')[0]
            return True
        return False

    def get_points(self):
        """获取 GLaDOS 积分、变化历史、兑换计划"""
        res = self.req('GET', '/api/user/points')
        if res and 'points' in res:
            # 当前积分
            self.points = str(res.get('points', '0')).split('.')[0]
            
            # 最近一次积分变化
            history = res.get('history', [])
            if history:
                last = history[0]
                change = str(last.get('change', '0')).split('.')[0]
                if not change.startswith('-'):
                    change = '+' + change
                self.points_change = change
            
            # 兑换计划
            plans = res.get('plans', {})
            pts = int(self.points) if self.points.isdigit() else 0
            exchange_lines = []
            for plan_id, plan_data in plans.items():
                need = plan_data['points']
                days = plan_data['days']
                if pts >= need:
                    exchange_lines.append(f"✅ {need}分→{days}天 (可兑换)")
                else:
                    exchange_lines.append(f"❌ {need}分→{days}天 (差{need-pts}分)")
            self.exchange_info = "\n".join(exchange_lines)
            return True
        return False

    def checkin(self):
        """执行 GLaDOS 签到"""
        res = self.req('POST', '/api/user/checkin', {'token': 'glados.cloud'})
        if res:
            self.checkin_msg = res.get('message', '签到失败')
        else:
            self.checkin_msg = "网络错误/域名不可用"
        return self.checkin_msg

    def get_result_text(self):
        """生成 GLaDOS 签到结果文本（优化格式）"""
        return f"""### 🖥️ GLaDOS - {self.email}
• 当前积分：{self.points} ({self.points_change})
• 剩余天数：{self.left_days} 天
• 签到结果：{self.checkin_msg}

🎁 积分兑换：
{self.exchange_info if self.exchange_info else '暂无兑换信息'}
"""

# ================= ikuuu 签到逻辑 =================
def ikuuu_checkin():
    """执行 ikuuu.nl 签到"""
    # 获取 ikuuu 账号信息
    email = os.environ.get('IKUUU_EMAIL', '')
    passwd = os.environ.get('IKUUU_PASSWORD', '')
    
    if not email or not passwd:
        log("⚠️ 未配置 IKUUU_EMAIL/IKUUU_PASSWORD，跳过 ikuuu 签到")
        return "未配置账号信息，跳过签到"
    
    session = requests.session()
    login_url = 'https://ikuuu.nl/auth/login'
    check_url = 'https://ikuuu.nl/user/checkin'
    
    header = {
        'origin': 'https://ikuuu.nl',
        'user-agent': COMMON_HEADERS['User-Agent']
    }
    
    data = {
        'email': email,
        'passwd': passwd
    }
    
    try:
        log('🔑 开始 ikuuu 登录...')
        # 登录
        response = session.post(url=login_url, headers=header, data=data, timeout=10)
        response.raise_for_status()
        login_res = response.json()
        log(f"ikuuu 登录结果: {login_res['msg']}")
        
        # 签到
        checkin_res = session.post(url=check_url, headers=header, timeout=10).json()
        log(f"ikuuu 签到结果: {checkin_res['msg']}")
        return checkin_res['msg']
        
    except Exception as e:
        error_msg = f"签到失败：{str(e)}"
        log(f"❌ ikuuu 签到异常: {error_msg}")
        return error_msg

# ================= 主程序 =================
def main():
    log("🚀 多平台自动签到脚本启动 (GLaDOS + ikuuu)")
    
    # 存储所有签到结果
    all_results = []
    
    # 1. 执行 GLaDOS 签到
    glados_cookies = get_glados_cookies()
    glados_success = 0
    if glados_cookies:
        all_results.append("### 🖥️ GLaDOS 签到结果")
        for cookie in glados_cookies:
            g = GLaDOS(cookie)
            # 执行签到
            g.checkin()
            # 获取账号信息
            g.get_status()
            g.get_points()
            # 记录结果
            all_results.append(g.get_result_text())
            # 统计成功数
            if "Checkin" in g.checkin_msg and "already" not in g.checkin_msg.lower():
                glados_success += 1
    else:
        all_results.append("### 🖥️ GLaDOS 签到结果\n未配置Cookie，跳过签到")
    
    # 2. 执行 ikuuu 签到（优化拼接逻辑）
    all_results.append("\n---\n### 📶 ikuuu 签到结果")
    ikuuu_result = ikuuu_checkin()
    all_results.append(f"• 签到结果：{ikuuu_result}")
    
    # 3. 整理推送内容
    # 优化标题：精简 ikuuu 结果展示
    ikuuu_title = ikuuu_result[:15] + "..." if len(ikuuu_result) > 15 else ikuuu_result
    push_title = f"多平台签到结果 | GLaDOS成功{glados_success}/{len(glados_cookies)} | ikuuu: {ikuuu_title}"
    push_content = "\n".join(all_results)
    push_content += f"\n\n---\n⏰ 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    # 4. 执行 wpush 推送
    wpush_apikey = os.environ.get("WPUSH_APIKEY")
    if wpush_apikey:
        wpush(wpush_apikey, push_title, push_content, channel="wechat")
    else:
        log("❌ 未配置 WPUSH_APIKEY，跳过推送")
    
    # 打印完整结果
    log("\n" + "="*50)
    log("📋 本次签到完整结果：")
    log(push_content)
    log("="*50)
    
    log("✅ 多平台签到任务执行完成")

if __name__ == '__main__':
    main()
