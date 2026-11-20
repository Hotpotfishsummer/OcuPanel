#!/usr/bin/env bash
# OcuPanel 运行状态：进程 + 端口 + HTTP 健康 + 最近日志
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$ROOT/local/ocupanel.pid"
LOGFILE="$ROOT/logs/ocupanel.log"
ENVFILE="$ROOT/backend/.env"

PORT="$(sed -n 's/^[[:space:]]*PORT[[:space:]]*=[[:space:]]*//p' "$ENVFILE" 2>/dev/null | tail -1 | tr -d '"'"'"'\r')"
PORT="${PORT:-8000}"
URL="http://127.0.0.1:$PORT"

code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "$URL/api/account/me" 2>/dev/null)"
code="${code:-000}"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  printf '进程：运行中 (PID %s)\n' "$(cat "$PIDFILE")"
else
  printf '进程：未运行\n'
fi

if [[ "$code" == "000" ]]; then
  printf 'HTTP：无应答  ← %s\n' "$URL"
elif [[ "$code" == "401" ]]; then
  printf 'HTTP：401（服务正常，接口需登录）  ← %s\n' "$URL"
else
  printf 'HTTP：%s  ← %s\n' "$code" "$URL"
fi

if [[ "$code" != "000" && -f "$LOGFILE" ]]; then
  printf '日志：%s\n' "$LOGFILE"
  printf '%s\n' '--- 最近 3 行 ---'
  tail -3 "$LOGFILE"
fi

# 有问题的退出码，方便脚本化判断
[[ "$code" != "000" ]] && exit 0 || exit 1
