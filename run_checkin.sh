#!/usr/bin/env bash
# 若设了 W42_SUB：gen_mihomo_config.py 抽取单节点 → 启动 mihomo（后台）→
# 等端口 7890 就绪 → 导出 W42_PROXY=http://127.0.0.1:7890 → 跑 checkin.py；
# 脚本结束杀掉 mihomo。若未设 W42_SUB：直接跑 checkin.py（沿用既有 W42_PROXY 或直连）。
set +e

MH_PID=""

if [ -n "$W42_SUB" ]; then
  echo "=== 启动 42w 代理 (mihomo) ==="
  if [ ! -x ./mihomo ]; then
    # mihomo 只是 42w 的代理依赖。它缺失时 42w 会退化为直连（可能失败），
    # 但绝不能因此中断本次运行 —— GLaDOS / ikuuu 的签到不受影响。
    echo "⚠️ ./mihomo 不存在或不可执行，42w 将直连（可能失败），其余平台照常签到"
  else
    # gen_mihomo_config 失败时（订阅里已无该节点）会被 set +e 静默吞掉，
    # 后面却仍拿一个不存在的配置去启 mihomo，白等一整轮。这里显式判断。
    if ! python gen_mihomo_config.py; then
      echo "⚠️ 生成 mihomo 配置失败（订阅里可能已无该节点）→ 42w 本次大概率失败，其余平台照常"
    fi
    nohup ./mihomo -f mihomo_config.yaml > mihomo.log 2>&1 &
    MH_PID=$!
    echo "mihomo PID=$MH_PID，等待端口 7890 ..."
    READY=0
    for i in $(seq 1 15); do
      if curl -s -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204 >/dev/null 2>&1; then
        echo "mihomo 就绪 (端口 7890)"; READY=1; break
      fi
      # 进程已退出（配置有误 / 端口被占）就没必要继续等
      if ! kill -0 "$MH_PID" 2>/dev/null; then
        echo "⚠️ mihomo 进程已退出（配置有误或端口被占用）"
        break
      fi
      sleep 1
    done
    if [ "$READY" -eq 0 ]; then
      echo "⚠️ mihomo 未就绪：42w 将指向一个不通的代理端口（大概率失败），其余平台照常"
      echo "--- mihomo.log 尾部 ---"
      tail -20 mihomo.log 2>/dev/null || true
      echo "--- end ---"
    fi
    export W42_PROXY=http://127.0.0.1:7890
  fi
fi

python checkin.py 2>&1 | tee checkin_output.txt

if [ -n "$MH_PID" ]; then
  kill $MH_PID 2>/dev/null || true
  echo "=== 已停止 mihomo (PID=$MH_PID) ==="
fi
