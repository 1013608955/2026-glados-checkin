# 2026多平台自动签到 (GLaDOS + ikuuu + SMAI.AI + VOAPI)

本项目是一个自动签到脚本，支持并发签到多个平台。

## 平台支持

| 平台 | 状态 | 特性 |
|------|------|------|
| GLaDOS | ✅ 完全支持 | 网络超级连接、主机测试、分数签到 |
| ikuuu | ✅ 完全支持 | VPN服务器签到、会员管理（支持 Cookie 模式，绕过验证码） |
| SMAI.AI | ✅ 完全支持 | 科学上网服务、实验机、网络分数 |
| VOAPI | ✅ 完全支持 | API平台签到、余额查询 |

## 特性功能

- ✅ **多账号支持**：一次性签到多个账号
- ✅ **按账号级别签到**：成功了下午就不再签到该账号
- ✅ **Token过期自动检测**：检测平台平台会话或Cookie过期
- ✅ **支持多个GLaDOS域名**：自动试试取最好连接
- ✅ **实时打印**：使用北京时间，具体到的签到情况
- ✅ **成功推送**：支持微信推送，让你实时知道签到成功

## 快速使用

### 环境变量配置

#### GLaDOS
```bash
GLADOS_COOKIE="koa:sess=your_cookie_here"
# 多账号用 & 分隔
GLADOS_COOKIE="koa:sess=cookie1...&koa:sess=cookie2..."
```

#### ikuuu（推荐 Cookie 模式）

**ikuuu 已启用登录验证码，账号密码模式会失败，请使用 Cookie 模式。**

**如何获取 Cookie（方案一：浏览器开发者工具）**

1. 用 Chrome/Edge 登录 ikuuu：https://ikuuu.nl
2. 按 `F12` 打开开发者工具
3. 切到 **Network**（网络）标签页
4. 刷新页面（F5），在请求列表里点第一个请求（通常是 `ikuuu.nl` 或 `user`）
5. 右侧看 **Headers**（请求头），滚动找到 `Cookie:` 那一行
6. 复制 `Cookie:` 后面直到行末的整个字符串

你需要保留以下字段（缺一不可）：
```
uid=xxxxx; email=你的邮箱; key=你的key; ip=你的ip; expire_in=时间戳
```

**⚠️ 缺少任何字段都可能被 Cloudflare 拦截，返回 HTML 而不是 JSON。**

```bash
# Cookie 模式（推荐）
IKUUU_COOKIE="uid=5012965; email=1013608955%40qq.com; key=049dd4...; ip=40f36d...; expire_in=1775922637"

# 多账号用 & 分隔（每个账号的完整 Cookie 用 & 连接）
IKUUU_COOKIE="uid=1; email=a@qq.com; key=aaa; ip=bbb; expire_in=111&uid=2; email=b@qq.com; key=ccc; ip=ddd; expire_in=222"
```

> **备用方案**：账号密码模式（`IKUUU_ACCOUNTS="email:password"`）仍然可用，但遇到验证码时会失败。

#### SMAI
```bash
SMAI_SESSION="your_session_here"
SMAI_USER_ID="your_user_id_here"
# 多账号用 & 分隔
SMAI_SESSION="session1&session2"
SMAI_USER_ID="uid1&uid2"
```

#### VOAPI
```bash
VOAPI_TOKEN="your_token_here"
# 多账号用 & 分隔
VOAPI_TOKEN="token1&token2"
```

### 例子配置

```bash
# 一个GLaDOS账号
GLADOS_COOKIE="koa:sess=abc123..."

# 两个ikuuu账号（Cookie模式）
IKUUU_COOKIE="uid=123; email=a@qq.com; key=aaa; ip=bbb; expire_in=111&uid=456; email=b@qq.com; key=ccc; ip=ddd; expire_in=222"

# 一个SMAI账号
SMAI_SESSION="session_abc123..."
SMAI_USER_ID="123456789"

# 一个VOAPI账号
VOAPI_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### GitHub Actions 部署

推荐在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中配置上述环境变量为 Secrets。

## 签到结果示例

```
### 📶 ikuuu 签到结果
• cookie_1: 似乎已经签到过了哦...

### ✅ SMAI.AI 签到结果
• 123456789: 签到成功 +$0.000006，累计 $0.164144，共 5 天
```

## 故障排除

### 平台访问异常
如果出现连接问题，脚本会自动尝试其他可用的GLaDOS域名。

### ikuuu 签到失败
- 确认 Cookie 是否包含 `uid`、`email`、`key`、`ip`、`expire_in` 五个字段
- 如果返回"系统无法接受您的验证结果"，说明 Cookie 不完整或已过期
- Cookie 过期后需重新从浏览器抓取并更新 Secret
- `expire_in` 时间戳表示 Cookie 过期时间，过期前需要手动更新

### Token过期
当检测到账号可能过期时，会推送警告。

## 技术特性

- 使用北京时间，避免时区差异
- 支持多个GLaDOS域名自动试试
- 签到成功后会记录状态，避免重复签到
- 支持微信推送，让你实时知道签到成功
- ikuuu 支持 Cookie 模式，绕过登录验证码

## 更新历史

- 2026-04-05: ikuuu 改为 Cookie 模式，绕过登录验证码
- 2026-03-26: 合并 VOAPI 签到功能，支持多账号签到
- 2026-03-21: 修复SMAI额度单位转换问题，使显示的额度与网站一致

---

推荐配合使用 GitHub Actions 实现完全自动化签到。
