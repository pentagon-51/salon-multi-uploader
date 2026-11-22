#!/bin/bash
cd "$(dirname "$0")" || exit 1

echo ""
echo "================================================================"
echo "  [미용실 멀티업로더] 실행 중..."
echo "================================================================"
echo ""

find_python() {
  for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3 /usr/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1; then
        command -v "$cand"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python)"
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 파이썬이 설치되어 있지 않습니다. 먼저 [2-1. 맥북 최초설치.command]를 실행해 주세요."
  read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

# 기존에 실행 중인 8765 포트 서버가 있으면 안전 정리
OLD_PIDS="$(lsof -ti :8765 2>/dev/null || true)"
if [ -n "$OLD_PIDS" ]; then
  echo "🔄 기존 실행 중인 미용실 멀티업로더를 정리하고 다시 시작합니다..."
  kill $OLD_PIDS >/dev/null 2>&1 || true
  sleep 1
fi

# 백그라운드 서버 시작
nohup "$PYTHON_BIN" "app_server.py" > "app.log" 2>&1 &
SERVER_PID=$!

# 서버 준비 대기 (최대 10초)
READY=0
for i in $(seq 1 30); do
  if curl -sSf "http://127.0.0.1:8765/api/status" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.35
done

if [ "$READY" -ne 1 ]; then
  echo "❌ 프로그램 시작에 실패했습니다. 아래 최근 로그를 확인해 주세요:"
  echo "----------------------------------------------------------------"
  tail -n 20 "app.log" 2>/dev/null || true
  echo "----------------------------------------------------------------"
  read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

open "http://127.0.0.1:8765"
echo "✅ 미용실 멀티업로더가 브라우저에서 열렸습니다! (http://127.0.0.1:8765)"
echo "⏳ 2초 후 창이 자동으로 닫힙니다..."
sleep 2
nohup sh -c 'sleep 0.5; osascript -e "tell application \"Terminal\" to close front window"' >/dev/null 2>&1 &
exit 0
