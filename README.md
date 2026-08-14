# 2026 多平台自动签到 (GLaDOS + ikuuu + 42w.shop + SMAI.AI)

本项目是一个自动签到脚本，支持并发签到多个平台，并通过 GitHub Actions 每日定时运行。

## 平台支持

| 平台 | 状态 | 特性 |
|------|------|------|
| GLaDOS | ✅ 完全支持 | 网络超级连接、主机测试、分数签到 |
| ikuuu | ✅ 完全支持 | VPN 服务器签到、会员管理（仅 Cookie 模式，绕过验证码） |
| 42w.shop | ✅ 完全支持 | New API 面板；`session` Cookie + `New-Api-User` 请求头；走代理过 Cloudflare |
| SMAI.AI | ⚠️ 暂不可用 | 签到接口现强制 Cloudflare Turnstile 人机验证，纯 API 无法自动完成（auth 续期代码已就绪，待 Playwright 方案） |

> SMAI.AI 当前在 GitHub Actions / 本地脚本里处于「未配置即跳过」状态：`SMAI_SESSION` 留空则不跑，不影响其它平台。鉴权链路（refresh → Bearer）已修复并验证可用，只差通过 Turnstile 这一关。

## 特性功能

- ✅ **多账号支持**：一次性签到多个账号
- ✅ **按账号级别签到**：成功了下午就不再签到该账号
- ✅ **Token 过期自动检测**：检测平台会话或 Cookie 过期
- ✅ **GLaDOS 多域名容错**：3 个域名自动轮询（cloud/rocks/network）
- ✅ **ikuuu 双域名备用**：主域名 .win + 备用 .fyi，自动切换
- ✅ **42w.shop 代理支持**：内置 mihomo 单节点代理，绕过 Cloudflare WAF
- ✅ **实时日志**：使用北京时间，精确到分钟的签到情况
- ✅ **微信推送**：支持微信推送，让你实时知道签到成功

## 快速使用

### 环境变量配置

#### GLaDOS
```bash
GLADOS_COOKIE="koa:sess=your_cookie_here"
# 多账号用 & 分隔
GLADOS_COOKIE="koa:sess=cookie1...&koa:sess=cookie2..."
```

#### ikuuu（推荐 Cookie 模式）

**⚠️ 重要：ikuuu 最新域名为 https://ikuuu.win（.fyi 作为备用）**

**ikuuu 已启用登录验证码，账号密码模式会失败，请使用 Cookie 模式。**

**如何获取 Cookie（方案一：浏览器开发者工具）**

1. 用 Chrome/Edge 登录 ikuuu：**https://ikuuu.win**（如无法访问则尝试备用域名 .fyi）
2. 按 `F12` 打开开发者工具
3. 切到 **Network**（网络）标签页
4. 刷新页面（F5），在请求列表里点第一个请求（通常是 `ikuuu.fyi` 或 `user`）
5. 右侧看 **Headers**（请求头），滚动找到 `Cookie:` 那一行
6. 复制 `Cookie:` 后面直到行末的整个字符串

你需要保留以下字段（缺一不可）：
```
uid=xxxxx; email=你的邮箱; key=你的 key; ip=你的 ip; expire_in=时间戳
```

**⚠️ 缺少任何字段都可能被 Cloudflare 拦截，返回 HTML 而不是 JSON。**

```bash
# Cookie 模式（推荐）
IKUUU_COOKIE="uid=5012965; email=1013608955%40qq.com; key=049dd4...; ip=40f36d...; expire_in=1775922637"

# 多账号用 & 分隔（每个账号的完整 Cookie 用 & 连接）
IKUUU_COOKIE="uid=1; email=a@qq.com; key=aaa; ip=bbb; expire_in=111&uid=2; email=b@qq.com; key=ccc; ip=ddd; expire_in=222"
```

> **备用方案**：账号密码模式（`IKUUU_ACCOUNTS="email:password"`）仍然可用，但遇到验证码时会失败。

#### 42w.shop（New API 面板）

