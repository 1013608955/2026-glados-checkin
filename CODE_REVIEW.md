# 代码库优化与安全审计（2026-08-23）

覆盖文件：`checkin.py`(791) · `_ws.py`(115) · `cdp_get_cookies.py`(45) · `smai_capture.py`(178)
· `check_expiry.py`(102) · `gen_mihomo_config.py`(93) · `run_checkin.sh`(35) · `requirements.txt`
· `.github/workflows/checkin.yml` · `keep-alive.yml` · `.gitignore`

## 一、整体结构与模块依赖

```
checkin.py ──(无内部 import，纯 stdlib+requests)──▶ 4 平台签到 + 状态 + 推送
   │
   ├─ GLaDOS 类        (内置)
   ├─ ikuuu_*()        (内置, 多域名容错)
   ├─ smai_one()       (内置, refresh→Bearer→checkin)
   └─ w42_one()        (内置, session+New-Api-User 头, 仅本平台走 W42_PROXY)

_ws.py ── 公共 CDP WebSocket 客户端（handshake / 分帧 / FIN / ping-pong）
   ├─ cdp_get_cookies.py    ✅ from _ws import（已去重）
   ├─ smai_capture.py       ✅ from _ws import（已去重）
   └─ check_expiry.py       ❌ 自带第 3 份 class WS（含 BUG，见 H1）

gen_mihomo_config.py ── W42_SUB 订阅 → 单节点 mihomo 配置
run_checkin.sh ── 启动 mihomo → 导出 W42_PROXY → 跑 checkin.py → 收尾
checkin.yml ── CI 编排（已 pin SHA / 最小权限 / concurrency / artifact）
keep-alive.yml ── 每日提交 last-active.txt 保持仓库活跃（⚠️ 未 pin，见 M1）
```

**关键实现小结**
- SMAI：短效 `session` cookie 必须先用长期 `new_api_refresh` POST `/auth/refresh` 续期拿 Bearer，再打 checkin（已修复，功能正确但被 Turnstile 拦截，CI 跳过）。
- 42w：New API 网关，`session` Cookie + `New-Api-User` 头；`trust_env=False` 只认 `W42_PROXY`；CF 拦截时给可操作提示。
- 状态机：`.checkin_state.json` 记录「上午成功」→ 下午整轮跳过（`SKIP_AFTERNOON`）。
- 安全基线（上一轮已做）：全站 `verify=True`、无 `shell=True`/`eval`、不回显 token、mihomo 绑本机。

---

## 二、问题清单（按优先级）

### 🔴 HIGH

**H1 · `check_expiry.py` 自带第 3 份 `class WS`，且忽略 FIN → 大响应被截断**
- 位置：`check_expiry.py:12-69`（自带 WS）；`_read_frame` 只返回 `(opcode, payload)`，未读 FIN 位；`recv()`(L51-63) 在**第一帧**就 `return`，不做分片拼接。
- 影响：CDP `Storage.getCookies` 返回全部 cookie 的 JSON 通常 >125 字节，会被分帧。该实现只取首帧 → `json.loads` 失败或拿到**部分 cookie** → 过期时间判断**静默错误**。同时与 `_ws.py` 形成第 3 份漂移实现，改一处另两处不同步。
- 附带：`.gitignore` 第 29 行已把 `check_expiry.py` 列为忽略，但 `git ls-files` 显示它**仍被跟踪**（gitignore 对已跟踪文件无效）→ 带 bug 的代码实际进了仓库历史。
- 建议：改为 `from _ws import WS, get_browser_ws`；并 `git rm --cached check_expiry.py` 让 .gitignore 生效（或保留则先修 bug）。

**H2 · `main()` 单函数 ~190 行，4 平台块高度复制粘贴**
- 位置：`checkin.py:602-791`（GLaDOS / ikuuu / SMAI / 42w 四段模板相同：if skipped / else 调签到 / record_success / 计数 / is_expired）。
- 影响：可维护性差。改一处（如新增过期关键词、改跳过逻辑）极易漏改其它三处；深层嵌套 + 平台特定分支使 diff 噪音大、回归风险高。
- 建议：抽成统一的「平台驱动循环」——定义 `Platform` 数据结构 `{name, keys, run(key,val)->(msg,ok,detail)}`，主流程只写一次 skip/record/count 逻辑。

