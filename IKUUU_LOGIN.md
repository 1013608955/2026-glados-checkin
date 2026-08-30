# ikuuu 自动登录与 Cookie 刷新方案

## 目标

让 `ikuuu.win` 的签到不再依赖手动复制 Cookie，实现：

- **Phase 1（本地）**：用 Playwright 驱动浏览器完成登录，自动把 Cookie 写入 `ikuuu_cookie.json`。
- **Phase 2（GitHub Actions）**：定时检查/刷新 Cookie，并把 Cookie 通过 Actions Cache 共享给 `checkin.yml`。

## 核心限制说明（必读）

ikuuu 的登录页接入了 **Geetest v4 人机验证**，且 Cookie 是**固定 7 天有效期、不滑动续期**
（实测 `GET /user` 不返回 `Set-Cookie`，`expire_in` 恒定 7 天）。这两点决定了架构：

> **Geetest 的设计目的就是区分人和机器** —— 所以「零成本」和「100% 无人值守」不可兼得。

因此本方案采用**职责分离**：

| 环节 | 在哪里跑 | 是否需要人 |
|------|----------|-----------|
| 签到（每天 2 次） | GitHub Actions | ❌ 全自动 |
| 刷新 Cookie（每 ~6 天） | 本机计划任务 | ✅ 点一下验证（约 10 秒） |

- ✅ `python ikuuu_login.py refresh`：Cookie 还够用就直接退出，不够才弹浏览器刷新。
- ✅ GitHub Actions 端只做签到、从不碰登录，因此**完全无人值守，也不会因验证码超时而失败**。
- ⚠️ 若追求连刷新都无人值守，需接入第三方打码服务（见文末扩展点）。

## 登录协议（已逆向，供扩展用）

无需浏览器即可登录 —— 页面里写死了 `captchaId = cc96d05ba8b60f9112f76e18526fcb73`，
登录请求是 jQuery form 编码的 `POST /auth/login`：

```
host=ikuuu.win
phase=password
captcha_result[lot_number]=…     ← Geetest v4 四件套，来自打码服务
captcha_result[captcha_output]=…
captcha_result[pass_token]=…
captcha_result[gen_time]=…
email=…
passwd=…
remember_me=on
pageLoadedAt=<页面加载毫秒时间戳>
```

只要拿到那四个 Geetest 参数，纯 `requests` 就能完成登录，连 Chromium 都不用装。
这也是后续接入 Capsolver 等服务时的实现路径。

## 项目文件结构

```
2026-glados-checkin/
├── .github/workflows/
│   ├── checkin.yml          # 多平台签到工作流（已接入 ikuuu_cookie.json 缓存）
│   ├── keep-alive.yml       # 保活
│   └── ikuuu-cookie.yml     # ikuuu Cookie 定时刷新工作流
├── checkin.py               # 签到主脚本（已支持读取 ikuuu_cookie.json）
├── ikuuu_login.py           # ikuuu 自动登录脚本（本方案核心）
├── requirements-ikuuu.txt   # Playwright 依赖（与主流程分开，按需安装）
├── ikuuu.env.example        # .env 配置模板
├── ikuuu_cookie.json        # 生成的 Cookie 文件（已被 .gitignore 排除）
├── .env                     # 本地凭据（已被 .gitignore 排除）
└── IKUUU_LOGIN.md           # 本文档
```

## Phase 1：本地运行

### 1. 配置凭据

复制模板并填入账号密码：

```bash
cp ikuuu.env.example .env
```

`.env` 内容示例（只填前两项即可）：

```ini
IKUUU_EMAIL=your_email@example.com
IKUUU_PASSWORD=your_password
# IKUUU_TOTP_SECRET=          # 如开启 2FA 再填
# IKUUU_PROXY=http://127.0.0.1:7890
IKUUU_HEADLESS=0               # 本地必须 0，否则看不到验证窗口
IKUUU_LOGIN_TIMEOUT=300
IKUUU_COOKIE_FILE=ikuuu_cookie.json
```

`.env` 已被 `.gitignore` 排除，不会进入 git。

### 2. 安装依赖

```bash
pip install -r requirements-ikuuu.txt
python -m playwright install chromium
```

