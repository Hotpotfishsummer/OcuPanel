#!/usr/bin/env bash
# OcuPanel 一键启动（macOS / Linux，Git Bash 也可）
#
#   bash start.sh            启动（缺什么补什么）
#   bash start.sh restart    先停再起
#   bash start.sh --no-open  启动但不打开浏览器
#
# 幂等：已经在跑就只做健康检查并打开页面，不会重复占端口。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PIDFILE="$ROOT/local/ocupanel.pid"
LOGFILE="$ROOT/logs/ocupanel.log"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
ENVFILE="$ROOT/backend/.env"

OPEN_BROWSER=1
ACTION=start
for arg in "$@"; do
  case "$arg" in
    restart) ACTION=restart ;;
    --no-open|--no-browser) OPEN_BROWSER=0 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$arg（可用：restart / --no-open）" >&2; exit 2 ;;
  esac
done

# ---------- 小工具 ----------
info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

http_code() {  # http_code <url>  → 000 表示连不上
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "$1" 2>/dev/null)"
  printf '%s' "${code:-000}"
}

open_browser() {
  [[ "$OPEN_BROWSER" == 1 ]] || return 0
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 &
  else info "请手动打开 $1"; fi
}

# 判定「是不是我们的服务」：/api/account/me 未登录时固定返回 401 + {"detail":"未登录"}。
# 只看端口通不通不够——别的程序占了 8000 也会 200，会误判成「已在运行」。
healthy() {
  local code
  code="$(http_code "${BASE_URL}/api/account/me")"
  [[ "$code" == "200" || "$code" == "401" ]]
}

read_env() {  # read_env KEY DEFAULT
  local value=""
  [[ -f "$ENVFILE" ]] && value="$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENVFILE" | tail -1 | tr -d '"'"'"'\r')"
  [[ -n "$value" ]] && printf '%s' "$value" || printf '%s' "$2"
}

# ---------- 配置 ----------
PORT="$(read_env PORT 8000)"
HOST="$(read_env HOST 127.0.0.1)"
[[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]] && HOST=127.0.0.1
BASE_URL="http://$HOST:${PORT}"

port_pid() {  # 端口占用者；没有 lsof（精简 Linux）就跳过这项检查
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -ti:"$1" 2>/dev/null | head -1 || true
}

# ---------- 已在运行？ ----------
running_pid=""
[[ -f "${PIDFILE}" ]] && running_pid="$(cat "${PIDFILE}" 2>/dev/null || true)"
if [[ -n "$running_pid" ]] && ! kill -0 "$running_pid" 2>/dev/null; then
  rm -f "${PIDFILE}"; running_pid=""
fi

if [[ "$ACTION" == "restart" ]]; then
  info "先停止现有实例"
  bash "$ROOT/stop.sh" || true
  running_pid=""
fi

if healthy; then
  ok "已在运行 → ${BASE_URL}"
  info "日志：${LOGFILE}"
  open_browser "${BASE_URL}"
  exit 0
fi

if [[ -n "$running_pid" ]]; then
  # 进程还在但端口没应答：可能是刚启动/正在加载模型，给 30 秒
  info "进程 $running_pid 存在但 HTTP 未就绪，最多再等 30 秒…"
  for _ in $(seq 1 30); do healthy && break; sleep 1; done
  if healthy; then
    ok "已就绪 → ${BASE_URL}"
    open_browser "${BASE_URL}"
    exit 0
  fi
  die "进程在但一直没应答。先 bash stop.sh，再看日志：${LOGFILE}"
fi

# ---------- 1. Python ----------
if [[ -x "$PY" ]]; then
  info "Python 虚拟环境已存在（$("$PY" -V 2>&1)）"
else
  BASE_PY=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then BASE_PY="$candidate"; break; fi
  done
  [[ -n "$BASE_PY" ]] || die "找不到 python3，请先安装 Python 3.11+（https://www.python.org/downloads/）"
  "$BASE_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "$BASE_PY 版本过低，需要 3.11+"
  info "创建虚拟环境 .venv（$("$BASE_PY" -V 2>&1)）"
  "$BASE_PY" -m venv "$VENV" || die "创建 venv 失败；Debian/Ubuntu 请先 sudo apt install python3-venv"
  PY="$VENV/bin/python"
  ok "虚拟环境就绪"
fi

