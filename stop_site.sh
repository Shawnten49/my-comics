#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$SCRIPT_DIR/run"
PID_FILE="$RUN_DIR/site.pid"
HOST="127.0.0.1"
PORT="8767"
force=0
previous=""

for argument in "$@"; do
  if [[ "$argument" == "--force" ]]; then
    force=1
  elif [[ "$previous" == "--host" ]]; then
    HOST="$argument"
  elif [[ "$previous" == "--port" ]]; then
    PORT="$argument"
  elif [[ "$argument" == --host=* ]]; then
    HOST="${argument#--host=}"
  elif [[ "$argument" == --port=* ]]; then
    PORT="${argument#--port=}"
  fi
  previous="$argument"
done

access_host="$HOST"
if [[ "$HOST" == "0.0.0.0" ]]; then
  access_host="127.0.0.1"
elif [[ "$HOST" == "::" ]]; then
  access_host="::1"
fi
health_url="http://$access_host:$PORT/api/health"
if [[ -f "$RUN_DIR/site.url" ]]; then
  health_url="$(cat "$RUN_DIR/site.url")api/health"
fi
curl_flags=(-fsS --max-time 2)
if [[ "$health_url" == https://* ]]; then
  curl_flags+=(-k)
fi

site_pid=""
if [[ -f "$PID_FILE" ]]; then
  candidate_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$candidate_pid" =~ ^[0-9]+$ ]] \
    && kill -0 "$candidate_pid" 2>/dev/null; then
    site_pid="$candidate_pid"
  fi
fi

if [[ -z "$site_pid" ]]; then
  health_response="$(curl "${curl_flags[@]}" "$health_url" 2>/dev/null || true)"
  if [[ "$health_response" == *'"manage_migrations"'* ]]; then
    recovered_pid="$(printf '%s' "$health_response" | sed -nE 's/.*"pid":([0-9]+).*/\1/p')"
    if [[ "$recovered_pid" =~ ^[0-9]+$ ]]; then
      mkdir -p "$RUN_DIR"
      echo "$recovered_pid" >"$PID_FILE"
      site_pid="$recovered_pid"
      echo "已从运行中的服务恢复 PID：$site_pid"
    fi
  fi
fi

if [[ -z "$site_pid" ]]; then
  port_pid="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
  if [[ "$port_pid" =~ ^[0-9]+$ ]]; then
    port_command="$(ps -p "$port_pid" -o command= 2>/dev/null || true)"
    if [[ "$port_command" == *"comic_web/app.py"* ]]; then
      mkdir -p "$RUN_DIR"
      echo "$port_pid" >"$PID_FILE"
      site_pid="$port_pid"
      echo "已从监听端口恢复 PID：$site_pid"
    fi
  fi
fi

if [[ -z "$site_pid" ]]; then
  if [[ -f "$PID_FILE" ]]; then
    invalid_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ ! "$invalid_pid" =~ ^[0-9]+$ ]]; then
      echo "PID 文件内容无效，已清理：$PID_FILE" >&2
    else
      echo "进程 $invalid_pid 已不存在，清理 PID 文件。"
    fi
    rm -f "$PID_FILE"
  else
    echo "漫画网站未运行：没有找到 run/site.pid，也没有检测到本地服务。"
  fi
  exit 0
fi

command_line="$(ps -p "$site_pid" -o command= 2>/dev/null || true)"
if [[ "$command_line" != *"comic_web/app.py"* ]]; then
  echo "拒绝停止 PID $site_pid：它不再是漫画网站进程。" >&2
  echo "进程命令：$command_line" >&2
  rm -f "$PID_FILE"
  exit 1
fi

echo "正在停止漫画网站，PID：$site_pid"
kill "$site_pid" 2>/dev/null || true

for _ in {1..240}; do
  if ! kill -0 "$site_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "漫画网站已停止。"
    exit 0
  fi
  sleep 0.25
done

if [[ "$force" -eq 1 ]]; then
  echo "进程未在 60 秒内退出，按 --force 要求强制终止。" >&2
  kill -9 "$site_pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "漫画网站已强制停止。未完成的迁移会在下次启动时保守恢复。"
  exit 0
fi

echo "进程仍在安全停止迁移任务，PID 文件已保留：$PID_FILE" >&2
echo "可以继续等待后再次执行 ./stop_site.sh。" >&2
echo "只有确认可以强制中断时才使用：./stop_site.sh --force" >&2
exit 1
