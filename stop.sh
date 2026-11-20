#!/usr/bin/env bash
# OcuPanel 停止脚本：先按 PID 文件停，再兜底按端口找漏网进程
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$ROOT/local/ocupanel.pid"
ENVFILE="$ROOT/backend/.env"

PORT="$(sed -n 's/^[[:space:]]*PORT[[:space:]]*=[[:space:]]*//p' "$ENVFILE" 2>/dev/null | tail -1 | tr -d '"'"'"'\r')"
PORT="${PORT:-8000}"

stopped=0

stop_pid() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || return 1
  kill "$pid" 2>/dev/null || return 1
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || { echo "已停止 PID $pid"; stopped=1; return 0; }
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null && echo "已强制停止 PID $pid" && stopped=1
}

if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] && stop_pid "$pid"
  rm -f "$PIDFILE"
fi

# 兜底：PID 文件丢了/对不上时，按端口找本项目自己的进程（别的程序不动）
if [[ "$stopped" == 0 ]]; then
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    # 精确匹配本仓库路径，不依赖目录名（clone 到 /opt/ocupanel 也能认出来）
    if ps -p "$pid" -o command= 2>/dev/null | grep -qF -- "$ROOT/backend/main.py"; then
      stop_pid "$pid"
    fi
  done < <(lsof -ti:"$PORT" 2>/dev/null || true)
fi

if [[ "$stopped" == 0 ]]; then
  other="$( { command -v lsof >/dev/null 2>&1 && lsof -ti:"$PORT" 2>/dev/null | head -1; } || true)"
  if [[ -n "$other" ]]; then
    echo "未在运行（端口 $PORT 被其它进程 PID $other 占用，未处理）"
  else
    echo "未在运行"
  fi
fi
exit 0