# ---------- 2. 后端依赖 ----------
# 用 importlib.metadata 抽查，不真的 import torch（省 2–4 秒）
if "$PY" - <<'PYCHECK' >/dev/null 2>&1
import importlib.metadata as m
for pkg in ("fastapi", "uvicorn", "torch", "transformers", "fpdf2", "opencv-python", "python-dotenv"):
    m.version(pkg)
PYCHECK
then
  info "后端依赖已安装"
else
  info "安装后端依赖（首次约 2–5 分钟，torch 体积较大）"
  "$PY" -m pip install --upgrade pip -q
  "$PY" -m pip install -r "$ROOT/backend/requirements.txt" \
    || die "依赖安装失败，检查网络或 pip 源后重试"
  ok "后端依赖安装完成"
fi

# ---------- 3. .env ----------
if [[ ! -f "$ENVFILE" ]]; then
  cp "$ROOT/backend/.env.example" "$ENVFILE"
  chmod 600 "$ENVFILE" 2>/dev/null || true
  ok "已从 .env.example 生成 backend/.env"
fi
if [[ -z "$(read_env OPENAI_BASE_URL '')" && -z "$(read_env OPENAI_API_KEY '')" ]]; then
  warn "backend/.env 里没有 OPENAI_BASE_URL / OPENAI_API_KEY，页面能打开但生成报告会返回 409"
  warn "编辑 $ENVFILE 填入端点与模型，或在页面「个人设置」里配置后再用"
fi

# ---------- 4. 前端 ----------
DIST="$ROOT/frontend/dist"
need_build=0
if [[ ! -f "$DIST/index.html" ]]; then
  need_build=1
elif [[ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" "$ROOT/frontend/package.json" \
      "$ROOT/frontend/vite.config.js" -newer "$DIST/index.html" -print -quit 2>/dev/null)" ]]; then
  need_build=1  # 源码 / 构建配置比产物新
fi

if [[ "$need_build" == 1 ]]; then
  command -v npm >/dev/null 2>&1 || die "需要 Node 18+ 来构建前端（https://nodejs.org/）"
  # node_modules 缺失，或 package.json 比上次安装记录新（新增了依赖）
  if [[ ! -d "$ROOT/frontend/node_modules" ]] \
     || [[ "$ROOT/frontend/package.json" -nt "$ROOT/frontend/node_modules/.package-lock.json" ]]; then
    info "安装前端依赖"
    (cd "$ROOT/frontend" && npm install --no-fund --no-audit) || die "npm install 失败"
  fi
  info "构建前端（npm run build）"
  (cd "$ROOT/frontend" && npm run build) || die "前端构建失败"
  ok "前端产物已更新：frontend/dist"
else
  info "前端产物是最新的"
fi

# ---------- 5. 端口 ----------
if [[ -n "$(port_pid "${PORT}")" ]]; then
  die "端口 ${PORT} 被其它进程占用（PID $(port_pid "${PORT}")）。改 backend/.env 里的 PORT，或先停掉它"
fi

# ---------- 6. 启动 ----------
mkdir -p "$ROOT/logs" "$ROOT/local"
if [[ -f "${LOGFILE}" ]] && [[ $(wc -c <"${LOGFILE}" | tr -d ' ') -gt 10485760 ]]; then
  mv -f "${LOGFILE}" "${LOGFILE}.1"   # 超过 10 MB 轮转一份，避免日志无限长
fi

info "启动服务（后台，日志 ${LOGFILE}）"
PYTHONUNBUFFERED=1 nohup "$PY" "$ROOT/backend/main.py" >>"${LOGFILE}" 2>&1 &
echo $! >"${PIDFILE}"

printf '\033[36m▸\033[0m 等待服务就绪'
ready=0
for _ in $(seq 1 60); do
  if healthy; then ready=1; break; fi
  kill -0 "$(cat "${PIDFILE}")" 2>/dev/null || break   # 进程已退出，不必再等
  printf '.'; sleep 1
done
printf '\n'

if [[ "$ready" != 1 ]]; then
  rm -f "${PIDFILE}"
  warn "启动失败，最后 20 行日志："
  tail -20 "${LOGFILE}" 2>/dev/null || true
  die "请按上面的报错处理；日志全文：${LOGFILE}"
fi

ok "OcuPanel 已启动 → ${BASE_URL}（PID $(cat "${PIDFILE}")）"
info "首次生成报告需加载向量模型，可能多等 30–60 秒"
info "状态：bash status.sh   停止：bash stop.sh"

open_browser "${BASE_URL}"
