#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
RUN_DIR="$SCRIPT_DIR/run"
PID_FILE="$RUN_DIR/site.pid"
LOG_FILE="$RUN_DIR/site.log"
mkdir -p "$RUN_DIR"

HOST="127.0.0.1"
PORT="8767"
LAN=0
LAN_ADDRESS=""
LAN_ALLOW_HTTP=0
TLS_CERT=""
TLS_KEY=""
previous=""
for argument in "$@"; do
  if [[ "$argument" == "--lan" ]]; then
    LAN=1
  elif [[ "$argument" == "--lan-allow-http" ]]; then
    LAN_ALLOW_HTTP=1
  elif [[ "$previous" == "--host" ]]; then
    HOST="$argument"
  elif [[ "$previous" == "--port" ]]; then
    PORT="$argument"
  elif [[ "$argument" == --host=* ]]; then
    HOST="${argument#--host=}"
  elif [[ "$argument" == --port=* ]]; then
    PORT="${argument#--port=}"
  elif [[ "$previous" == "--lan-address" ]]; then
    LAN_ADDRESS="$argument"
  elif [[ "$argument" == --lan-address=* ]]; then
    LAN_ADDRESS="${argument#--lan-address=}"
  elif [[ "$previous" == "--tls-cert" ]]; then
    TLS_CERT="$argument"
  elif [[ "$argument" == --tls-cert=* ]]; then
    TLS_CERT="${argument#--tls-cert=}"
  elif [[ "$previous" == "--tls-key" ]]; then
    TLS_KEY="$argument"
  elif [[ "$argument" == --tls-key=* ]]; then
    TLS_KEY="${argument#--tls-key=}"
  fi
  previous="$argument"
done

if [[ "$LAN" -eq 1 ]]; then
  if [[ -z "$LAN_ADDRESS" ]]; then
    LAN_ADDRESS="$(python3 -c 'from comic_web.app import detect_lan_ip; print(detect_lan_ip())')"
  fi
  HOST="$LAN_ADDRESS"
  if [[ -n "$TLS_CERT" || -n "$TLS_KEY" || "$LAN_ALLOW_HTTP" -eq 0 ]]; then
    SCHEME="https"
  else
    SCHEME="http"
  fi
else
  SCHEME="http"
fi

access_host="$HOST"
if [[ "$HOST" == "0.0.0.0" ]]; then
  access_host="127.0.0.1"
elif [[ "$HOST" == "::" ]]; then
  access_host="::1"
fi
health_url="$SCHEME://$access_host:$PORT/api/health"
curl_flags=(-fsS --max-time 2)
if [[ "$SCHEME" == "https" ]]; then
  curl_flags+=(-k)
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
    existing_command="$(ps -p "$existing_pid" -o command= 2>/dev/null || true)"
    if [[ "$existing_command" == *"comic_web/app.py"* ]]; then
      existing_health="$(curl "${curl_flags[@]}" "$health_url" 2>/dev/null || true)"
      if [[ -n "$existing_health" ]]; then
        echo "漫画网站已经在运行，PID：$existing_pid"
        echo "访问地址：$SCHEME://$HOST:$PORT/"
        exit 0
      fi
      echo "已有漫画网站进程运行，但当前模式或地址不匹配。请先执行 ./stop_site.sh。" >&2
      exit 1
    fi
  fi
  rm -f "$PID_FILE"
fi

health_response="$(curl "${curl_flags[@]}" "$health_url" 2>/dev/null || true)"
if [[ "$health_response" == *'"manage_migrations"'* ]] \
  && [[ "$health_response" == *'"download_management"'* ]] \
  && [[ "$health_response" == *'"batch_download"'* ]] \
  && [[ "$health_response" == *'"incremental_pull"'* ]] \
  && [[ "$health_response" == *'"deletion_management"'* ]] \
  && [[ "$health_response" == *'"auth_roles"'* ]] \
  && [[ "$health_response" == *'"series_chapters"'* ]] \
  && [[ "$health_response" == *'"system_settings"'* ]]; then
  existing_pid="$(printf '%s' "$health_response" | sed -nE 's/.*"pid":([0-9]+).*/\1/p')"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]]; then
    echo "$existing_pid" >"$PID_FILE"
  fi
  echo "漫画网站已经在运行，PID：${existing_pid:-未知}"
  echo "访问地址：$SCHEME://$HOST:$PORT/"
  printf '%s://%s:%s/\n' "$SCHEME" "$HOST" "$PORT" >"$RUN_DIR/site.url"
  exit 0
fi

port_pid="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
if [[ -n "$port_pid" ]]; then
  port_command="$(ps -p "$port_pid" -o command= 2>/dev/null || true)"
  echo "无法启动：端口 $PORT 已被其他进程占用，PID：$port_pid" >&2
  echo "进程命令：$port_command" >&2
  echo "请先关闭占用端口的进程，或使用 ./start_site.sh --port 其他端口。" >&2
  exit 1
fi

cd "$SCRIPT_DIR"
echo "正在启动本地漫画网站……"
PYTHONUNBUFFERED=1 nohup python3 comic_web/app.py "$@" >>"$LOG_FILE" 2>&1 &
site_pid=$!
disown "$site_pid" 2>/dev/null || true
echo "$site_pid" >"$PID_FILE"

ready=0
for _ in {1..60}; do
  if ! kill -0 "$site_pid" 2>/dev/null; then
    break
  fi
    health_response="$(curl "${curl_flags[@]}" "$health_url" 2>/dev/null || true)"
  if [[ "$health_response" == *"\"pid\":$site_pid"* ]] \
    && [[ "$health_response" == *'"manage_migrations"'* ]] \
    && [[ "$health_response" == *'"download_management"'* ]] \
    && [[ "$health_response" == *'"batch_download"'* ]] \
    && [[ "$health_response" == *'"incremental_pull"'* ]] \
    && [[ "$health_response" == *'"deletion_management"'* ]] \
    && [[ "$health_response" == *'"auth_roles"'* ]] \
    && [[ "$health_response" == *'"series_chapters"'* ]] \
    && [[ "$health_response" == *'"system_settings"'* ]]; then
    ready=1
    break
  fi
  sleep 0.5
done

if [[ "$ready" -ne 1 ]]; then
  echo "网站启动失败。最近日志：" >&2
  tail -n 30 "$LOG_FILE" >&2 || true
  if kill -0 "$site_pid" 2>/dev/null; then
    kill "$site_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  exit 1
fi

if [[ "$LAN" -eq 1 ]]; then
  local_url="http://127.0.0.1:$PORT/"
  local_health="$(curl -fsS --max-time 2 "${local_url}api/health" 2>/dev/null || true)"
  if [[ -z "$local_health" ]]; then
    echo "局域网服务已启动，但本机回环地址健康检查失败。" >&2
    echo "请检查日志：$LOG_FILE" >&2
  else
    printf '%s\n' "$local_url" >"$RUN_DIR/site.local.url"
  fi
fi

echo "网站已启动，PID：$site_pid"
echo "访问地址：$SCHEME://$HOST:$PORT/"
if [[ "$LAN" -eq 1 ]]; then
  echo "本机访问：http://127.0.0.1:$PORT/"
fi
printf '%s://%s:%s/\n' "$SCHEME" "$HOST" "$PORT" >"$RUN_DIR/site.url"
echo "日志文件：$LOG_FILE"