42w.shop 在 Cloudflare 后面，GitHub Actions 的机房 IP 直连会被 403，因此必须走代理。有两种配置方式，任选其一：

**方式 A：直接给代理地址（W42_PROXY）**

| Secret | 说明 |
|--------|------|
| `W42_COOKIE`（必填） | 登录 `https://api.42w.shop` 后的完整 Cookie 字符串。本机 Chrome 带调试端口启动后运行 `python cdp_get_cookies.py`，输出写入 `cookie_live.txt`，整行复制过来。**不要换行、不要加引号。** |
| `W42_UID`（可选） | 数字 uid，如 `20147`。留空则脚本自动从 Cookie 识别。 |
| `W42_PROXY`（必填） | 一个 GitHub 能直连、且 Cloudflare 信任的出口代理。格式任选：`http://用户:密码@主机:端口`、`https://...`、`socks5://...`、`socks5h://...`（推荐 socks5h，域名也走代理）。 |

**方式 B：给 Clash 订阅链接（W42_SUB，更省心）**

| Secret | 说明 |
|--------|------|
| `W42_COOKIE`（必填） | 同上。 |
| `W42_UID`（可选） | 同上。 |
| `W42_SUB`（必填） | 你的 Clash 订阅链接整串（如 `https://xxx.com/subscribe?token=...`）。工作流会用 mihomo 拉取订阅、自动抽取**单个节点**、起本地代理给 42w 用。 |
| `W42_SUB_NODE`（可选） | 从订阅里「钉选」某个固定节点名。关键：填你本地 Clash 当初抓 Cookie 时用的那个节点名（出口 IP 一致，`cf_clearance` 才不会被 Cloudflare 判重挑战）。不填则 mihomo 用默认节点「新加坡高速 05\| CTCM」。首次运行日志会打印节点清单，照着挑一个填到这里即可。 |

```bash
# 多账号：一行一个 Cookie（或用 & 分隔），W42_UID 同序对应，可留空自动识别
W42_COOKIE="session=...; sidebar_state=true; server_name_session=...; cf_clearance=..."
W42_UID="20147"
W42_SUB="https://your-clash-subscribe-link"
W42_SUB_NODE="新加坡高速 05| CTCM"
```

> **维护提示**：核心 `session` 有效期约 26 天，`cf_clearance` 与抓 Cookie 时的出口 IP 绑定。建议每 ~20 天重新登录 `https://api.42w.shop`、重跑 `python cdp_get_cookies.py` 并更新 `W42_COOKIE`。若换了 Clash 节点导致 `cf_clearance` 失效，需回到抓 Cookie 时同一节点出口。

#### SMAI

| Secret | 说明 |
|--------|------|
| `SMAI_SESSION`（可选） | 浏览器登录 `api.smai.ai` 后的 `session` Cookie 值。留空则跳过 SMAI。 |
| `SMAI_USER_ID`（可选） | 数字 uid，如 `1207`。 |
| `SMAI_REFRESH`（可选，推荐） | 长期 cookie `new_api_refresh` 的值。签到前脚本会用它向 `POST /api/user/auth/refresh` 续期拿到 Bearer token。该 token 会轮换（旧 token 失效），建议同时配置 `REPO_PAT` 让脚本自动回写，否则约每 ~25 天重抓一次。 |

```bash
# 一个 SMAI 账号
SMAI_SESSION="session_abc123..."
SMAI_USER_ID="1207"
SMAI_REFRESH="264e5d07-..."
```

> **当前状态**：SMAI 签到接口已强制 Cloudflare Turnstile 应用层验证，纯 API 无法生成验证 token，故暂不能自动签到。相关 Secret 全部留空即可干净跳过，不影响其它平台。

### 例子配置

