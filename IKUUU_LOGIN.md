# ikuuu 自动登录与 Cookie 刷新方案

## 目标

让 `ikuuu.win` 的签到不再依赖手动复制 Cookie，实现：

- **Phase 1（本地）**：用 Playwright 驱动浏览器完成登录，自动把 Cookie 写入 `ikuuu_cookie.json`。
- **Phase 2（GitHub Actions）**：定时检查/刷新 Cookie，并把 Cookie 通过 Actions Cache 共享给 `checkin.yml`。

## 好消息：零成本 + 全网无人值守（2026-08-31 实测）

ikuuu 登录页确实用了 **Geetest V4**，但它选的是 **`captcha_type=ai`（自适应一键验证）**：

> 真实浏览器点一下「点我开始验证」就直接 `verify success`，**不会弹滑块、不会要点选**。

所以**无头浏览器同样能过**，整个链路 8 秒完成，**不需要任何第三方打码服务**。

实测记录（无头模式）：

```
[03:19:24] 已填入凭据，先打开并完成 Geetest 验证，再提交登录表单
[03:19:25] Geetest：已点击「点我开始验证」
[03:19:26] Geetest：✅ 验证通过          ← 1 秒后直接通过
[03:19:28] ✅ 登录并校验成功（9 个 Cookie）
```

唯一的限制是 **Cookie 固定 7 天、不滑动续期**（`GET /user` 不返回 `Set-Cookie`），
所以每 7 天必须重登一次 —— 但这件事 Actions 会自动做，你什么都不用管。

| 环节 | 频率 | 在哪跑 | 需要人吗 |
|------|------|--------|----------|
| 签到 | 每天 2 次 | GitHub Actions | ❌ |
| 刷新 Cookie | 每 3 天 | GitHub Actions（无头） | ❌ |

> ⚠️ 兜底：只有当 IP / 浏览器指纹被 Geetest 判为高风险时，`ai` 才会降级成 `slide`（滑块），
> 那时无头会超时失败。届时的降级方案见文末「兜底方案」。

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

## Phase 2：GitHub Actions（全自动，无需打码服务）

### 1. Secrets 清单

在 `Settings → Secrets and variables → Actions → Repository secrets` 里添加：

| Secret | 必填 | 说明 |
|--------|------|------|
| `IKUUU_EMAIL` | ✅ | 登录邮箱 |
| `IKUUU_PASSWORD` | ✅ | 登录密码 |
| `IKUUU_TOTP_SECRET` | 否 | 账号开了 2FA 才需要 |
| `IKUUU_PROXY` | 否 | runner 出口访问 ikuuu 需要代理时才填 |
| `IKUUU_BASE_URL` | 否 | 默认 `https://ikuuu.win` |
| `IKUUU_COOKIE` | 否 | **仅作兜底**，正常情况下用不到（见「Cookie 传递」） |

### 2. 工作流职责拆分

| 工作流 | 频率 | 做什么 | 需要 Chromium |
|--------|------|--------|----------------|
| `ikuuu-cookie.yml` | 每 3 天 | 无头登录刷新 Cookie → 写入 Actions Cache | ✅ |
| `checkin.yml` | 每天 2 次 | 四平台签到，从 Cache 取 Cookie | ❌ |

`ikuuu-cookie.yml` 流程：

1. 恢复上一次的 `ikuuu_cookie.json`（Actions Cache）。
2. 安装 Playwright + Chromium。
3. `python ikuuu_login.py refresh` —— **智能判断**：剩余 > 2.5 天就直接退出，不消耗验证。
4. `python ikuuu_login.py validate` 二次校验。
5. 把 Cookie 文件写回 Cache（key 带 `run_id`，每次都会存新条目）。

失败时：GitHub 会自动给仓库管理员发邮件，并保留截图和 HTML 作为 artifact（7 天）。

### 3. Cookie 如何在两个 workflow 之间传递

用 **Actions Cache**，key 前缀统一为 `ikuuu-cookie-v1-`：

