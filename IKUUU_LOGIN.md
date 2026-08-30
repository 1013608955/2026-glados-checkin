# ikuuu 自动登录与 Cookie 刷新方案

## 目标

让 `ikuuu.win` 的签到不再依赖手动复制 Cookie，实现：

- **Phase 1（本地）**：用 Playwright 驱动浏览器完成登录，自动把 Cookie 写入 `ikuuu_cookie.json`。
- **Phase 2（GitHub Actions）**：定时检查/刷新 Cookie，并把 Cookie 通过 Actions Cache 共享给 `checkin.yml`。

## 核心限制说明（必读）

ikuuu 的登录页接入了 **Geetest v4 人机验证**。本方案当前使用 Playwright 的真实浏览器流程，验证弹窗需要**真人完成**（在本地有头模式下浏览器窗口会弹出，由用户在窗口里拖动/点选）。

因此：

- ✅ 本地运行 `python ikuuu_login.py login` 可用（有头模式）。
- ⚠️ GitHub-hosted runner（无显示器、无人工值守）**默认无法完成 Geetest**。`.github/workflows/ikuuu-cookie.yml` 已搭好骨架，但要在 GitHub 上完全无人值守运行，还需额外接入：
  - 第三方打码服务（如 Capsolver、2captcha、anti-captcha）识别 Geetest；或
  - 在能看到桌面的人工值守环境/self-hosted runner 上运行。

后文给出了最小改动接入第三方打码服务的扩展点。

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

## Phase 2：GitHub Actions

### 1. Secrets 清单

在仓库 Settings → Secrets and variables → Actions → Repository secrets 里添加：

| Secret | 必填 | 说明 |
|--------|------|------|
| `IKUUU_EMAIL` | 是 | ikuuu 登录邮箱 |
| `IKUUU_PASSWORD` | 是 | ikuuu 登录密码 |
| `IKUUU_TOTP_SECRET` | 否 | 如开启 2FA 则填 TOTP 密钥 |
| `IKUUU_PROXY` | 否 | Actions 出口访问 ikuuu 时所需的代理 |
| `IKUUU_BASE_URL` | 否 | 默认 `https://ikuuu.win`，备用域名可改 |

### 2. 工作流设计

`.github/workflows/ikuuu-cookie.yml`：

- **定时频率**：默认 `0 */6 * * *`（每 6 小时一次），修改 YAML 中的 cron 即可调整。
- **执行逻辑**：
  1. 从 Actions Cache 恢复之前的 `ikuuu_cookie.json`。
  2. 安装 Playwright + Chromium。
  3. 运行 `python ikuuu_login.py login`：如果当前 Cookie 仍有效，脚本内部校验通过后会直接复用；如果失效或接近阈值，则重新登录。
  4. 运行 `python ikuuu_login.py validate` 二次校验。
  5. 把新的 `ikuuu_cookie.json` 写回 Actions Cache，供 `checkin.yml` 使用。
- **失败处理**：
  - 工作流失败时 GitHub 默认会给仓库管理员发邮件。
  - 失败的截图和 HTML 会作为 artifact 保留 7 天。
  - 手动触发支持 `force_login` 选项，勾选后先删除旧 Cookie 再强制重新登录。

`.github/workflows/checkin.yml` 已增加一步：在签到前从同一个 Cache key（`ikuuu-cookie-v1-*`）恢复 `ikuuu_cookie.json`，`checkin.py` 自动消费。

### 3. 完全无人值守的扩展点

要在 GitHub-hosted runner 上完全自动过 Geetest，需要把 `_click_geetest_start()` 与等待人工完成的部分替换为第三方识别。推荐扩展方式：

1. 在 `ikuuu_login.py` 中新增 `IKUUU_CAPTCHA_SOLVER` 环境变量（如 `capsolver`）。
2. 增加一个 solver 抽象：
   - 从页面读取 Geetest 的 `captcha_id`、`lot_number`、`gt` 等参数；
   - 提交给 Capsolver/2captcha；
   - 拿到 `captcha_output`、`pass_token`、`gen_time` 后，调用 Geetest 的 `captchaObj.onSuccess()` 或直接回填隐藏字段；
   - 触发登录提交。
3. 把 solver API key 也放进 Secrets。

> 本方案当前**没有内置自动识别**，是因为 Geetest v4 的形态随站点版本变化，识别逻辑需要接入付费服务并持续维护。文件已预留 `Config` 类扩展，方便后续追加。

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

- 新增 `ikuuu_login.py`：Playwright 登录、Cookie 落盘、TOTP、脱敏日志、失败留证。
- 新增 `requirements-ikuuu.txt` 与 `ikuuu.env.example`。
- 更新 `checkin.py`：未设置 `IKUUU_COOKIE` 时自动读取 `ikuuu_cookie.json`。
- 新增 `.github/workflows/ikuuu-cookie.yml`：定时刷新 Cookie 并缓存。
- 更新 `.github/workflows/checkin.yml`：恢复 Cookie 缓存。
- `.gitignore` 已排除 `.env`、`ikuuu_cookie.json`、`ikuuu_debug_*`。
