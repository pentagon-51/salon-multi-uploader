"""
익명 활성 사용자(DAU/MAU) 집계 모듈 (서버 비용 0원 · 구글 시트 Apps Script 웹앱 연동)
- 개인정보(아이디, 비밀번호, IP, 매장명, 업로드 내용 등)는 절대 수집하지 않습니다.
- 전송 항목은 무작위 UUID, OS(macOS/Windows), 프로그램 버전 3가지만 하루 최대 1회 비동기 전송합니다.
- TELEMETRY_ENDPOINT_URL이 비어 있거나 네트워크 오류/타임아웃(3초) 발생 시 조용히 건너뛰며
  프로그램 실행 속도나 업로드 기능에 어떠한 영향도 주지 않습니다.
"""

import os
import sys
import json
import uuid
import datetime
import threading
import urllib.request

from os_utils import get_auth_dir, get_base_dir

# Google Apps Script 웹 앱 배포 후 발급된 URL을 아래에 입력하세요. (비어 있으면 전송을 조용히 건너뜁니다)
TELEMETRY_ENDPOINT_URL = ""

TELEMETRY_TIMEOUT_SEC = 3.0


def _get_anonymous_id_path():
    return os.path.join(get_auth_dir(), "anonymous_id.txt")


def _get_last_telemetry_date_path():
    return os.path.join(get_auth_dir(), "last_telemetry_date.txt")


def get_or_create_anonymous_id():
    """개인정보가 전혀 없는 무작위 UUID를 auth_data/anonymous_id.txt에 1회만 생성·보관."""
    path = _get_anonymous_id_path()
    try:
        if os.path.isfile(path):
            existing = open(path, "r", encoding="utf-8").read().strip()
            if existing and len(existing) >= 8:
                return existing
    except Exception:
        pass

    new_id = str(uuid.uuid4())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_id)
    except Exception:
        pass
    return new_id


def _get_os_label():
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    return "Other"


def _get_app_version():
    version_path = os.path.join(get_base_dir(), "version.json")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ver = str(data.get("version") or "").strip()
        if ver:
            return ver
    except Exception:
        pass
    return "0.0.0"


def _has_reported_today(today_str):
    path = _get_last_telemetry_date_path()
    try:
        if os.path.isfile(path):
            return open(path, "r", encoding="utf-8").read().strip() == today_str
    except Exception:
        pass
    return False


def _mark_reported_today(today_str):
    path = _get_last_telemetry_date_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(today_str)
    except Exception:
        pass


def send_daily_active_ping(endpoint_url=None, force=False):
    """하루 최대 1회 익명 활성 사용자 핑(uuid, os, version) 전송. 모든 예외는 안전하게 무시."""
    try:
        target_url = str(endpoint_url if endpoint_url is not None else TELEMETRY_ENDPOINT_URL).strip()
        if not target_url:
            return False

        today_str = datetime.date.today().isoformat()
        if (not force) and _has_reported_today(today_str):
            return False

        payload = {
            "uuid": get_or_create_anonymous_id(),
            "os": _get_os_label(),
            "version": _get_app_version(),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            target_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TELEMETRY_TIMEOUT_SEC) as resp:
            status = getattr(resp, "status", 200)
            if 200 <= int(status) < 400:
                _mark_reported_today(today_str)
                return True
    except Exception:
        return False
    return False


def report_daily_active_async(endpoint_url=None):
    """서버 시작 시 메인 스레드를 전혀 지연시키지 않도록 데몬 스레드에서 1회 비동기 호출."""
    try:
        t = threading.Thread(
            target=send_daily_active_ping,
            kwargs={"endpoint_url": endpoint_url},
            daemon=True,
            name="telemetry-daily-ping",
        )
        t.start()
        return t
    except Exception:
        return None
