#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ikuuu (https://ikuuu.win) 自动登录 → 获取并校验 Cookie。本地与 CI 通用。

为什么必须用浏览器
------------------
登录接口 `POST /auth/login` 强制 Geetest v4（极验四代）验证。实测：
  - 不带 phase 提交        -> {"result":"invalid_phase", ...}
  - 带 phase=password 提交  -> {"result":"captcha_failed", "msg":"系统无法接受您的验证结果"}
即：纯 requests/curl 无论如何都过不去，只有真实浏览器能通过验证，
所以登录环节由 Playwright 驱动；拿到 Cookie 后再交给 requests 校验与复用。

有效性判定（刻意不消耗签到）
----------------------------
`GET /user`：返回 200 = Cookie 有效；302 跳 `/auth/login` = 已失效。
（不使用 `/user/checkin` 做验证，避免把当天的签到机会用掉）

用法
----
  python ikuuu_login.py status     查看当前 Cookie：是否有效、获取时间、账龄
  python ikuuu_login.py validate   校验已保存的 Cookie
  python ikuuu_login.py login      强制重新登录（浏览器过 Geetest）
  python ikuuu_login.py refresh    仅当 Cookie 失效时才登录（日常推荐）

退出码
------
  0  成功
  10 Cookie 失效 / 需要登录
  20 验证码(Geetest)未通过
  21 需要 2FA 动态口令，但未配置 IKUUU_TOTP_SECRET
  22 需要邮件验证码（无法自动获取）
  30 缺少凭据（IKUUU_EMAIL / IKUUU_PASSWORD）
  40 缺少 Playwright 或浏览器未安装
  50 网络/站点异常
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

CST = timezone(timedelta(hours=8))
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EXIT_OK, EXIT_NEED_LOGIN, EXIT_CAPTCHA = 0, 10, 20
EXIT_NEED_2FA, EXIT_NEED_EMAIL_CODE = 21, 22
EXIT_NO_CREDS, EXIT_NO_PLAYWRIGHT, EXIT_NETWORK = 30, 40, 50


# ============================== 基础设施 ==============================
def log(msg):
    stamp = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def mask_account(email):
    """账号脱敏：日志与产物里都不出现完整邮箱。"""
    if not email or "@" not in email:
        return "***"
    name, dom = email.split("@", 1)
    return f"{name[:2]}***@{dom}"


def load_dotenv(path=".env"):
    """极简 .env 解析（不引入 python-dotenv 依赖）。
    KEY=VALUE，支持 # 注释、export 前缀、单双引号。"""
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                data[key] = val
    return data


def _as_bool(v, default=True):
    if v is None or v == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off")


class Config:
    """统一配置：环境变量优先于 .env（便于 CI 用 Secrets 覆盖）。"""

    def __init__(self, env):
        self.email = env.get("IKUUU_EMAIL", "").strip()
        self.password = env.get("IKUUU_PASSWORD", "")
        self.base_url = env.get("IKUUU_BASE_URL", "https://ikuuu.win").strip().rstrip("/")
        self.proxy = env.get("IKUUU_PROXY", "").strip()
        self.cookie_file = env.get("IKUUU_COOKIE_FILE", "ikuuu_cookie.json").strip()
        self.headless = _as_bool(env.get("IKUUU_HEADLESS"), default=True)
        # 默认 300s：Geetest 需人工在浏览器窗口里完成，留足操作时间
        self.login_timeout = int(env.get("IKUUU_LOGIN_TIMEOUT", "300"))
        self.totp_secret = env.get("IKUUU_TOTP_SECRET", "").strip()
        self.user_agent = env.get("IKUUU_USER_AGENT", DEFAULT_UA).strip() or DEFAULT_UA
        self.remember = _as_bool(env.get("IKUUU_REMEMBER"), default=True)
        self.warn_after_days = int(env.get("IKUUU_WARN_AFTER_DAYS", "25"))
        # 刷新时机：距过期不足 N 天就刷新（别等彻底失效，否则中间会断档）。
        # ikuuu 的 Cookie 是固定 7 天、不滑动续期，所以这个阈值很关键。
        self.refresh_before_days = float(env.get("IKUUU_REFRESH_BEFORE_DAYS", "1.5"))
        # 登录成功后是否用 gh CLI 把 Cookie 回写到仓库 Secret，
        # 这样 GitHub Actions 端可以完全无人值守（只做签到，不碰登录）。
        self.push_secret = _as_bool(env.get("IKUUU_PUSH_SECRET"), default=False)
        self.secret_name = env.get("IKUUU_SECRET_NAME", "IKUUU_COOKIE").strip() or "IKUUU_COOKIE"
        self.repo = env.get("IKUUU_REPO", "").strip()

    @classmethod
    def load(cls, dotenv_path=".env"):
        merged = load_dotenv(dotenv_path)
        merged.update({k: v for k, v in os.environ.items() if k.startswith("IKUUU_")})
        return cls(merged)

    @property
    def host(self):
        return self.base_url.split("://", 1)[-1].split("/")[0]


