# 2026 多平台自动签到 (GLaDOS + ikuuu + SMAI.AI)

自动签到脚本，支持多平台、多账号，通过 GitHub Actions 每日定时运行，结果推送到微信。

## 平台支持

| 平台 | 状态 | 特性 |
|------|------|------|
| GLaDOS | ✅ 支持 | 多域名容错（cloud / rocks / network）、分数签到 |
| ikuuu | ✅ 支持（Cookie 模式） | **域名：https://ikuuu.top**；登录侧有 Geetest，仅建议用 Cookie |
| SMAI.AI | ⚠️ 暂不可用 | 签到接口强制 Cloudflare Turnstile，纯 API 无法完成（留空即跳过） |

## 特性

- 多账号支持，按账号粒度记录「上午已签 → 下午跳过」
- Token 过期检测 + 微信推送告警
- ikuuu 域名自动发现：换域名时从旧域名公告页解析新域名并跟进
- GitHub Actions 定时（北京时间 9:30 / 19:30），支持手动触发
- 推送正文自动降级为非 BMP 字符（WPush 限制）

## 环境变量

### GLaDOS

```bash
GLADOS_COOKIE="koa:sess=your_cookie_here"
# 多账号用 & 分隔
GLADOS_COOKIE="koa:sess=cookie1...&koa:sess=cookie2..."
```

### ikuuu（Cookie 模式，推荐）

站点：**https://ikuuu.top**（旧域名 .win/.fyi/.cc/.me 已于 2026-09-11 全部退役）

获取 Cookie：

1. 浏览器登录 https://ikuuu.top
2. F12 → Network → 刷新页面 → 点第一个请求
3. 复制请求头 `Cookie:` 后的整串

需要保留的字段（缺一不可）：

```
uid=xxxxx; email=你的邮箱; key=你的 key; ip=你的 ip; expire_in=时间戳
```

```bash
IKUUU_COOKIE="uid=5012965; email=xxx%40qq.com; key=...; ip=...; expire_in=..."
# 多账号用 & 分隔
```

> **Cookie 固定 7 天有效，不滑动续期。**
> `ikuuu-cookie.yml` 每 3 天尝试无头登录刷新；若 Geetest 降级为图片点选会失败，
> 此时需本机手动刷新：`IKUUU_HEADLESS=0 python ikuuu_login.py refresh`。

### SMAI

| Secret | 说明 |
|--------|------|
| `SMAI_SESSION` | 浏览器 `api.smai.ai` 的 session Cookie，留空则跳过 |
| `SMAI_USER_ID` | 数字 uid |
| `SMAI_REFRESH` | 长期 cookie `new_api_refresh`，用于续期换 Bearer |
| `REPO_PAT` | 可选；用于自动回写轮换后的 `SMAI_REFRESH` |

> 当前签到接口强制 Turnstile，纯 API 无法生成验证 token，故整体留空跳过。

## 示例配置

```bash
GLADOS_COOKIE="koa:sess=abc123..."
IKUUU_COOKIE="uid=123; email=a@qq.com; key=aaa; ip=bbb; expire_in=111"
# SMAI 留空跳过
```

## GitHub Actions 部署

在 **Settings → Secrets and variables → Actions** 配置上述变量。

工作流：
- `checkin.yml`：每日 2 次签到
- `ikuuu-cookie.yml`：每 3 天刷新 ikuuu Cookie（写入 Actions Cache）

## 签到结果示例

```
### 🖥️ GLaDOS - xxx@qq.com
• 积分：286 (+1)
• 剩余：170天
• 结果：Checkin! Got 1 Points

### 📶 ikuuu 签到结果
• cookie_1: 你获得了 1024MB 流量

---
📊 汇总：2/2 成功 | GLaDOS 1/1 | ikuuu 1/1
```

## 安全须知

- **工作流 action 已 pin 到 commit SHA**，避免 tag 被劫持
- 声明最小 `permissions`（`contents: read` + `actions: write`）
- **日志不含明文凭证**：只记录结果消息，不回显 Cookie / token
- `REPO_PAT` 建议用细粒度令牌，仅授予本仓 `Secrets: write`

## 故障排除

### ikuuu 返回「非 JSON 响应」
Cookie 已过期（服务端返回登录页 HTML）。重新抓取并更新 `IKUUU_COOKIE`，
或本机跑 `python ikuuu_login.py refresh`。

### ikuuu 返回 HTTP 405
站点换域名了。签到侧会自动发现；登录侧需手动更新 `IKUUU_BASE_URL`。

### ikuuu 刷新工作流失败
`Geetest：验证中 → 静默超时` 说明验证从 ai 一键降级为图片点选，无头无法通过。
此时本机 `IKUUU_HEADLESS=0 python ikuuu_login.py refresh` 人工过一次。

### GLaDOS 返回 `Cloudflare verification failed`
站点 CF 校验未通过。重新抓取 `GLADOS_COOKIE`；若持续失败，考虑本机运行。

### Token 过期
检测到疑似过期会在推送标题加 ⚠️ 并在正文列出。

## 技术特性

- 统一平台驱动 `_drive()`：GLaDOS / ikuuu / SMAI 共用跳过、计数、告警逻辑
- 北京时间处理，避免 runner 时区差异
- 签到成功后记录状态，避免重复签到
- ikuuu 域名自动发现（解析旧域名公告页的混淆 JS）

## 更新历史

- **2026-11-19**: 移除 42w.shop 全链路（含 mihomo 代理、CDP 抓 Cookie、订阅同步脚本）；
  工作流简化为直跑 `checkin.py`
- **2026-09-13**: ikuuu 域名自动发现；订阅拉取改三档策略
- **2026-09-11**: ikuuu 换域名至 ikuuu.top
- **2026-08-15**: 接入 42w.shop（后于 2026-11-19 移除）
- **2026-04-14**: 移除 VOAPI；修复北京时间计算
- 2026-04-05: ikuuu 改为 Cookie 模式

---

推荐配合 GitHub Actions 实现完全自动化签到。