**H3 · 错误处理过宽，失败根因被吞**
- 位置：`smai_one`(L519 `except Exception: return str(e)...`)、`w42_one`(L591)、`ikuuu_checkin_cookie` 各 `except` 分支、`load_state`/`save_state`(L142-166) 静默吞异常。
- 影响：网络抖动 / JSON 解析失败 / 状态文件损坏被统一降级成「签到失败」或「网络错误」，CI 日志里看不到真实异常类型与堆栈，排障只能靠猜。
- 建议：① 至少记录 `type(e).__name__` + 简短堆栈到日志；② `load_state` 损坏时重建而非静默返回空；③ 区分「网络错误」与「业务失败」。

---

### 🟡 MEDIUM

**M1 · `keep-alive.yml` 未 pin action + 直推 main**
- 位置：`keep-alive.yml:17` `actions/checkout@v4`（标签，未 pin SHA）；L30 `git push` 用 `GITHUB_TOKEN` 直推 `main`。
- 影响：① 与 `checkin.yml` 已建立的「SHA pin」安全基线**不一致**（供应链）；② 每日 `git push` 是远程 `main` 持续领先、本地 `origin/main` 跟踪引用假象的**根因**（每次 keep-alive 提交都推进 origin/main）。
- 建议：keep-alive 也 pin SHA；或统一收口到一个基建说明里。

**M2 · 明文 token 落盘**
- 位置：`persist_smai_refresh`(`checkin.py:110` 写 `smai_refresh_latest.txt`)、`smai_capture.py:163-172` 写 `smai_session.txt`/`smai_refresh.txt`。
- 影响：本机明文存长期凭证（虽在 .gitignore，但盘上明文、CI 工作区 ephemeral 仍明文）。`REPO_PAT` 建议用细粒度令牌（仅本仓 Secrets: write）而非全 `repo` 作用域。
- 建议：配了 `REPO_PAT` 时**不写明文文件**；落盘文件用后即删；README 已提醒但可加严格化。

**M3 · `gen_mihomo_config.extract_node` 假设节点单行 + 写死默认节点**
- 位置：`gen_mihomo_config.py:32-44`（要求 `- name: ... server: ...` 同一行）；`DEFAULT_NODE="新加坡高速 05| CTCM"`(L20)。
- 影响：若订阅用多行 YAML 映射（节点定义跨多行），`"server:" not in s` 直接漏抽 → `SystemExit`。默认节点名写死，订阅更新/换节点即失效。
- 建议：用 `yaml.safe_load` 解析订阅（需加 `PyYAML` 依赖，正好 M5 提到 PySocks 可被替换），按 `name` 取完整节点 dict 再序列化回单行。

**M4 · 可读性：嵌套推导式 + 冗余 import**
- 位置：`config['ikuuu']`(L627) 一层 `for...for email in [...]` 嵌套推导式，极难读；`from datetime import datetime, date, timedelta`(L17) 中 `datetime/date` 顶层未用；`get_beijing_time`(L37) 又重复 import 一次。
- 影响：阅读/修改成本高，新贡献者易写错。
- 建议：把 ikuuu 的 key 构造拆成显式 `for` 循环；清理未用 import；`get_beijing_time` 用顶层已导入的 `timedelta`。

**M5 · 死代码与闲置依赖**
- 位置：`voapi_tokens = []  # VOAPI 已移除`(L623)；`requirements.txt` 含 `PySocks`（代码仅用 `proxies={'http':...}`，SOCKS 方案未用 → PySocks 多余）。
- 影响：误导后续维护者；CI 多装一个包。
- 建议：删 `voapi_tokens`；若 M3 改用 YAML 解析，可把 `PySocks` 换成 `PyYAML`；否则删 `PySocks`。

**M6 · W42_COOKIE 无过期预警**
- 位置：业务约束（`W42_COOKIE` ~26 天过期，见项目记忆）；`checkin.py` 仅在签到失败时提示。
- 影响：CI 静默失效，直到某天 42w 全失败才发现 cookie 过期。
- 建议：`cdp_get_cookies.py` 已能读 `expires`；可在 CI 加一步「距过期 <N 天则发过期预警」，或本地定时跑 `check_expiry.py`。

---

### 🟢 LOW

