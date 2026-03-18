# 🎁 多平台自动签到集合 (GLaDOS + ikuuu + SMAI.AI)

<div align="center">

**不用写代码 · 不用买服务器 · 不用每天登录**

**一次配置，永久自动签到**

| 平台 | 签到奖励 | 每日次数 | 状态 |
|------|---------|---------|------|
| 🖥️ **GLaDOS** | +12~20 积分 | 2次 (9:30/19:30) | ✅ |
| 📶 **ikuuu** | 流量 + 时长 | 2次 | ✅ |
| ✅ **SMAI.AI** | 10,000~50,000 额度 | 2次 | ✅ |

[![Auto Checkin](https://github.com/1013608955/2026-glados-checkin/actions/workflows/checkin.yml/badge.svg)](https://github.com/1013608955/2026-glados-checkin/actions)

**⭐ 觉得有用？点个 Star 支持一下！**

</div>

---

## 🚀 三步部署

### 第一步：Fork 本仓库

点右上角 **Fork** 按钮，复制到你自己的 GitHub 账号下。

### 第二步：配置 Secrets

进入仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

#### GLaDOS（必填）

| Secret 名称 | 值 | 说明 |
|---|---|---|
| `GLADOS_COOKIE` | `koa:sess=xxx; koa:sess.sig=yyy` | 浏览器 Cookie |

**获取方法：**
1. 安装浏览器扩展 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalgdj)
2. 登录 https://glados.cloud → 进入签到页面
3. 点 Cookie-Editor → 找到 `koa:sess` 和 `koa:sess.sig`
4. 组合：`koa:sess=长字符串; koa:sess.sig=短字符串`

#### ikuuu（可选）

| Secret 名称 | 值 | 说明 |
|---|---|---|
| `IKUUU_ACCOUNTS` | `邮箱1:密码1&邮箱2:密码2` | 多账号用 `&` 分隔 |

旧格式仍然有效（单账号）：分别设置 `IKUUU_EMAIL` 和 `IKUUU_PASSWORD`

#### SMAI.AI（可选）

| Secret 名称 | 值 | 必填 | 说明 |
|---|---|---|---|
| `SMAI_SESSION` | `MTc3MzYw...` | ✅ | 浏览器 session 值 |
| `SMAI_USER_ID` | `1201` | ✅ | `new-api-user` 的值 |

**获取方法：** 两个值从同一个请求中获取
1. 登录 https://api.smai.ai/console/checkin
2. 按 `F12` → **Network（网络）** → 点击页面上的签到按钮（或刷新页面）
3. 点一个 `/api/` 开头的请求，查看 **Request Headers**：
   - **`SMAI_SESSION`** → Cookie 字段中 `session=` 后面的值
   - **`SMAI_USER_ID`** → `new-api-user` 字段的值（纯数字）

> 💡 两个值必须来自**同一个账号**的浏览器请求。多账号用 `&` 分隔，一一对应。

#### 微信推送（可选）

| Secret 名称 | 值 | 说明 |
|---|---|---|
| `WPUSH_APIKEY` | wpush.cn API Key | 签到结果推送到微信 |

### 第三步：启用 Actions

1. 进入 **Actions** 标签页
2. 点击启用工作流
3. 点 **Run workflow** 测试一次

🎉 **完成！** 每天 9:30 和 19:30 自动签到。

---

## 🔄 签到逻辑

| 时间 | 行为 |
|------|------|
| **9:30（上午）** | 全平台全账号签到，记录结果 |
| **19:30（下午）** | 上午成功的账号跳过，失败的重试 |
| **全部成功** | 如果所有账号上午都成功，下午跳过推送 |

---

## 📱 多账号支持

所有平台都支持多账号，用 `&` 分隔：

| 平台 | Secret | 多账号格式 |
|------|--------|-----------|
| GLaDOS | `GLADOS_COOKIE` | `cookie1&cookie2&cookie3` |
| ikuuu | `IKUUU_ACCOUNTS` | `email1:password1&email2:password2` |
| SMAI.AI | `SMAI_SESSION` | `session1&session2` |
| SMAI.AI | `SMAI_USER_ID` | `uid1&uid2`（与 session 一一对应） |

---

## ⚠️ Token 过期提醒

某个平台的 token 失效时，推送会带红色警告：

```
⚠️ Token 过期警告
  🔴 GLaDOS [user@email.com] Cookie 可能已过期
  🔴 SMAI.AI Session 可能已过期
  👉 请更新对应平台的 Secret
```

---

## ❓ 常见问题

**Q: GLaDOS 显示 "please checkin via glados.cloud"？**
> 本项目已修复此问题，token 使用 `glados.cloud`。

**Q: Cookie/Session 多久过期？**
> GLaDOS 约 30 天，SMAI 约几周。收到过期警告时更新即可。

**Q: Actions 定时不触发？**
> 新仓库可能不稳定，可用 [cron-job.org](https://cron-job.org) 外部触发（免费）。

---

## 📁 项目文件

| 文件 | 说明 |
|------|------|
| `checkin.py` | 三平台签到脚本（Python） |
| `.github/workflows/checkin.yml` | GitHub Actions 工作流 |
| `requirements.txt` | Python 依赖 |
| `images/` | GLaDOS 教程截图 |

---

## 📝 License

MIT