```yaml
key: ikuuu-cookie-v1-${{ github.run_id }}-${{ github.run_attempt }}
restore-keys: |
  ikuuu-cookie-v1-
```

- `ikuuu-cookie.yml` 每次写入一条新 Cache（key 唯一，必然保存）。
- `checkin.yml` 用前缀 restore，总能拿到最近一次刷新的 Cookie。
- 每 3 天刷新一次，远低于 Cache 的 7 天回收阈值，不会失效。

**为什么不用 Secret 传递 Cookie？** 因为 `GITHUB_TOKEN` 无法写 Secret，
必须额外配置一个有 Secrets 写权限的 PAT，多一个依赖。Cache 方案零配置。

`checkin.py` 的读取优先级是 **`ikuuu_cookie.json` > `IKUUU_COOKIE`**：
文件由 workflow 维护永远最新，而 Secret 里的往往是手动填的旧快照，
若优先用 Secret，会拿过期 Cookie 覆盖掉刚刷新的，导致签到失败。

### 4. 兜底方案（Geetest 降级成滑块时）

如果哪天 Actions 的无头环境被 Geetest 判为高风险，`captcha_type` 会从 `ai`
降级成 `slide`，点击后弹出滑块、无人能完成 → 工作流超时失败。届时按优先级选：

**① 本机刷新 + 回写 Secret（零成本）**

```bash
python ikuuu_login.py refresh --push-secret
```

需要给 PAT 开 Secrets 写权限，否则会报：

```
failed to fetch public key: HTTP 403: Resource not accessible by personal access token
```

开启：<https://github.com/settings/personal-access-tokens> → 选那个
**Repository access = all repositories** 的 token（本仓库是 `openclaw` 那个）→
**Repository permissions** → **Secrets** → **Read and write** → 保存 → `gh auth refresh`。

> 注意：权限列表里叫 **Secret scanning** 的那几项是「秘密扫描告警」，不是我们要的
> **Secrets**（管理仓库 Actions Secrets），别选错。

验证：`gh secret list --repo 1013608955/2026-glados-checkin`

可写进 `.env` 省得每次敲参数：

```ini
IKUUU_PUSH_SECRET=1
IKUUU_REPO=1013608955/2026-glados-checkin
```

**② 接入打码服务（约 $0.05/月）**

Capsolver 的 Geetest v4 = **$1.2/1000 次**（最低充值 $6）。登录协议已完整逆向
（见上文），实现时**不需要浏览器**：

1. `GET /auth/login` → base64 解页面 → 正则提 `captchaId`
2. 调 Capsolver `GeeTestTaskProxyless`：`{websiteURL, captchaId}`
3. 拿 `lot_number` / `captcha_output` / `pass_token` / `gen_time`
4. 按上文格式 `POST /auth/login`（纯 `requests`）
5. 写 `ikuuu_cookie.json`

**③ 开源纯 Python solver**

[`xKiian/GeekedTest`](https://github.com/xKiian/GeekedTest) 是纯 Python 实现的
Geetest v4 solver，支持 `slide` / `icon` / `gobang` / `ai`，不需要浏览器。
风险是 Geetest 一改版常量就失效，需要跟着维护。

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
- **`checkin.py` 读取优先级改为 `ikuuu_cookie.json` > `IKUUU_COOKIE`**：
  文件由 workflow 维护永远最新，避免过期 Secret 覆盖刚刷新的 Cookie。
- `.github/workflows/ikuuu-cookie.yml`：每 3 天**无头登录**刷新 Cookie 并写入
  Actions Cache。实测 ikuuu 用 `captcha_type=ai` 一键验证，无头 8 秒即可通过，
  **不需要第三方打码服务**。失败时 GitHub 自动发邮件 + 保留截图/HTML。
- `.github/workflows/checkin.yml`：从 Cache 恢复 Cookie 用于签到。
- `.gitignore` 已排除 `.env`、`ikuuu_cookie.json`、`ikuuu_debug_*`。
