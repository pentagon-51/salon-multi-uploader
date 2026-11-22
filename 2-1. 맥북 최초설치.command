#!/bin/bash
cd "$(dirname "$0")" || exit 1

echo ""
echo "================================================================"
echo "  [미용실 멀티업로더] 맥북 최초 설치"
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
  echo "❌ 파이썬(Python 3.9 이상)이 설치되어 있지 않습니다."
  echo ""
  echo "💡 아래 공식 페이지에서 macOS용 Python을 설치하신 뒤 이 파일을 다시 실행해 주세요."
  echo "   https://www.python.org/downloads/macos/"
  echo ""
  read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

echo "✅ 파이썬 확인 완료: $PYTHON_BIN"
mkdir -p auth_data upload done
chmod +x "2-2. 맥북 프로그램 실행.command" >/dev/null 2>&1 || true

echo ""
echo "[1/3] 필요한 프로그램 구성 요소를 설치하는 중입니다... (1~3분 소요)"
PIP_PKGS="playwright opencv-python pillow pyperclip google-api-python-client google-auth-oauthlib"

"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true
if ! "$PYTHON_BIN" -m pip install --upgrade $PIP_PKGS; then
  echo "↪ macOS 환경 옵션(--break-system-packages)으로 재시도합니다..."
  if ! "$PYTHON_BIN" -m pip install --break-system-packages --upgrade $PIP_PKGS; then
    echo ""
    echo "❌ 필요한 구성 요소 설치에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요."
    read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
    exit 1
  fi
fi

echo ""
echo "[2/3] 자동 업로드용 전용 브라우저를 설치하는 중입니다..."
if ! "$PYTHON_BIN" -m playwright install chromium; then
  echo ""
  echo "❌ 전용 브라우저 설치에 실패했습니다. 네트워크 상태를 확인한 뒤 다시 실행해 주세요."
  read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

echo ""
echo "[3/3] 설치 완료 최종 확인 중..."
if ! "$PYTHON_BIN" -c "import playwright, cv2, PIL, pyperclip, googleapiclient"; then
  echo ""
  echo "❌ 설치 검증에 실패했습니다. 위 오류 내용을 확인한 뒤 다시 실행해 주세요."
  read -r -p "내용을 확인한 뒤 엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

echo ""
echo "================================================================"
echo "  🎉 미용실 멀티업로더 설치가 모두 완료되었습니다!"
echo "  이제 폴더 안의 [2-2. 맥북 프로그램 실행.command]를 더블클릭해 시작하세요."
echo "================================================================"
echo ""
read -r -p "엔터를 누르면 창이 닫힙니다..."
exit 0
