#!/usr/bin/env bash
# 若设了 W42_SUB：gen_mihomo_config.py 抽取单节点 → 启动 mihomo（后台）→
# 等端口 7890 就绪 → 导出 W42_PROXY=http://127.0.0.1:7890 → 跑 checkin.py；
# 脚本结束杀掉 mihomo。若未设 W42_SUB：直接跑 checkin.py（沿用既有 W42_PROXY 或直连）。
set +e

MH_PID=""

if [ -n "$W42_SUB" ]; then
  echo "=== 启动 42w 代理 (mihomo) ==="
  python gen_mihomo_config.py
  nohup ./mihomo -f mihomo_config.yaml > mihomo.log 2>&1 &
  MH_PID=$!
  echo "mihomo PID=$MH_PID，等待端口 7890 ..."
  READY=0
  for i in $(seq 1 40); do
    if curl -s -x http://127.0.0.1:7890 https://api.42w.shop >/dev/null 2>&1; then
      echo "mihomo 就绪 (端口 7890)"; READY=1; break
    fi
    sleep 1
  done
  if [ "$READY" -eq 0 ]; then
    echo "⚠️ mihomo 端口未就绪，查看 mihomo.log："
    tail -20 mihomo.log 2>/dev/null || true
  fi
  export W42_PROXY=http://127.0.0.1:7890
fi

python checkin.py 2>&1 | tee checkin_output.txt

if [ -n "$MH_PID" ]; then
  kill $MH_PID 2>/dev/null || true
  echo "=== 已停止 mihomo (PID=$MH_PID) ==="
fi
