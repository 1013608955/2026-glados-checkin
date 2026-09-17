#!/usr/bin/env bash
# 42w 代理（mihomo）编排脚本。
#
# 订阅拉取策略（重要，别改回去）：
#   机场明确限流——「短时间内超出限制，或检测到订阅被分享，会自动重置订阅凭证」，
#   旧链接随即失效。所以**默认根本不拉订阅**：
#     · 优先用 Secret W42_NODE_YAML（由本机 w42_sync_node.py 同步上去的单节点配置）
#     · 只有该节点连接失败时，才带 --force-subscription 重新拉一次订阅（低频兜底）
#     · 未配置 W42_NODE_YAML 时才必然走订阅（兼容旧行为）
# 若未设 W42_SUB 且无 W42_NODE_YAML：直接跑 checkin.py（42w 直连，多半失败）。
set +e

MH_PID=""

# 启动 mihomo 并等待就绪。$1 额外传给 gen_mihomo_config.py 的参数（可为空）。
# 返回 0=就绪，1=失败（已打印原因）。
start_stack() {
  if ! python gen_mihomo_config.py $1; then
    echo "⚠️ 生成 mihomo 配置失败 → 42w 本次大概率失败，其余平台照常"
    return 1
  fi
  nohup ./mihomo -f mihomo_config.yaml > mihomo.log 2>&1 &
  MH_PID=$!
  echo "mihomo PID=$MH_PID，等待端口 7890 ..."
  for i in $(seq 1 15); do
    # 必须带 --max-time：探测本身走代理，节点不通时 curl 会一直挂着，
    # 单次无上限会把整轮拖到 80 秒以上。
    if curl -s --max-time 5 -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204 >/dev/null 2>&1; then
      echo "mihomo 就绪 (端口 7890)"
      return 0
    fi
    # 进程已退出（配置有误 / 端口被占）就没必要继续等
    if ! kill -0 "$MH_PID" 2>/dev/null; then
      echo "⚠️ mihomo 进程已退出（配置有误或端口被占用）"
      return 1
    fi
    sleep 1
  done
  echo "⚠️ mihomo 端口等待超时（节点不可达或被拒）"
  return 1
}

kill_mihomo() {
  if [ -n "$MH_PID" ]; then
    kill "$MH_PID" 2>/dev/null || true
    MH_PID=""
  fi
}

if [ -n "$W42_SUB" ] || [ -n "$W42_NODE_YAML" ]; then
  echo "=== 启动 42w 代理 (mihomo) ==="
  if [ ! -x ./mihomo ]; then
    # mihomo 只是 42w 的代理依赖。它缺失时 42w 退化为直连（可能失败），
    # 但绝不能因此中断本次运行 —— GLaDOS / ikuuu 的签到不受影响。
    echo "⚠️ ./mihomo 不存在或不可执行，42w 将直连（可能失败），其余平台照常签到"
  else
    if start_stack ""; then
      export W42_PROXY=http://127.0.0.1:7890
    else
      tail -20 mihomo.log 2>/dev/null || true
      kill_mihomo

      # 唯一的主动拉订阅时机：同步下来的节点连不上（多半是机场换了节点信息）。
      # 正常情况下这里不会执行，所以不会消耗订阅拉取配额。
      if [ -n "$W42_NODE_YAML" ] && [ -n "$W42_SUB" ]; then
        echo "=== 同步节点连不上，尝试重新拉取订阅（低频兜底）==="
        if start_stack "--force-subscription"; then
          export W42_PROXY=http://127.0.0.1:7890
          echo "✅ 重新拉取订阅后 42w 代理已可用"
        else
          tail -20 mihomo.log 2>/dev/null || true
          kill_mihomo
          echo "⚠️ 重新拉取订阅后仍不通：42w 本次直连（大概率被 Cloudflare 拦截）"
        fi
      else
        # 绝不导出 W42_PROXY：否则 42w 会对着死端口发请求，换来莫名其妙的
        # SSLError，反而掩盖「代理根本没起来」这个事实。
        echo "⚠️ 42w 本次将直连（大概率被 Cloudflare 拦截），其余平台照常"
      fi
    fi
  fi
fi

python checkin.py 2>&1 | tee checkin_output.txt

kill_mihomo && echo "=== 已停止 mihomo ==="
