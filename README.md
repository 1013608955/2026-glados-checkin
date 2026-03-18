# 🎁 多平台自动签到集合 (GLaDOS + ikuuu + SMAI.AI)

<div align="center">

**不用写代码 · 不用买服务器 · 不用每天登录**

**一次配置，永久自动签到**

---

| 平台 | 签到奖励 | 每日次数 | 状态 |
|------|---------|---------|------|
| 🖥️ **GLaDOS** | +12~20 积分 | 2次 (9:30/21:30) | ✅ |
| 📶 **ikuuu** | 流量 + 时长 | 1次 | ✅ |
| ✅ **SMAI.AI** | 10,000~50,000 额度 | 1次 | ✅ |

---

[![Auto Checkin](https://github.com/1013608955/2026-glados-checkin/actions/workflows/checkin.yml/badge.svg)](https://github.com/1013608955/2026-glados-checkin/actions)
[![GitHub Stars](https://img.shields.io/github/stars/1013608955/2026-glados-checkin?style=social)](https://github.com/1013608955/2026-glados-checkin)

**⭐ 觉得有用？点个 Star 支持一下！**

</div>

---

## 🚀 快速部署（3步搞定）

### 第一步：Fork 本仓库

点击页面右上角的 **Fork** 按钮，将项目复制到你的账号下。

---

### 第二步：配置 Secrets 🔐

进入你 Fork 的仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

#### GLaDOS 配置（必需）

| Name | Value | 说明 |
|------|-------|------|
| `GLADOS_COOKIE` | `koa:sess=xxx; koa:sess.sig=yyy` | 从浏览器 Cookie-Editor 获取 |

> ⚠️ GLaDOS 官网已迁移到 **https://glados.cloud**

**获取 Cookie 方法：**
1. 安装浏览器扩展 [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalgdj)
2. 登录 https://glados.cloud → 进入签到页面
3. 点击 Cookie-Editor 图标 → 找到 `koa:sess` 和 `koa:sess.sig`
4. 组合格式：`koa:sess=长字符串; koa:sess.sig=短字符串`

#### ikuuu 配置（可选）

| Name | Value | 说明 |
|------|-------|------|
| `IKUUU_EMAIL` | 你的邮箱 | ikuuu.nl 账号 |
| `IKUUU_PASSWORD` | 你的密码 | ikuuu.nl 密码 |

#### SMAI.AI 配置（可选）

| Name | Value | 必需 | 说明 |
|------|-------|------|------|
| `SMAI_SESSION` | `MTc3MzYwNzgw...` | ✅ | 浏览器 Cookie 中的 session 值 |
| `SMAI_USER_ID` | 留空即可 | ❌ | 会自动获取，一般不需要手动填 |

**获取 Session 方法：**
1. 浏览器打开 https://api.smai.ai/console/checkin 并登录
2. 按 `F12` → **Network（网络）** → 刷新页面
3. 点任意请求 → **Headers** → 找到 **Cookie** → 复制 `session=` 后面的全部内容

#### 微信推送配置（可选）

| Name | Value | 说明 |
|------|-------|------|
| `WPUSH_APIKEY` | wpush.cn API Key | 微信推送通知 |

---

### 第三步：启用 Actions ⚡

1. 进入 **Actions** 标签页
2. 点击 **I understand my workflows, go ahead and enable them**
3. 点击左侧 **Multi-Platform Checkin**
4. 点击右侧 **Run workflow** 手动测试一次

**🎉 完成！** 以后每天自动签到。

---

## ⭐ 推荐：使用 cron-job.org 触发（更稳定）

GitHub Actions 定时任务对新仓库可能不触发。推荐使用免费的 cron-job.org：

1. 注册 [cron-job.org](https://cron-job.org)（免费）
2. 创建两个 Cronjob（早 9:30 + 晚 21:30）
3. URL 填：`https://api.github.com/repos/你的用户名/2026-glados-checkin/actions/workflows/checkin.yml/dispatches`
4. 方法选 POST，Headers 加：
   - `Accept: application/vnd.github.v3+json`
   - `Authorization: token 你的GitHub_Token`
   - `Content-Type: application/json`
5. Body 填：`{"ref": "main"}`

> 💡 GitHub Token 需要勾选 **workflow** 权限

---

## 📊 推送效果预览

```
多平台签到结果 | GLaDOS成功1/1 | ikuuu: 获得流量！

### 🖥️ GLaDOS 签到结果
### 🖥️ GLaDOS - user@email.com
• 当前积分：46 (+20)
• 剩余天数：353 天
• 签到结果：Checkin Repeats!

🎁 积分兑换：
❌ 100分→10天 (差54分)
❌ 200分→30天 (差154分)

---
### 📶 ikuuu 签到结果
• 签到结果：获得 1024 MB 流量

⏰ 执行时间：2026-03-18 09:30:00
```

---

## ⏰ 自动运行时间

| 时间（北京时间） | 平台 | 说明 |
|------------------|------|------|
| **09:30** | GLaDOS + ikuuu + SMAI | 全平台早间签到 |
| **21:30** | GLaDOS | GLaDOS 晚间签到 |

---

## ❓ 常见问题

<details>
<summary><b>Q: 显示 "please checkin via https://glados.cloud" 怎么办？</b></summary>

签到脚本已修复此问题（token 使用 `glados.cloud`）。如果仍报错，请检查 Cookie 是否正确。

</details>

<details>
<summary><b>Q: Cookie 多久过期？</b></summary>

约 30 天。过期后重新获取新 Cookie，更新 Secret 即可。

</details>

<details>
<summary><b>Q: 支持多账号吗？</b></summary>

所有平台均支持多账号，用 `&` 分隔：

| 平台 | Secret | 格式 |
|------|--------|------|
| GLaDOS | `GLADOS_COOKIE` | `cookie1&cookie2` |
| ikuuu | `IKUUU_ACCOUNTS` | `email1:password1&email2:password2` |
| SMAI.AI | `SMAI_SESSION` | `session1&session2` |

ikuuu 旧格式 `IKUUU_EMAIL` + `IKUUU_PASSWORD` 仍然有效。

</details>

<details>
<summary><b>Q: Actions 定时不执行怎么办？</b></summary>

使用 [cron-job.org](https://cron-job.org) 外部触发，详见上方推荐方案。

</details>

<details>
<summary><b>Q: SMAI.AI session 过期了怎么办？</b></summary>

重新登录 https://api.smai.ai/console/checkin，按 F12 抓取新的 session，更新 Secret。

</details>

---

## 📂 项目文件

| 文件 | 说明 |
|------|------|
| `checkin.py` | GLaDOS + ikuuu 签到脚本 (Python) |
| `smai_checkin.js` | SMAI.AI 签到脚本 (Node.js) |
| `.github/workflows/checkin.yml` | GitHub Actions 工作流 |
| `requirements.txt` | Python 依赖 |
| `images/` | 教程截图 |

---

## 🤝 需要帮助？

- 📝 **提 Issue**：遇到问题请提 Issue
- ⭐ **Star**：如果对你有帮助，请点个 Star 支持一下
- 🍴 **Fork**：欢迎 Fork 并贡献代码

---

## 📝 License

MIT

---

<div align="center">

**Made with ❤️ for 自动签到爱好者**

</div>