```bash
# 一个 GLaDOS 账号
GLADOS_COOKIE="koa:sess=abc123..."

# 两个 ikuuu 账号（Cookie 模式）
IKUUU_COOKIE="uid=123; email=a@qq.com; key=aaa; ip=bbb; expire_in=111&uid=456; email=b@qq.com; key=ccc; ip=ddd; expire_in=222"

# 一个 42w 账号（订阅方式）
W42_COOKIE="session=...; sidebar_state=true; server_name_session=...; cf_clearance=..."
W42_UID="20147"
W42_SUB="https://your-clash-subscribe-link"

# SMAI 暂未配置（留空跳过）
```

### GitHub Actions 部署

推荐在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中配置上述环境变量为 Secrets。

## 签到结果示例

```
### 🌐 42w.shop 签到结果
• mukui: 今日已签到 (今日 +606955，累计 3215565，共 5 天)

### 📶 ikuuu 签到结果
• cookie_1: 似乎已经签到过了哦...

### ✅ GLaDOS 签到结果
• ...
```

## 故障排除

### 平台访问异常
如果出现连接问题，脚本会自动尝试其他可用的 GLaDOS 域名。

### ikuuu 签到失败
- 确认 Cookie 是否包含 `uid`、`email`、`key`、`ip`、`expire_in` 五个字段
- 如果返回"系统无法接受您的验证结果"，说明 Cookie 不完整或已过期
- Cookie 过期后需重新从浏览器抓取并更新 Secret
- `expire_in` 时间戳表示 Cookie 过期时间，过期前需要手动更新

### 42w.shop 签到失败
- 日志出现 `403` / `cf-ray=...` / 非 JSON（HTML）响应：说明出口 IP 被 Cloudflare 拦截。
  - 若用 `W42_PROXY`：确认该代理 GitHub 能直连、且出口 IP 被 Cloudflare 信任；出口 IP 最好与抓 Cookie 时一致。
  - 若用 `W42_SUB`：确认 `W42_SUB_NODE` 钉到了抓 Cookie 时用的那个节点（出口 IP 一致），否则 `cf_clearance` 会被判重挑战。
- 日志出现 `Unauthorized` / `Cookie 可能过期`：说明 `W42_COOKIE` 里装的是旧/无效 Cookie，重跑 `python cdp_get_cookies.py` 刷新后更新 Secret。
- `session` 有效期约 26 天，建议每 ~20 天刷新一次。

### SMAI 签到失败 / 跳过
- 当前 SMAI 强制 Turnstile 验证，脚本无法自动签到。`SMAI_*` Secret 留空即干净跳过。
- 若 `SMAI_REFRESH` 报错 `AUTH_SESSION_REVOKED`：该 refresh token 已被轮换吊销，需重新登录 `api.smai.ai` 并用 `python smai_capture.py` 重抓。

### Token 过期
当检测到账号可能过期时，会推送警告。

## 技术特性

- 使用北京时间，避免时区差异
- 支持多个 GLaDOS 域名自动容错
- 签到成功后会记录状态，避免重复签到
- 支持微信推送，让你实时知道签到成功
- ikuuu 支持 Cookie 模式，绕过登录验证码
- ikuuu 域名已切换：主域名 .win + 备用 .fyi（.nl 已挂）
- 42w.shop 支持 mihomo 单节点代理，绕过 Cloudflare WAF
- CDP 客户端抽为公共 `_ws.py` 模块，避免重复实现

## 更新历史

- **2026-08-15**: 接入 42w.shop 自动签到（CDP 抓 Cookie + mihomo 单节点代理过 Cloudflare）；修复 SMAI 401（refresh → Bearer 续期）；抽离公共 `_ws.py` CDP 模块；清理废弃诊断脚本
- **2026-04-14**: 移除 VOAPI 模块 + 修复北京时间计算错误（使用 UTC→UTC+8 timezone 转换）
- 2026-04-05: ikuuu 改为 Cookie 模式，绕过登录验证码
- 2026-03-26: （曾合并 VOAPI 签到功能，后于 2026-04-14 移除）
- 2026-03-21: 修复 SMAI 额度单位转换问题，使显示的额度与网站一致

---

推荐配合使用 GitHub Actions 实现完全自动化签到。