> 已安装 Chromium 的情况下，脚本会在启动时自动检测；如缺失，脚本会提示。

### 3. 运行登录

```bash
# 登录并写入 ikuuu_cookie.json
python ikuuu_login.py login

# 只校验现有 Cookie 是否仍有效
python ikuuu_login.py validate

# 查看 Cookie 账龄和状态
python ikuuu_login.py status
```

运行 `login` 时浏览器窗口会弹出：脚本自动填好账号密码并点击「点我开始验证」，**用户在弹出的验证框里完成滑块/点选后**，脚本自动提交登录并把 Cookie 落盘。

### 4. Cookie 文件格式

`ikuuu_cookie.json` 示例（部分字段）：

```json
{
  "cookie": "PHPSESSID=...; uid=...; email=...; key=...; ip=...; expire_in=...",
  "obtained_at": "2026-08-31T02:03:35.652937+08:00",
  "obtained_at_ts": 1788113015,
  "source": "playwright-login",
  "account": "10***@qq.com",
  "validated": true,
  "last_validated_at": "...",
  "expire_in_ts": 1788717813,
  "cookies": { /* 各 cookie 的 expires/httpOnly/secure 明细 */ }
}
```

`checkin.py` 会优先读 `IKUUU_COOKIE` 环境变量；环境变量为空时自动读取该文件。

## Phase 2：GitHub Actions（只签到，不登录）

### 1. Secrets 清单

Actions 端只需要一个 Secret：

| Secret | 必填 | 说明 |
|--------|------|------|
| `IKUUU_COOKIE` | 是 | 本机刷新后回写的 Cookie 串 |
| `IKUUU_BASE_URL` | 否 | 默认 `https://ikuuu.win`，备用域名可改 |

> 登录凭据（`IKUUU_EMAIL` / `IKUUU_PASSWORD`）**只放在本机 `.env`**，不进 GitHub ——
> Actions 从不登录，也就没有泄露面。

### 2. 工作流职责拆分

| 工作流 | 频率 | 做什么 | 装 Playwright 吗 |
|--------|------|--------|------------------|
| `checkin.yml` | 每天 2 次 | 四平台签到，直接消费 `IKUUU_COOKIE` | ❌ |
| `ikuuu-cookie.yml` | 每天 1 次 | **健康巡检**：校验 Cookie、算剩余天数 | ❌ |

`ikuuu-cookie.yml` 的巡检逻辑：

- Cookie 失效（`GET /user` 跳登录页）→ `::error::` + 退出码 1 → **工作流失败，GitHub 自动给你发邮件**。
- 剩余 ≤ 1.5 天 → `::warning::`，提醒你该刷新了。
- 剩余 ≤ 3 天 → `::notice::`，温和提示。
- 只需要 `requests`，几秒钟跑完。

### 3. 零成本闭环：本机刷新 + 自动回写 Secret

这是不用打码平台时**人工成本最低**的做法：每 ~6 天你点一次验证（约 10 秒），其余全自动。

#### 3.1 前置条件：给 PAT 开 Secrets 写权限

`gh secret set` 需要 token 具备 Secrets 写权限。你的 `gh` 已登录，但默认的
fine-grained PAT 通常没开，会报：

```
failed to fetch public key: HTTP 403: Resource not accessible by personal access token
```

开启步骤：

1. 打开 <https://github.com/settings/personal-access-tokens>
2. 找到 `gh` 正在用的那个 fine-grained token（`gh auth status` 可看账号）
3. **Repository permissions** → **Secrets** → 改成 **Read and write**
4. 保存后重新登录一次：`gh auth login`（或 `gh auth refresh`）

验证：

```bash
gh secret list --repo 1013608955/2026-glados-checkin
```

能看到 secret 列表即表示权限 OK。

#### 3.2 日常刷新命令

```bash
# Cookie 还够用 → 直接退出，不打扰
# 快过期/已失效  → 弹浏览器，你点一下验证，然后自动回写 Secret
python ikuuu_login.py refresh --push-secret
```

不想每次都敲参数，可以把开关写进 `.env`：