def get_session(cfg):
    s = requests.Session()
    s.headers.update({
        "User-Agent": cfg.user_agent,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    if cfg.proxy:
        # 只对显式配置的 IKUUU_PROXY 生效，忽略环境自带的代理
        s.trust_env = False
        s.proxies = {"http": cfg.proxy, "https": cfg.proxy}
    return s


# ============================== Cookie 存储 ==============================
def load_cookie_store(cfg):
    if not os.path.exists(cfg.cookie_file):
        return None
    try:
        with open(cfg.cookie_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ 读取 {cfg.cookie_file} 失败：{type(e).__name__}: {e}")
        return None


def save_cookie_store(cfg, cookie_str, cookies, source, validated=None, note=""):
    now = datetime.now(CST)
    detail = {}
    expire_in = None
    for c in cookies or []:
        detail[c.get("name", "")] = {
            "expires": c.get("expires"),
            "httpOnly": c.get("httpOnly"),
            "secure": c.get("secure"),
        }
        if c.get("name") == "expire_in":
            try:
                expire_in = int(c.get("value"))
            except Exception:
                pass

    data = {
        "cookie": cookie_str,
        "obtained_at": now.isoformat(),
        "obtained_at_ts": int(now.timestamp()),
        "source": source,
        "account": mask_account(cfg.email),
        "validated": validated,
        "last_validated_at": now.isoformat() if validated else None,
        "expire_in_ts": expire_in,
        "note": note,
        "cookies": detail,
    }
    tmp = cfg.cookie_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg.cookie_file)
    log(f"💾 Cookie 已写入 {cfg.cookie_file}（source={source}）")
    return data


def cookie_age_days(store):
    ts = (store or {}).get("obtained_at_ts")
    if not ts:
        return None
    return (time.time() - int(ts)) / 86400.0


def cookie_remaining_days(store):
    """距 Cookie 过期还剩多少天；无 expire_in 信息时返回 None。"""
    ts = (store or {}).get("expire_in_ts")
    if not ts:
        return None
    return (int(ts) - time.time()) / 86400.0


def push_secret_to_github(cfg, cookie_str):
    """用 gh CLI 把 Cookie 回写到仓库 Secret，让 Actions 端无需再登录。

    需要本机已 `gh auth login`。Cookie 只通过 stdin 传入，不会出现在命令行
    参数（避免被 ps / 日志捕获），也不会打印到日志。
    """
    import subprocess

    if not cookie_str:
        return False, "empty_cookie"
    cmd = ["gh", "secret", "set", cfg.secret_name, "--body", cookie_str]
    if cfg.repo:
        cmd += ["--repo", cfg.repo]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "gh 未安装（https://cli.github.com/）"
    except subprocess.TimeoutExpired:
        return False, "gh secret set 超时"
    if p.returncode == 0:
        return True, "ok"
    err = (p.stderr or p.stdout or "").strip().splitlines()
    hint = err[-1] if err else f"exit={p.returncode}"
    if "auth" in hint.lower() or "login" in hint.lower():
        hint += "（请先执行 gh auth login）"
    return False, hint[:200]


# ============================== 有效性校验 ==============================
def validate_cookie(cfg, cookie_str):
    """返回 (bool, reason)。刻意用 GET /user，不消耗签到。"""
    if not cookie_str:
        return False, "empty_cookie"
    s = get_session(cfg)
    try:
        r = s.get(f"{cfg.base_url}/user", headers={"Cookie": cookie_str},
                  timeout=20, allow_redirects=False)
    except Exception as e:
        return False, f"network_error:{type(e).__name__}:{str(e)[:120]}"

    if r.status_code == 200:
        return True, "ok"
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if "/auth/login" in loc:
            return False, "expired_redirect_to_login"
        return False, f"unexpected_redirect:{loc}"
    if r.status_code in (401, 403):
        return False, f"http_{r.status_code}"
    return False, f"http_{r.status_code}"


# ============================== TOTP（可选） ==============================
def totp_code(secret, digits=6, period=30):
    """标准 TOTP（RFC 6238），纯标准库实现，无需额外依赖。"""
    s = secret.upper().replace(" ", "").replace("-", "")
    pad = "=" * ((8 - len(s) % 8) % 8)
    key = base64.b32decode(s + pad)
    counter = int(time.time()) // period
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ============================== 浏览器登录 ==============================
def _dump_debug(page, tag):
    """失败时留证：截图 + 页面 HTML（文件名已 gitignore）。"""
    ts = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    png, html = f"ikuuu_debug_{tag}_{ts}.png", f"ikuuu_debug_{tag}_{ts}.html"
    try:
        page.screenshot(path=png, full_page=True)
        with open(html, "w", encoding="utf-8") as f:
            f.write(page.content())
        log(f"🖼️ 已留存排查材料：{png} / {html}")
    except Exception as e:
        log(f"⚠️ 保存排查材料失败：{type(e).__name__}: {e}")


def _toggle_remember(page, wanted=True, timeout_ms=5000):
    """勾选 / 取消“记住我”。

    坑点：该复选框是 Bootstrap 的 custom-control-input，本体被 label 覆盖，
    直接 page.check("#remember-me") 会触发 Playwright 的可点击性检查 →
    反复 scrollIntoViewIfNeeded 重试 → 页面无限上下滚动、卡死在这一步。
    因此：优先点 label（原生行为，change 事件正确派发），失败再退化为 JS 置位。
    全程短超时，绝不阻塞主流程。
    """
    try:
        label = page.locator('label[for="remember-me"]')
        if label.count():
            label.first.click(timeout=timeout_ms)
            return
    except Exception:
        pass
    try:
        page.evaluate(
            """(wanted) => {
                const el = document.getElementById('remember-me');
                if (!el) return false;
                if (el.checked === wanted) return true;
                el.checked = wanted;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return true;
            }""",
            wanted,
        )
    except Exception:
        pass


# Geetest v4 是“嵌入式按钮型”验证码：表单提交后页面上只会渲染出一个
# 「点我开始验证」的按钮，必须点它才会弹出真正的验证框。
# 早先版本只是 click("button.login") 后就轮询等待 —— 没人点这个按钮，
# 弹窗永远不开，必然 180s 超时。故需主动驱动。
GEETEST_PROBE_JS = """() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           s.display !== 'none' && s.visibility !== 'hidden' &&
           parseFloat(s.opacity || '1') > 0.05;
  };
  const q = (sel) => Array.prototype.slice.call(document.querySelectorAll(sel)).filter(vis);
  const tipEl = q('.geetest_tip')[0] || q('.geetest_err_tips')[0];
  const errEl = q('.geetest-error-message')[0];
  const tip = tipEl ? (tipEl.textContent || '').trim() : '';
  // 注意：.geetest_captcha 是“常驻外层容器”（class 上带 geetest_float /
  // geetest_nextReady），它一渲染出来就在，绝不能拿来当“弹窗已打开”的判据 ——
  // 否则脚本会以为弹窗开着、永远不去点开始按钮，最终必然超时。
  return {
    start_btn: q('.geetest_btn_click').length > 0 || q('.geetest_box_btn').length > 0,
    popup: q('.geetest_popup_wrap').length > 0 || q('.geetest_window').length > 0 ||
           q('.geetest_panel').length > 0,
    tip: tip,
    err: errEl ? (errEl.textContent || '').trim() : '',
    success: /验证通过|验证成功|通过验证|success/i.test(tip) || q('.geetest_success').length > 0
  };
}"""


def _geetest_probe(page):
    """探测 Geetest 当前状态；任何异常都返回安全的空状态，绝不中断主流程。"""
    try:
        return page.evaluate(GEETEST_PROBE_JS) or {}
    except Exception:
        return {}


def _click_geetest_start(page):
    """点击「点我开始验证」按钮，打开验证弹窗。

    关键：必须用真实鼠标事件并给出正确视口坐标。
    之前用 JS 派发无坐标 MouseEvent，Geetest 弹窗会出现在 (0,0) 或被浏览器忽略，
    用户看不到验证框，最终超时。
    """
    for sel in (".geetest_btn_click", ".geetest_box_btn"):
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            bb = loc.bounding_box(timeout=5000)
            if not bb or bb.get("width", 0) <= 0 or bb.get("height", 0) <= 0:
                continue
            # 真实指针点击元素中心，Geetest 才能正确弹出验证框
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            return True
        except Exception:
            continue
    # 退化方案：HTMLElement.click() 仍由浏览器派发生成，比裸 MouseEvent 更可信
    try:
        return bool(page.evaluate("""() => {
            const el = document.querySelector('.geetest_btn_click') ||
                       document.querySelector('.geetest_box_btn') ||
                       document.querySelector('.geetest_holder');
            if (!el) return false;
            el.click();
            return true;
        }"""))
    except Exception:
        return False


def playwright_login(cfg):
    """用真实浏览器完成登录（含 Geetest v4），返回 (cookies, cookie_str)。
    失败时抛出 LoginError(code, msg)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise LoginError(EXIT_NO_PLAYWRIGHT,
                         "未安装 Playwright：pip install -r requirements-ikuuu.txt && playwright install chromium")

    log(f"🌐 启动浏览器登录（headless={cfg.headless}，超时 {cfg.login_timeout}s）")
    log(f"   账号：{mask_account(cfg.email)}（密码不会输出到日志）")

    with sync_playwright() as p:
        launch_kw = {
            "headless": cfg.headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if cfg.proxy:
            launch_kw["proxy"] = {"server": cfg.proxy}
        try:
            browser = p.chromium.launch(**launch_kw)
        except Exception as e:
            raise LoginError(EXIT_NO_PLAYWRIGHT, f"浏览器启动失败（chromium 是否已安装？）：{e}")

        ctx = browser.new_context(
            user_agent=cfg.user_agent,
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # 直接抓取 /auth/login 的应答 JSON —— 站点自己的判定才是权威结果，
        # 比观察 DOM 状态可靠（页面可能不跳转、只弹 toast）。
        login_resp = {}

        def _on_response(resp):
            try:
                if "/auth/login" not in resp.url:
                    return
                if resp.request.method != "POST":
                    return
                body = resp.text()
                login_resp["status"] = resp.status
                login_resp["body"] = (body or "")[:800]
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            # 任何单步操作最多等 15s，避免某个元素不可交互时无限重试
            page.set_default_timeout(15000)

            page.goto(f"{cfg.base_url}/auth/login", wait_until="domcontentloaded", timeout=60000)
            # 填邮箱
            page.wait_for_selector("#email", timeout=30000)
            page.fill("#email", cfg.email)

            # 填密码（若当前阶段已显示）
            pwd = page.locator("#password")
            if pwd.count() and pwd.first.is_visible():
                pwd.first.fill(cfg.password)
            else:
                log("   ⚠️ 密码框当前不可见，先提交邮箱以进入下一阶段")
                page.click("button.login")
                page.wait_for_selector("#password", timeout=20000)
                page.fill("#password", cfg.password)

            # 勾选“记住我” → 才能拿到长期 Cookie（而非仅会话级 PHPSESSID）
            if cfg.remember and page.locator("#remember-me").count():
                _toggle_remember(page, True)
                try:
                    checked = page.is_checked("#remember-me")
                except Exception:
                    checked = None
                log(f"   “记住我”：{'✅ 已勾选' if checked else '⚠️ 未确认勾选（可能拿不到长期 Cookie）'}")
            elif cfg.remember:
                log("   ⚠️ 页面未找到 #remember-me，跳过勾选")

            log("   已填入凭据，先打开并完成 Geetest 验证，再提交登录表单")
            # 实测（2026-08-31）：ikuuu 用的是 Geetest V4 的 `captcha_type=ai`
            # 自适应一键验证 —— 点一下按钮就 verify success，不弹滑块/点选，
            # 因此**无头模式同样能过**，无需第三方打码服务。
            # 只有当 IP/指纹被判为高风险时才会降级成 slide，那种情况才需要人工。
            if cfg.headless:
                log("   无头模式：ikuuu 为 ai 型一键验证，点击后通常直接通过")

            deadline = time.time() + cfg.login_timeout
            phase = "timeout"
            geetest_solved = False
            geetest_opened = False
            geetest_clicks = 0
            click_fail_logged = False
            last_tip = None
            login_submitted = False

            while time.time() < deadline:
                url = page.url
                if "/auth/login" not in url:
                    phase = "logged_in"
                    break

                # ① 站点自己的应答才是权威判定（页面可能不跳转、只弹 toast）
                raw = login_resp.pop("body", None) or ""
                if raw:
                    low = raw.lower()
                    ret, msg = None, raw[:150]
                    try:
                        data = json.loads(raw)
                        ret, msg = data.get("ret"), str(data.get("msg", ""))
                    except Exception:
                        pass
                    if ret == 1 or "登录成功" in msg:
                        log("   站点返回：✅ 登录成功")
                        phase = "logged_in"
                        break
                    if "captcha" in low:
                        # 正常中间态：站点在索要人机验证票据；等验证完成后再自动提交
                        pass
                    else:
                        neg = ("错误", "失败", "不正确", "不存在", "invalid", "error", "wrong")
                        if ret in (0, "0") or any(k in low for k in neg):
                            phase = f"rejected:{msg[:120]}"
                            break

                # ② 2FA / 邮箱验证码（id 以数字开头，#2fa-code 不是合法 CSS 选择器）
                twofa = page.locator('[id="2fa-code"]')
                if twofa.count() and twofa.first.is_visible():
                    if not cfg.totp_secret:
                        phase = "need_2fa"
                        break
                    code = totp_code(cfg.totp_secret)
                    log(f"   检测到 2FA 阶段，填入动态口令 {code[:2]}****")
                    twofa.first.fill(code)
                    page.click("button.login")

                mailcode = page.locator('input[name="login-email-code"]')
                if mailcode.count() and mailcode.first.is_visible():
                    phase = "need_email_code"
                    break

                # ③ 先驱动 Geetest 完成 → 再提交登录
                g = _geetest_probe(page)
                if g.get("tip") and g["tip"] != last_tip:
                    log(f"   Geetest：{g['tip']}")
                    last_tip = g["tip"]

                if g.get("success"):
                    if not geetest_solved:
                        geetest_solved = True
                        geetest_opened = True
                        log("   Geetest：✅ 验证通过")
                    if not login_submitted:
                        login_submitted = True
                        log("   验证已完成，提交登录表单…")
                        try:
                            page.click("button.login", timeout=5000)
                        except Exception:
                            pass
                elif geetest_solved:
                    # 已经通过但可能弹窗还没完全消失；继续等提交结果
                    pass
                elif g.get("popup"):
                    # 弹窗已开，正在等待人工/自动完成，不要打扰
                    geetest_opened = True
                elif g.get("start_btn"):
                    # 关键一步：不点这个按钮，弹窗永远不会出现 → 必然超时
                    if _click_geetest_start(page):
                        geetest_clicks += 1
                        geetest_opened = True
                        if geetest_clicks == 1:
                            log("   Geetest：已点击「点我开始验证」，请在浏览器窗口中完成验证…")
                        elif geetest_clicks % 5 == 0:
                            log(f"   Geetest：验证框被关闭，已重新打开（第 {geetest_clicks} 次）")
                    elif not click_fail_logged:
                        # 绝不静默重试：点不开就立刻说清楚，避免又白等 5 分钟
                        click_fail_logged = True
                        log("   ⚠️ 已看到「点我开始验证」按钮，但自动点击失败（元素可能被遮挡）。"
                            "请手动在浏览器窗口里点一下它")

                if g.get("err"):
                    log(f"⚠️ Geetest 提示：{g['err']}")

                page.wait_for_timeout(1000)

            if phase.startswith("rejected"):
                _dump_debug(page, "rejected")
                raise LoginError(EXIT_NEED_LOGIN,
                                 f"站点拒绝了本次登录：{phase.split(':', 1)[-1]}"
                                 f"（请核对 IKUUU_EMAIL / IKUUU_PASSWORD）")
            if phase == "need_2fa":
                _dump_debug(page, "2fa")
                raise LoginError(EXIT_NEED_2FA,
                                 "账号要求 2FA 动态口令，但未配置 IKUUU_TOTP_SECRET（把 TOTP 密钥写进 .env 即可自动填码）")
            if phase == "need_email_code":
                _dump_debug(page, "emailcode")
                raise LoginError(EXIT_NEED_EMAIL_CODE,
                                 "站点要求输入邮件中的 8 位验证码（异地/IP 变化会触发），无法自动获取；"
                                 "建议本地跑一次让该 IP 变可信，或手动登录后再复用 Cookie")
            if phase.startswith("captcha_failed"):
                _dump_debug(page, "captcha")
                raise LoginError(EXIT_CAPTCHA, f"Geetest 验证未通过：{phase.split(':', 1)[-1]}")
            if phase == "timeout":
                _dump_debug(page, "timeout")
                hint = ("从未弹出验证框（脚本未能点开 Geetest）" if not geetest_opened
                        else "验证框已弹出但一直未完成")
                raise LoginError(EXIT_CAPTCHA,
                                 f"登录超时（{cfg.login_timeout}s）：{hint}。"
                                 f"请设 IKUUU_HEADLESS=0 本机可视化跑一次、在浏览器窗口里人工过验证"
                                 f"（可把 IKUUU_LOGIN_TIMEOUT 调大到 300 留足操作时间）")

            # 登录后确认能进用户中心
            page.goto(f"{cfg.base_url}/user", wait_until="domcontentloaded", timeout=60000)
            if "/auth/login" in page.url:
                _dump_debug(page, "notloggedin")
                raise LoginError(EXIT_NEED_LOGIN, "表单已提交但会话未建立（仍未跳转到用户中心）")

            cookies = ctx.cookies()
            pairs = []
            for c in cookies:
                dom = (c.get("domain") or "").lstrip(".")
                if dom and (dom == cfg.host or cfg.host.endswith(dom)):
                    pairs.append(f"{c['name']}={c['value']}")
            if not pairs:
                _dump_debug(page, "nocookie")
                raise LoginError(EXIT_NEED_LOGIN, "登录成功但未取到任何 Cookie")
            return cookies, "; ".join(pairs)
        except LoginError:
            raise
        except Exception as e:
            # 典型场景：用户中途关掉浏览器窗口 / 页面崩溃
            raise LoginError(EXIT_CAPTCHA,
                             f"浏览器流程异常中断：{type(e).__name__}: {str(e)[:200]}")
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass


class LoginError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


# ============================== 子命令 ==============================
def cmd_status(cfg, as_json=False):
    store = load_cookie_store(cfg)
    if not store:
        log("📭 尚无 Cookie 文件，需要先登录")
        return EXIT_NEED_LOGIN
    age = cookie_age_days(store)
    ok, reason = validate_cookie(cfg, store.get("cookie", ""))
    store["validated"] = ok
    store["last_validated_at"] = datetime.now(CST).isoformat()
    with open(cfg.cookie_file, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    info = {
        "account": store.get("account"),
        "obtained_at": store.get("obtained_at"),
        "age_days": round(age, 2) if age is not None else None,
        "valid": ok,
        "reason": reason,
        "source": store.get("source"),
    }
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        log(f"账号：{info['account']} | 获取于 {info['obtained_at']} | 账龄 {info['age_days']} 天")
        log(f"有效性：{'✅ 有效' if ok else '❌ 失效'}（{reason}）")
        if ok and age and age >= cfg.warn_after_days:
            log(f"⚠️ Cookie 已使用 {age:.1f} 天，接近建议刷新阈值 {cfg.warn_after_days} 天")
    return EXIT_OK if ok else EXIT_NEED_LOGIN


def cmd_validate(cfg, as_json=False):
    store = load_cookie_store(cfg)
    if not store:
        log("📭 尚无 Cookie 文件")
        return EXIT_NEED_LOGIN
    ok, reason = validate_cookie(cfg, store.get("cookie", ""))
    log(f"{'✅ Cookie 有效' if ok else '❌ Cookie 失效'}（{reason}）")
    return EXIT_OK if ok else EXIT_NEED_LOGIN


def cmd_login(cfg, as_json=False):
    if not cfg.email or not cfg.password:
        log("❌ 缺少凭据：请在 .env 或环境变量中设置 IKUUU_EMAIL / IKUUU_PASSWORD")
        return EXIT_NO_CREDS
    try:
        cookies, cookie_str = playwright_login(cfg)
    except LoginError as e:
        log(f"❌ 登录失败：{e.msg}")
        return e.code

    ok, reason = validate_cookie(cfg, cookie_str)
    store = save_cookie_store(cfg, cookie_str, cookies, source="playwright-login",
                              validated=ok, note=reason)
    if not ok:
        log(f"❌ 登录拿到了 Cookie 但校验未通过：{reason}")
        return EXIT_NEED_LOGIN
    log(f"✅ 登录并校验成功（{mask_account(cfg.email)}，{len(cookies)} 个 Cookie）")

    if cfg.push_secret:
        ok2, why = push_secret_to_github(cfg, cookie_str)
        if ok2:
            log(f"☁️ 已回写 GitHub Secret `{cfg.secret_name}`"
                + (f"（{cfg.repo}）" if cfg.repo else ""))
        else:
            log(f"⚠️ 回写 Secret 失败：{why}")
            log("   Actions 端仍会沿用旧 Cookie；可手动执行："
                f"gh secret set {cfg.secret_name} --body \"<cookie>\"")

    if as_json:
        print(json.dumps({k: store[k] for k in
                          ("account", "obtained_at", "source", "validated")},
                         ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_refresh(cfg, as_json=False):
    """日常入口（给计划任务用）：只有快过期或已失效时才弹浏览器重新登录。

    判定优先级：
      1) 无 Cookie 文件 → 登录
      2) 已失效（/user 跳登录页）→ 登录
      3) 距 expire_in 不足 refresh_before_days 天 → 登录（避免断档）
      4) 其余 → 什么都不做，直接退出（这样每天跑计划任务也不会骚扰用户）
    """
    store = load_cookie_store(cfg)
    if not store:
        log("📭 无 Cookie 文件，开始首次登录…")
        return cmd_login(cfg, as_json=as_json)

    ok, reason = validate_cookie(cfg, store.get("cookie", ""))
    if not ok:
        log(f"♻️ Cookie 已失效（{reason}），开始重新登录…")
        return cmd_login(cfg, as_json=as_json)

    left = cookie_remaining_days(store)
    age = cookie_age_days(store)
    if left is not None:
        log(f"✅ Cookie 仍有效，剩余 {left:.2f} 天（账龄 {age:.2f} 天）")
        if left <= cfg.refresh_before_days:
            log(f"♻️ 剩余不足 {cfg.refresh_before_days} 天，提前刷新以免断档…")
            return cmd_login(cfg, as_json=as_json)
    else:
        log(f"✅ Cookie 仍有效（账龄 {age:.2f} 天，无过期时间信息）")
        if age is not None and age >= cfg.warn_after_days:
            log(f"♻️ 账龄 {age:.1f} 天 ≥ 阈值 {cfg.warn_after_days} 天，刷新…")
            return cmd_login(cfg, as_json=as_json)

    log("👍 无需刷新")
    return EXIT_OK


def main():
    ap = argparse.ArgumentParser(description="ikuuu 自动登录并获取/校验 Cookie")
    ap.add_argument("command", choices=["status", "validate", "login", "refresh"])
    ap.add_argument("--env", default=".env", help="凭据文件路径（默认 .env）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果（便于 CI 解析）")
    ap.add_argument("--push-secret", action="store_true",
                    help="登录成功后用 gh CLI 把 Cookie 回写到仓库 Secret"
                         "（让 GitHub Actions 端完全无需登录，需已 gh auth login）")
    ap.add_argument("--repo", default="",
                    help="回写 Secret 的目标仓库，格式 owner/repo（默认取当前 git 仓库）")
    args = ap.parse_args()

    cfg = Config.load(args.env)
    # 命令行开关优先级高于 .env
    if args.push_secret:
        cfg.push_secret = True
    if args.repo:
        cfg.repo = args.repo
    log(f"🔧 目标站点：{cfg.base_url} | Cookie 文件：{cfg.cookie_file}")
    if cfg.proxy:
        log(f"🔧 使用代理：{cfg.proxy}")
    if cfg.push_secret:
        log(f"☁️ 成功后会回写 Secret `{cfg.secret_name}`"
            + (f" → {cfg.repo}" if cfg.repo else "（当前仓库）"))

    handlers = {
        "status": cmd_status,
        "validate": cmd_validate,
        "login": cmd_login,
        "refresh": cmd_refresh,
    }
    return handlers[args.command](cfg, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
