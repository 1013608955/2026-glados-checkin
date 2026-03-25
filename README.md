# 2026多平台自动签到 (GLaDOS + ikuuu + SMAI.AI + VOAPI)

本项目是一个自动签到脚本，支持并发签到多个平台。

## 平台支持

| 平台 | 状态 | 特性 |
|------|------|------|
| GLaDOS | ✅ 完全支持 | 网络超级连接、主机测试、分数签到 |
| ikuuu | ✅ 完全支持 | VPN服务器签到、会员管理 |
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

```bash
# GLaDOS
GLADOS_COOKIE="koa:sess=your_cookie_here"

# ikuuu
IKUUU_ACCOUNTS="email1:password1&email2:password2"

# SMAI
SMAI_SESSION="your_session_here"
SMAI_USER_ID="your_user_id_here"

# VOAPI
VOAPI_TOKEN="your_token_here"
```

### 例子配置

```bash
# 一个GLaDOS账号
GLADOS_COOKIE="koa:sess=abc123..."

# 两个ikuuu账号
IKUUU_ACCOUNTS="user1@example.com:pass1&user2@example.com:pass2"

# 一个SMAI账号
SMAI_SESSION="session_abc123..."
SMAI_USER_ID="123456789"

# 一个VOAPI账号
VOAPI_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

## 签到结果示例

```
✅ SMAI.AI 签到结果
● 123456789: 签到成功 +$0.000006，累计 $0.164144，共 5 天
```

## 故障排除

### 平台访问异常
如果出现连接问题，脚本会自动尝试其他可用的GLaDOS域名。

### 签到失败
如果签到失败，平台会返回具体错误信息，并在下次签到时重试。

### Token过期
当检测到账号可能过期时，会推送警告。

## 技术特性

- 使用北京时间，避免时区差异
- 支持多个GLaDOS域名自动试试
- 签到成功后会记录状态，避免重复签到
- 支持微信推送，让你实时知道签到成功

## 更新历史

- 2026-03-26: 合并 VOAPI 签到功能，支持多账号签到
- 2026-03-21: 修复SMAI额度单位转换问题，使显示的额度与网站一致

---

推荐配合使用 GitHub Actions 实现完全自动化签到。