- **L1 无结构化日志**：全靠 `print` + 手拼时间戳，CI 排障只能 `tee` 到 txt。建议引入 `logging`，按平台/级别输出。
- **L2 mihomo 就绪探测**：`run_checkin.sh:17` 用 `gstatic generate_204`，若 runner 禁 gstatic 则 `READY` 恒 0，但仍导出 `W42_PROXY`（不影响，但探测无意义）。可改探测本机监听端口是否 accept。
- **L3 成功/过期判定靠中文子串**：`is_expired`(L189-198) 与各处 `"成功" in msg` 对 API 文案改动脆弱（爬虫固有风险，可接受但应集中、便于一处调整）。
- **L4 SRP 违反**：`checkin.py` 同时承载 4 平台 + 状态 + 推送 + 编排。可拆 `glados/ikuuu/smai/w42/state/notify` 子模块（与 H2 同源，可一并重构）。
- **L5 concurrency group**：`checkin-${{ github.ref }}` 当前单分支 OK，多分支/PR 下需注意语义。

---

## 三、优先行动建议（落地顺序）

1. **H1**：`check_expiry.py` 改用 `_ws` 并 `git rm --cached`（顺手消除第 3 份漂移）。最小改动、消除静默错误。
2. **M1**：`keep-alive.yml` pin SHA，统一供应链基线。
3. **H3**：补异常类型/堆栈到日志（先不动架构，快速提升排障力）。
4. **H2 + L4**：把 4 平台抽成统一驱动（一次性重构，长期收益最大）。
5. **M3/M5**：订阅解析改 YAML + 清理依赖。
6. **M2/M6**：token 落盘收敛 + 过期预警。

> 注：上一轮已完成的（脱敏、去 IP 回显、pin SHA、最小权限、concurrency、artifact、mihomo 下载修复）不在本清单重复。

---

## 四、完成情况复核（2026-09-01）

`HIGH` 与 `MEDIUM` 全部关闭。逐条落点（供回归时对照，别再从零排查）：

| 项 | 状态 | 落点 |
|---|---|---|
| H1 | ✅ | `check_expiry.py` 改 `from _ws import WS`（消除第 3 份漂移实现，顺带修了忽略 FIN 导致的分帧截断）；已 `git rm --cached`，`.gitignore` 生效。当前 `git ls-files -i -c` 为空，无「被跟踪却已忽略」的文件。 |
| H2 | ✅ | `_drive()` 通用驱动；**四平台全部接入**（GLaDOS / ikuuu / SMAI / 42w）。ikuuu 是最后一个——因 cookie 直签与密码登录签名不同，长期保留着 41 行内联副本，最终靠 `_ikuuu_unit()` 统一入口 + `_ikuuu_warn()` 动态诊断收口。 |
| H3 | ✅ | `log_traceback(context)` 输出完整堆栈；推送异常带 `type(e).__name__`。 |
| M1 | ✅ | `keep-alive.yml` 已 pin `actions/checkout@11d5960...`（v4）。 |
| M2 | ✅ | 配了 `SMAI_PERSIST_PAT`/`REPO_PAT` 时不写明文文件，直接走 `gh secret set`。 |
| M3 | ✅ | 用 `extract_node_block()` 块级兜底支持多行 YAML 写法（**保持纯标准库，未引 PyYAML**）；`DEFAULT_NODE` 可由 `W42_SUB_NODE` 覆盖。 |
| M4 | ✅ | ikuuu key 的嵌套推导式改显式 `for` 循环；顶层只留 `date`，`datetime/timedelta` 按需局部导入并注释说明。 |
| M5 | ✅ | `voapi_tokens` 死代码已删；`requirements.txt` 移除 `PySocks`。 |
| M6 | ✅ | `w42_cookie_watch`：对 `W42_COOKIE` 取 sha256 指纹跨天留存，连续 ≥18 天预警。 |

**仍为 LOW 且主动接受**（审计原文即标注「可接受」）：L1 结构化日志（仍用 `log()` 包装 print）、
L2 mihomo 就绪探测走 gstatic、L3 中文子串判定、L4 模块拆分（H2 已解决同源问题的主要部分）、
L5 concurrency 语义（当前单分支无影响）。

### H2 收口时的等价性验证方法（可复用）
改「驱动层」这类横切重构，靠肉眼 review 不可靠。本次用的办法：
1. 把旧实现原样复制进测试脚本，与新实现**同时**跑同一批桩数据；
2. 覆盖边界：空账号、全成功、cookie 过期带/不带诊断、密码过期、上午跳过、失败但非过期、混合成功+过期；
3. 断言三件事分别相等：成功计数/total、`expired` 警告文本、输出行（归一化后才比）；
4. 最后用真实凭据跑一次端到端冒烟（本次实际签到成功 670 MB）。
若归一化后仍有差异，说明是行为漂移而非格式调整，必须回滚。