```ini
IKUUU_PUSH_SECRET=1
IKUUU_REPO=1013608955/2026-glados-checkin
IKUUU_REFRESH_BEFORE_DAYS=1.5   # 剩余不足 1.5 天才刷新
```

#### 3.3 挂到 Windows 计划任务

每天跑一次即可（脚本自己会判断要不要刷新，不刷新时 2 秒退出）。

用管理员权限的 PowerShell 注册：

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
           -Argument "ikuuu_login.py refresh --push-secret" `
           -WorkingDirectory "C:\Users\Admin\.openclaw\workspace\2026-glados-checkin"
$trigger = New-ScheduledTaskTrigger -Daily -At 10:00
Register-ScheduledTask -TaskName "ikuuu Cookie 刷新" -Action $action -Trigger $trigger -Description "ikuuu Cookie 到期前自动刷新并回写 GitHub Secret"
```

> 如果想彻底静默（不弹浏览器窗口到前台），可以配合 `-WindowStyle Hidden` 的
> wscript 包装，参考你已有的「游戏更新检查」计划任务做法。
> 但注意：**需要你点验证的那天必须能看到窗口**，所以建议保留可见窗口。

### 4. 追求 100% 无人值守：接入打码服务（可选）

若连「每 6 天点一次」都想省掉，接入 Capsolver（Geetest v4 = **$1.2/1000 次**，
按每月 30-60 次算约 **$0.05/月**，最低充值 $6）。

好消息是登录协议已经完整逆向（见上文），所以**不需要浏览器**，实现路径很短：

1. `GET /auth/login` → 从 base64 页面里正则提取 `captchaId`。
2. 调 Capsolver `GeeTestTaskProxyless`：`{websiteURL, captchaId}`。
3. 拿到 `lot_number` / `captcha_output` / `pass_token` / `gen_time`。
4. 按上文格式 `POST /auth/login`（纯 `requests`，无需 Playwright）。
5. 成功后写 `ikuuu_cookie.json` 并可复用 `--push-secret` 回写。

接入后 `ikuuu-cookie.yml` 就能真正每 6 小时自动刷新，且不需要装 Chromium。

> 当前**没有内置**自动识别：一是需要付费 API Key，二是 Geetest 形态会随站点版本
> 变化、需要长期维护。`Config` 类已预留扩展位，后续追加很容易。

## 本地调试常见问题

### 1. 页面弹出「请验证身份」但没有验证框

**原因**：先点了登录按钮，站点发现没验证码票据，弹出 modal 挡住；同时 JS 无坐标点击让 Geetest 弹窗出现在错误位置。  
**已修复**：当前版本改为「先完成 Geetest → 再提交登录」，并使用真实鼠标坐标点击验证按钮。

### 2. 验证按钮一直点不开，报「点击失败」

可能原因：

- 元素被 modal/遮罩层挡住 — 关闭弹窗后再运行。
- 当前为无头模式 — 本地请确认 `.env` 中 `IKUUU_HEADLESS=0`。
- 页面 Geetest 脚本未加载完成 — 可适当调大 `IKUUU_LOGIN_TIMEOUT`。

### 3. Cookie 拿到了但签到仍提示过期

运行：

```bash
python ikuuu_login.py validate
python checkin.py
```

如果 `validate` 返回失效，可能是 IP 环境变化导致服务端清 Session，需要重新登录。

## 变更摘要

- 新增 `ikuuu_login.py`：Playwright 登录、Cookie 落盘、TOTP、脱敏日志、失败留证、
  `refresh` 智能刷新（按剩余天数决策）、`--push-secret` 自动回写 GitHub Secret。
- 新增 `requirements-ikuuu.txt` 与 `ikuuu.env.example`。
- 更新 `checkin.py`：未设置 `IKUUU_COOKIE` 时自动读取 `ikuuu_cookie.json`。
- `.github/workflows/ikuuu-cookie.yml`：改为**只读健康巡检**（不再尝试登录，
  因为 Geetest 在 runner 上过不了，硬跑只会超时失败）。
- `.github/workflows/checkin.yml`：直接用 `IKUUU_COOKIE` Secret 签到，移除 Cache 步骤。
- `.gitignore` 已排除 `.env`、`ikuuu_cookie.json`、`ikuuu_debug_*`。
