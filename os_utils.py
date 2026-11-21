import os
import re
import sys
import json
import glob
import time
import datetime
import shutil
import threading
import traceback
import subprocess

MOD_KEY = "Meta" if sys.platform == "darwin" else "Control"

# 오픈소스 미용실 범용 기본값 (실제 매장명·주소·지역은 auth_data/user_profile.json에서 동적 로드)
BRANCH_NAME = ""
BRANCH_ADDRESS = ""

ALL_PLATFORM_KEYS = ("yt", "naver", "clip", "tt", "ig", "pin", "threads", "daangn", "x")


def normalize_branch_name(text, profile=None):
    """문자열 공백 정리 헬퍼 (기존 호출 호환 유지; 특정 매장명 하드코딩 없음)"""
    if text is None:
        return ""
    return str(text)


COMMON_BANNED_TAGS = {
    "shorts",
    "쇼츠",
}

EMPTY_PROFILE_TEMPLATE = {
    "enabled_platforms": list(ALL_PLATFORM_KEYS),
    "salon_name": "",
    "region_keyword": "",
    "designer_name": "",
    "specialty": "",
    "booking_url": "",
    "naver_booking_url": "",
    "blog_categories": [],
    "naver_blog_cat_men": "",
    "naver_blog_cat_women": "",
    "pinterest_board": "",
    "naver_blog_id": "",
    "pinterest_id": "",
    "place_search_keyword": "",
    "personal_banned_tags": "",
    "salon_address": "",
    "location_guide": "",
    "parking_guide": "",
    "business_hours": "",
    "prompt_mode": "standard",
    "custom_style_guide": "",
    "onboarding_checks": {},
    "prompt_copied": False,
    "onboarding_step": 0,
    "onboarding_completed": False,
    "is_configured": False,
}

LEGACY_AUTH_EXACT_FILES = (
    "youtube_token.json",
    "client_secret.json",
    "user_profile.json",
    "daily_usage.json",
    "login_credentials.json",
)


def get_base_dir():
    """환경변수(HOME 등)에 의존하지 않고 현재 프로젝트 루트 폴더 경로를 반환"""
    return os.path.dirname(os.path.abspath(__file__))


def get_auth_dir():
    """인증·쿠키·개인설정 전용 폴더(auth_data/) 경로 반환 및 자동 생성"""
    path = os.path.join(get_base_dir(), "auth_data")
    os.makedirs(path, exist_ok=True)
    return path


def get_chrome_profile_dir():
    """영구 전용 크롬 프로필 폴더(auth_data/chrome_profile) 경로 반환 및 자동 생성"""
    path = os.path.join(get_auth_dir(), "chrome_profile")
    os.makedirs(path, exist_ok=True)
    return path


def cleanup_chrome_profile_locks():
    """이전 강제 종료로 남을 수 있는 auth_data/chrome_profile 내 Singleton* 잠금 파일 안전 삭제"""
    profile_dir = get_chrome_profile_dir()
    removed = []
    for fname in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        fp = os.path.join(profile_dir, fname)
        if os.path.lexists(fp):
            try:
                os.remove(fp)
                removed.append(fname)
            except Exception:
                pass
    return removed


def launch_persistent_chromium(
    playwright_obj,
    headless=False,
    viewport=None,
    record_video_dir=None,
    record_video_size=None,
    locale="ko-KR",
    args=None,
):
    """전용 영구 크롬 프로필(auth_data/chrome_profile)로 Chrome 채널 우선 실행, 불가 시 기본 Chromium 폴백"""
    cleanup_chrome_profile_locks()
    launch_args = list(args) if args is not None else []
    if "--disable-blink-features=AutomationControlled" not in launch_args:
        launch_args.append("--disable-blink-features=AutomationControlled")

    kwargs = {
        "user_data_dir": get_chrome_profile_dir(),
        "headless": headless,
        "args": launch_args,
    }
    if locale:
        kwargs["locale"] = locale
    if viewport is not None:
        kwargs["viewport"] = viewport
    else:
        kwargs["no_viewport"] = True
    if record_video_dir:
        kwargs["record_video_dir"] = record_video_dir
        if record_video_size:
            kwargs["record_video_size"] = record_video_size

    try:
        ctx = playwright_obj.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception as e:
        print(f"ℹ️ [브라우저] Chrome 채널 영구 프로필 실행 불가({e}) → 기본 Chromium으로 자동 전환합니다.", flush=True)
        ctx = playwright_obj.chromium.launch_persistent_context(**kwargs)
    try:
        attach_diagnostics_tracker(ctx)
    except Exception:
        pass
    return ctx


PERSISTENT_COOKIE_RULES = (
    ("naver_cookies.json", ("naver.com",), {"nid_aut", "nid_ses"}),
    ("tiktok_cookies.json", ("tiktok.com",), {"sessionid", "sessionid_ss", "sid_tt", "uid_tt", "uid_tt_ss", "sid_guard"}),
    ("instagram_cookies.json", ("instagram.com",), {"sessionid", "ds_user_id"}),
    ("pinterest_cookies.json", ("pinterest.com",), {"_pinterest_sess", "_auth"}),
    ("threads_cookies.json", ("threads.com", "threads.net"), {"sessionid", "ds_user_id"}),
    (
        "daangn_cookies.json",
        ("daangn.com", "karrotmarket.com"),
        {
            "_daangn_session",
            "daangn_session",
            "karrot_session",
            "biz_session",
            "biz_access_token",
            "access_token",
            "refresh_token",
            "b_token",
            "daangn_auth",
            "karrot_auth",
        },
    ),
    ("x_cookies.json", ("x.com", "twitter.com"), {"auth_token", "twid"}),
)

DAANGN_VISITOR_COOKIE_PREFIXES = ("ch-session", "ch-veil", "_ga", "_gcl", "_fbp", "_clck", "_clsk", "_dd_", "_interaction")
DAANGN_VISITOR_COOKIE_EXACT = {"karrot_user_device_id", "device_id", "accounts_sid", "_kmpid"}


def _domain_matches(cookie_domain, target_domains):
    dom = (cookie_domain or "").lstrip(".").lower()
    if not dom:
        return False
    for td in target_domains:
        t = td.lstrip(".").lower()
        if dom == t or dom.endswith("." + t):
            return True
    return False


def _is_cookie_active(cookie_obj, now_ts=None):
    """쿠키 값이 비어있지 않고 만료되지 않았는지 판별 (세션 쿠키 expires<=0은 유효로 간주)"""
    if not isinstance(cookie_obj, dict):
        return False
    val = str(cookie_obj.get("value") or "").strip()
    if not val:
        return False
    exp = cookie_obj.get("expires")
    if exp is not None:
        try:
            exp_f = float(exp)
            if exp_f > 0 and exp_f < (now_ts if now_ts is not None else time.time()):
                return False
        except Exception:
            pass
    return True


def _has_logged_in_session_cookie(filename, matched_cookies, session_names, auth_verified=False):
    """각 플랫폼의 비로그인 방문자 쿠키를 제외하고 실제 로그인 인증 쿠키·세션 토큰 보유 여부만 정밀 판별"""
    if not matched_cookies:
        return False

    now_ts = time.time()
    active_by_name = {}
    for c in matched_cookies:
        if not _is_cookie_active(c, now_ts=now_ts):
            continue
        cname = (c.get("name") or "").strip().lower()
        cval = str(c.get("value") or "").strip()
        cdom = (c.get("domain") or "").lstrip(".").lower()
        if cname:
            active_by_name.setdefault(cname, []).append((cval, cdom))

    if not active_by_name:
        return False

    # 1) 네이버 블로그/클립: NID_AUT + NID_SES 모두 존재해야 실제 로그인 세션
    if filename == "naver_cookies.json":
        has_aut = any(len(v) >= 8 for v, _ in active_by_name.get("nid_aut", []))
        has_ses = any(len(v) >= 8 for v, _ in active_by_name.get("nid_ses", []))
        return has_aut and has_ses

    # 2) 인스타그램: sessionid(>=10) + ds_user_id(숫자) 모두 존재 (방문자 csrftoken/mid/ig_did만 있으면 False)
    if filename == "instagram_cookies.json":
        has_sid = any(len(v) >= 10 for v, _ in active_by_name.get("sessionid", []))
        has_uid = any(v.isdigit() and len(v) >= 3 for v, _ in active_by_name.get("ds_user_id", []))
        return has_sid and has_uid

    # 3) 쓰레드: threads.com / threads.net 도메인에 sessionid(>=10) + ds_user_id(숫자) 모두 존재하거나 브라우저 실로그인 검증됨
    if filename == "threads_cookies.json":
        if auth_verified:
            return True
        has_sid = any(len(v) >= 10 for v, _ in active_by_name.get("sessionid", []))
        has_uid = any(v.isdigit() and len(v) >= 3 for v, _ in active_by_name.get("ds_user_id", []))
        return has_sid and has_uid

    # 4) 틱톡: 세션 쿠키(sessionid/sessionid_ss/sid_tt) + 계정 식별 쿠키(uid_tt/uid_tt_ss/sid_guard) 모두 존재
    if filename == "tiktok_cookies.json":
        has_sid = any(
            len(v) >= 10
            for key in ("sessionid", "sessionid_ss", "sid_tt")
            for v, _ in active_by_name.get(key, [])
        )
        has_uid = any(
            len(v) >= 5
            for key in ("uid_tt", "uid_tt_ss", "sid_guard")
            for v, _ in active_by_name.get(key, [])
        )
        return has_sid and has_uid

    # 5) 핀터레스트: _auth == "1" 이고 _pinterest_sess(>=16) 모두 존재 (_auth == "0"이면 비로그인)
    if filename == "pinterest_cookies.json":
        auth_vals = [v for v, _ in active_by_name.get("_auth", [])]
        if "0" in auth_vals and "1" not in auth_vals:
            return False
        has_auth1 = "1" in auth_vals
        has_sess = any(len(v) >= 16 for v, _ in active_by_name.get("_pinterest_sess", []))
        return has_auth1 and has_sess

    # 6) 당근 비즈: 방문자·로그인화면 임시 쿠키(ch-session-*, karrot_user_device_id, accounts_sid, _interaction* 등) 완전 배제!
    #    실제 비즈 대시보드 인증 쿠키(>=12자, accounts.daangn.com 제외)가 있거나,
    #    브라우저에서 실제 대시보드 진입(auth_verified=True)이 확인된 경우만 True
    if filename == "daangn_cookies.json":
        if auth_verified:
            return True
        for cname, entries in active_by_name.items():
            if cname in DAANGN_VISITOR_COOKIE_EXACT or any(cname.startswith(px) for px in DAANGN_VISITOR_COOKIE_PREFIXES):
                continue
            for cval, cdom in entries:
                if cdom == "accounts.daangn.com" or cdom.startswith("accounts."):
                    continue
                if cname in session_names and len(cval) >= 12:
                    return True
        return False

    # 7) 엑스(X): auth_token(>=10) + twid(>=3) 모두 존재 (guest_id/gt/__cf_bm만 있으면 False)
    if filename == "x_cookies.json":
        has_tok = any(len(v) >= 10 for v, _ in active_by_name.get("auth_token", []))
        has_twid = any(len(v) >= 3 for v, _ in active_by_name.get("twid", []))
        return has_tok and has_twid

    for cname in session_names:
        if active_by_name.get(cname):
            return True
    return False


def check_cookie_login_redirect(filename, path, timeout=3.5):
    """저장된 쿠키로 보호 URL에 HTTP 요청을 보내 로그인 페이지로 리다이렉트되는지 1회 정밀 확인.
    네트워크 불가·오류 시 None 반환(기존 쿠키 판정 유지), 로그인 페이지 리다이렉트 감지 시 False, 통과 시 True.
    (당근 비즈는 SPA/브라우저 OAuth 세션 구조 특성상 단순 urllib 요청에서 오판·쿠키 삭제 위험이 있으므로
    브라우저 실검증 결과(auth_verified)와 쿠키 정밀 검사를 신뢰하고 None 반환)"""
    import urllib.request
    import urllib.error

    if filename == "daangn_cookies.json":
        return None

    check_specs = {
        "naver_cookies.json": (
            "https://blog.naver.com/GoBlogWrite.naver",
            ("nid.naver.com/nidlogin", "nidlogin.login"),
        ),
    }
    spec = check_specs.get(filename)
    if not spec or not path or not os.path.exists(path):
        return None
    target_url, login_markers = spec
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = data.get("cookies") or []
        now_ts = time.time()
        pairs = []
        for c in cookies:
            if not _is_cookie_active(c, now_ts=now_ts):
                continue
            name = (c.get("name") or "").strip()
            val = str(c.get("value") or "").strip()
            if name and val:
                pairs.append(f"{name}={val}")
        if not pairs:
            return False

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(_NoRedirect())
        req = urllib.request.Request(
            target_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Cookie": "; ".join(pairs),
            },
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                final_url = (resp.geturl() or "").lower()
                if any(mk in final_url for mk in login_markers):
                    return False
                body_head = resp.read(4096).decode("utf-8", errors="ignore").lower()
                if filename == "naver_cookies.json" and "nidlogin" in body_head:
                    return False
                return True
        except urllib.error.HTTPError as he:
            if he.code in (301, 302, 303, 307, 308):
                loc = (he.headers.get("Location") or "").lower()
                if any(mk in loc for mk in login_markers):
                    return False
                return True
            if he.code in (401, 403):
                return False
            return None
    except Exception:
        return None


def is_platform_cookie_authenticated(filename, path, auto_clean=False):
    """저장된 플랫폼 쿠키 JSON 파일이 단순 방문자 쿠키가 아닌 실제 로그인 인증 세션을 보유하고 있는지 정밀 검사"""
    if not path or not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= 10:
            raise ValueError("cookie file is empty")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("cookie root is not dict")
        cookies = data.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            return False
        auth_verified = bool(data.get("auth_verified", False))
        rule = next((r for r in PERSISTENT_COOKIE_RULES if r[0] == filename), None)
        if not rule:
            return len(cookies) > 0
        _, domains, session_names = rule
        matched = [c for c in cookies if _domain_matches(c.get("domain"), domains)]
        return _has_logged_in_session_cookie(filename, matched, session_names, auth_verified=auth_verified)
    except Exception:
        if auto_clean:
            try:
                os.remove(path)
            except Exception:
                pass
        return False


def sync_cookies_from_persistent_context(context, verified_platforms=None, checked_filenames=None):
    """열려 있는 persistent context에서 플랫폼별 실제 로그인 인증 쿠키만 추출해 auth_data/<플랫폼>_cookies.json에 원자 저장.
    비로그인 방문자 쿠키만 있는 경우 가짜 쿠키를 저장하지 않으며, checked_filenames 대상 중 비로그인 상태면 기존 잔여 파일도 정리."""
    if context is None:
        return []
    try:
        all_cookies = context.cookies()
    except Exception:
        return []
    if not isinstance(all_cookies, list):
        return []

    verified_set = set(verified_platforms or ())
    checked_set = set(checked_filenames or ())
    synced = []

    for filename, domains, session_names in PERSISTENT_COOKIE_RULES:
        matched = [c for c in all_cookies if _domain_matches(c.get("domain"), domains)]
        is_verified = filename in verified_set
        is_logged_in = _has_logged_in_session_cookie(filename, matched, session_names, auth_verified=is_verified)
        dst_path = get_auth_file(filename)

        if not is_logged_in:
            # 이번에 직접 점검한 플랫폼인데 비로그인 상태라면 가짜/만료 쿠키 파일이 남아 초록불이 뜨지 않도록 정리
            if filename in checked_set and os.path.exists(dst_path):
                try:
                    os.remove(dst_path)
                except Exception:
                    pass
            continue

        tmp_path = f"{dst_path}.tmp"
        payload = {"cookies": matched, "origins": []}
        if is_verified:
            payload["auth_verified"] = True
            payload["verified_at"] = int(time.time())
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            if is_valid_cookie_file(tmp_path, auto_clean=False):
                os.replace(tmp_path, dst_path)
                synced.append(filename)
            elif os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    return synced


def get_blackbox_login_dir():
    """로그인 점검·수동 로그인 전용 블랙박스 폴더(blackbox_login/) 경로 반환 및 자동 생성"""
    path = os.path.join(get_base_dir(), "blackbox_login")
    os.makedirs(path, exist_ok=True)
    return path


def get_blackbox_upload_dir():
    """실제 업로드 전용 블랙박스 폴더(blackbox_upload/) 경로 반환 및 자동 생성"""
    path = os.path.join(get_base_dir(), "blackbox_upload")
    os.makedirs(path, exist_ok=True)
    return path


def get_blackbox_dir(mode="upload"):
    """mode('login' | 'upload')에 따른 블랙박스 폴더 경로 반환 및 자동 생성"""
    if str(mode or "upload").strip().lower() == "login":
        return get_blackbox_login_dir()
    return get_blackbox_upload_dir()


def migrate_legacy_auth_files():
    """루트에 남아 있는 기존 쿠키·토큰·개인설정 파일을 auth_data/로 안전 자동 이관"""
    base_dir = get_base_dir()
    auth_dir = get_auth_dir()
    candidates = [os.path.join(base_dir, fn) for fn in LEGACY_AUTH_EXACT_FILES]
    candidates.extend(glob.glob(os.path.join(base_dir, "*_cookies.json")))

    moved = []
    for src in candidates:
        if not os.path.isfile(src):
            continue
        fname = os.path.basename(src)
        dst = os.path.join(auth_dir, fname)
        try:
            if not os.path.exists(dst):
                shutil.move(src, dst)
                moved.append(fname)
            else:
                # auth_data 쪽이 비어 있거나 루트 파일이 더 최신이면 안전하게 갱신
                src_size = os.path.getsize(src)
                dst_size = os.path.getsize(dst)
                if (dst_size <= 10 and src_size > 10) or (src_size > 10 and os.path.getmtime(src) > os.path.getmtime(dst)):
                    shutil.move(src, dst)
                    moved.append(fname)
                else:
                    os.remove(src)
        except Exception:
            pass
    return moved


def get_auth_file(filename):
    """auth_data/<filename> 경로 반환 (루트에 구버전 파일이 있으면 자동 이관)"""
    auth_dir = get_auth_dir()
    dst = os.path.join(auth_dir, filename)
    legacy_src = os.path.join(get_base_dir(), filename)
    if os.path.isfile(legacy_src):
        try:
            if not os.path.exists(dst):
                shutil.move(legacy_src, dst)
            else:
                src_size = os.path.getsize(legacy_src)
                dst_size = os.path.getsize(dst)
                if (dst_size <= 10 and src_size > 10) or (src_size > 10 and os.path.getmtime(legacy_src) > os.path.getmtime(dst)):
                    shutil.move(legacy_src, dst)
                else:
                    os.remove(legacy_src)
        except Exception:
            pass
    return dst


def _resolve_blackbox_mode(mode=None, login_only=False):
    if mode is not None and str(mode).strip():
        return "login" if str(mode).strip().lower() == "login" else "upload"
    return "login" if login_only else "upload"


def get_blackbox_path(platform_name, mode=None, login_only=False):
    """mode('login' | 'upload')에 따라 blackbox_login/ 또는 blackbox_upload/ 저장 경로 반환"""
    resolved_mode = _resolve_blackbox_mode(mode=mode, login_only=login_only)
    if resolved_mode == "login":
        return os.path.join(get_blackbox_login_dir(), f"블랙박스_{platform_name}_로그인.webm")
    return os.path.join(get_blackbox_upload_dir(), f"블랙박스_{platform_name}.webm")


def create_blackbox_record_dir(platform_name, mode=None, login_only=False):
    """실행 단위 블랙박스 임시 녹화 폴더(debug_records/_rec_<플랫폼>_<login|upload>_<ts>) 생성"""
    debug_dir = os.path.join(get_base_dir(), "debug_records")
    os.makedirs(debug_dir, exist_ok=True)
    resolved_mode = _resolve_blackbox_mode(mode=mode, login_only=login_only)
    rec_dir = os.path.join(debug_dir, f"_rec_{platform_name}_{resolved_mode}_{int(time.time() * 1000)}")
    os.makedirs(rec_dir, exist_ok=True)
    return rec_dir


def is_any_locator_visible(page, selector):
    """셀렉터에 매칭되는 요소 중 실제로 화면에 보이는(visible) 요소가 하나라도 있으면 True.
    브라우저/페이지 닫힘 예외는 상위로 즉시 전파해 대기 루프를 빠르게 종료."""
    if _is_page_closed_safely(page):
        raise RuntimeError("Target page, context or browser has been closed")
    try:
        loc = page.locator(selector)
        cnt = loc.count()
        for i in range(min(cnt, 10)):
            try:
                if loc.nth(i).is_visible():
                    return True
            except Exception as e:
                if is_target_closed_error(e):
                    raise
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return False


def save_storage_state_safely(context, cookies_path, platform_label="", mark_verified=False):
    """storage_state를 임시 파일에 먼저 저장·검증한 뒤 원자적으로 교체해 쿠키 손상 및 예외 중단 방지"""
    if not context or not cookies_path:
        return False
    label = f"[{platform_label}] " if platform_label else ""
    tmp_path = f"{cookies_path}.tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cookies_path)), exist_ok=True)
        context.storage_state(path=tmp_path)
        if mark_verified and os.path.exists(tmp_path):
            try:
                with open(tmp_path, "r", encoding="utf-8") as rf:
                    sdata = json.load(rf)
                if isinstance(sdata, dict):
                    sdata["auth_verified"] = True
                    sdata["verified_at"] = int(time.time())
                    with open(tmp_path, "w", encoding="utf-8") as wf:
                        json.dump(sdata, wf, ensure_ascii=False, indent=2)
            except Exception:
                pass
        if is_valid_cookie_file(tmp_path, auto_clean=False):
            os.replace(tmp_path, cookies_path)
            return True
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        print(f"⚠️ {label}저장된 쿠키 데이터가 비어 있어 기존 쿠키를 유지합니다.", flush=True)
        return False
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        print(f"⚠️ {label}쿠키(storage_state) 저장 중 예외 발생(세션 정리 계속): {e}", flush=True)
        return False


def is_target_closed_error(exc):
    """Playwright 브라우저/컨텍스트/페이지 닫힘 예외 여부 판별"""
    s = str(exc or "").lower()
    return any(
        kw in s
        for kw in (
            "target page, context or browser has been closed",
            "target closed",
            "browser has been closed",
            "context has been closed",
            "page has been closed",
            "connection closed",
            "target crashed",
            "frame was detached",
        )
    )


def _is_page_closed_safely(page):
    """페이지 또는 브라우저 연결이 이미 닫혔는지 안전하게 판별"""
    if page is None:
        return True
    try:
        if page.is_closed():
            return True
    except Exception:
        return True
    try:
        ctx = page.context
        if ctx is not None and getattr(ctx, "browser", None) is not None:
            if not ctx.browser.is_connected():
                return True
    except Exception:
        return True
    return False


def _sleep_interruptible(seconds, page=None, stop_checker=None, slice_sec=0.25):
    """중지 요청(stop_checker) 또는 브라우저 창 닫힘을 0.25초 단위로 감지하는 분할 대기.
    반환값: 'stopped' | 'closed' | 'ok'"""
    end_ts = time.time() + max(0.0, float(seconds))
    while True:
        if stop_checker is not None:
            try:
                if stop_checker():
                    return "stopped"
            except Exception:
                pass
        if page is not None and _is_page_closed_safely(page):
            return "closed"
        rem = end_ts - time.time()
        if rem <= 0:
            return "ok"
        time.sleep(min(slice_sec, rem))


def detect_two_factor_auth(page):
    """2단계 인증 / 보안 확인 / 인증번호 입력 / 기기 등록 화면 감지. 감지 시 사유 문자열 반환, 아니면 None."""
    pages_to_check = []
    if page is not None:
        pages_to_check.append(page)
        try:
            ctx = page.context
            if ctx is not None:
                for p in ctx.pages:
                    if p is not page and not _is_page_closed_safely(p):
                        pages_to_check.append(p)
        except Exception:
            pass

    url_keywords = (
        "two_factor",
        "two-factor",
        "/2fa",
        "/mfa",
        "/otp",
        "deviceconfirm",
        "device_confirm",
        "challenge",
        "checkpoint",
        "signin/v2/challenge",
        "verify",
        "verification",
        "security_check",
    )
    input_sels = (
        'input[autocomplete="one-time-code"], '
        'input[name*="otp" i], '
        'input[name*="totp" i], '
        'input[name*="verification" i], '
        'input[placeholder*="인증번호"], '
        'input[placeholder*="인증 코드"], '
        'input[placeholder*="보안 코드"], '
        'input[placeholder*="verification code" i], '
        'input[placeholder*="security code" i], '
        'input[placeholder*="6-digit" i]'
    )
    text_sels = (
        ':text("2단계 인증"), '
        ':text("2차 인증"), '
        ':text("인증번호를 입력"), '
        ':text("인증번호 입력"), '
        ':text("인증 코드를 입력"), '
        ':text("보안 코드를 입력"), '
        ':text("새로운 환경에서 로그인"), '
        ':text("기기 등록"), '
        ':text("휴대폰 인증"), '
        ':text("본인 확인"), '
        ':text("Two-factor"), '
        ':text("2-Step Verification"), '
        ':text("Enter the code"), '
        ':text("Verification code"), '
        ':text("Security check"), '
        ':text("Verify it\'s you")'
    )

    for pg in pages_to_check:
        if _is_page_closed_safely(pg):
            continue
        try:
            cur_url = (pg.url or "").lower()
            is_google_pwd_step = ("accounts.google.com" in cur_url and "/challenge/pwd" in cur_url)
            if not is_google_pwd_step:
                for kw in url_keywords:
                    if kw in cur_url:
                        return f"보안/2단계 인증 경로({kw})"
            if is_any_locator_visible(pg, input_sels):
                return "인증번호/보안코드 입력창"
            if not is_google_pwd_step and is_any_locator_visible(pg, text_sels):
                return "2단계 인증/보안 확인 안내"
        except Exception as e:
            if pg is page and is_target_closed_error(e):
                raise
    return None


def wait_for_manual_login(
    page,
    platform_label,
    is_logged_in_fn,
    timeout_sec=180,
    poll_interval=2.0,
    settle_sec=2.5,
    on_tick=None,
    stop_checker=None,
):
    """열린 전용 크롬에서 2단계 인증 안내 + 수동 로그인 완료 대기 + stop_checker/창 닫힘 즉시 감지."""
    def _stopped():
        if stop_checker is None:
            return False
        try:
            return bool(stop_checker())
        except Exception:
            return False

    if _stopped():
        msg = "작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다."
        print(f"🛑 [{platform_label}] {msg}", flush=True)
        raise InterruptedError(f"{platform_label} {msg}")

    if _is_page_closed_safely(page):
        msg = "브라우저 창이 닫혀 로그인 대기를 즉시 종료합니다."
        print(f"🛑 [{platform_label}] {msg}", flush=True)
        raise RuntimeError(f"{platform_label} {msg}")

    effective_timeout = float(timeout_sec)
    print(
        f"🔑 [{platform_label}] 로그인이 필요합니다. 열린 크롬 창에서 직접 로그인을 완료해 주세요 (최대 {int(effective_timeout)}초 대기)...",
        flush=True,
    )
    start_ts = time.time()
    last_log_ts = start_ts
    last_2fa_log_ts = 0.0
    twofa_announced = False
    tick_idx = 0

    while True:
        elapsed = time.time() - start_ts

        if _stopped():
            msg = f"작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다 ({int(elapsed)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise InterruptedError(f"{platform_label} {msg}")

        if _is_page_closed_safely(page):
            msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(elapsed)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise RuntimeError(f"{platform_label} {msg}")

        if on_tick is not None:
            try:
                on_tick(page, tick_idx, elapsed)
            except Exception as tick_err:
                if _stopped():
                    msg = f"작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다 ({int(elapsed)}초 경과)."
                    print(f"🛑 [{platform_label}] {msg}", flush=True)
                    raise InterruptedError(f"{platform_label} {msg}") from tick_err
                if is_target_closed_error(tick_err) or _is_page_closed_safely(page):
                    msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(elapsed)}초 경과)."
                    print(f"🛑 [{platform_label}] {msg}", flush=True)
                    raise RuntimeError(f"{platform_label} {msg}") from tick_err

        if _stopped():
            msg = f"작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다 ({int(elapsed)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise InterruptedError(f"{platform_label} {msg}")

        if _is_page_closed_safely(page):
            msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(elapsed)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise RuntimeError(f"{platform_label} {msg}")

        # 2단계 인증 / 보안 확인 감지 시 실시간 안내 및 충분한 대기 시간 확보(최대 300초)
        try:
            twofa_reason = detect_two_factor_auth(page)
        except Exception as tfa_err:
            if is_target_closed_error(tfa_err) or _is_page_closed_safely(page):
                msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(elapsed)}초 경과)."
                print(f"🛑 [{platform_label}] {msg}", flush=True)
                raise RuntimeError(f"{platform_label} {msg}") from tfa_err
            twofa_reason = None

        if twofa_reason:
            if effective_timeout < 300.0 and (effective_timeout - elapsed) < 120.0:
                effective_timeout = min(300.0, elapsed + 120.0)
            now_ts = time.time()
            if (not twofa_announced) or (now_ts - last_2fa_log_ts >= 28.0):
                twofa_announced = True
                last_2fa_log_ts = now_ts
                last_log_ts = now_ts
                print(
                    f"🔐 [{platform_label}] 2단계 인증/보안 확인이 감지되었습니다({twofa_reason}). 열린 크롬 창에서 직접 인증을 완료해 주세요! (최대 {int(effective_timeout)}초 대기)",
                    flush=True,
                )

        try:
            logged_in = bool(is_logged_in_fn(page))
        except Exception as e:
            if _stopped():
                msg = f"작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다 ({int(elapsed)}초 경과)."
                print(f"🛑 [{platform_label}] {msg}", flush=True)
                raise InterruptedError(f"{platform_label} {msg}") from e
            if is_target_closed_error(e) or _is_page_closed_safely(page):
                msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(elapsed)}초 경과)."
                print(f"🛑 [{platform_label}] {msg}", flush=True)
                raise RuntimeError(f"{platform_label} {msg}") from e
            logged_in = False

        if logged_in:
            print(f"✅ [{platform_label}] 로그인 완료 감지! 세션 안정화 후 계속 진행합니다.", flush=True)
            st = _sleep_interruptible(settle_sec, page=page, stop_checker=stop_checker)
            if st == "stopped":
                raise InterruptedError(f"{platform_label} 작업 중지 요청")
            return True

        if elapsed >= effective_timeout:
            msg = f"로그인 대기 시간 초과({int(effective_timeout)}초)로 중단되었습니다."
            print(f"⏱️ [{platform_label}] {msg}", flush=True)
            raise TimeoutError(f"{platform_label} {msg}")

        if time.time() - last_log_ts >= 14.0 and not _stopped() and not _is_page_closed_safely(page):
            print(
                f"⏳ [{platform_label}] 로그인 대기 중... 열린 크롬 창에서 로그인을 완료해 주세요 ({int(elapsed)}초 / {int(effective_timeout)}초)",
                flush=True,
            )
            last_log_ts = time.time()

        tick_idx += 1
        sleep_res = _sleep_interruptible(poll_interval, page=page, stop_checker=stop_checker)
        if sleep_res == "stopped":
            msg = f"작업 중지 요청을 감지해 로그인 대기를 즉시 종료합니다 ({int(time.time() - start_ts)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise InterruptedError(f"{platform_label} {msg}")
        if sleep_res == "closed":
            msg = f"로그인 대기 중 브라우저 창이 닫혀 즉시 종료합니다 ({int(time.time() - start_ts)}초 경과)."
            print(f"🛑 [{platform_label}] {msg}", flush=True)
            raise RuntimeError(f"{platform_label} {msg}")


_DIAG_LOCK = threading.Lock()
_ACTIVE_DIAG_PLATFORM = ""
_PLATFORM_DIAGNOSTICS = {}
_TEST_BLACKBOX_LOCK = threading.Lock()
_TEST_BLACKBOX_DIR = ""
_TEST_BLACKBOX_SEQ = 0
_TEST_BLACKBOX_KIND = "test"


def set_active_diagnostics_platform(platform_name=""):
    """현재 진행 중인 플랫폼명 지정 (실시간 URL·버튼 캐시 및 에러 진단 매핑용)"""
    global _ACTIVE_DIAG_PLATFORM
    with _DIAG_LOCK:
        _ACTIVE_DIAG_PLATFORM = str(platform_name or "").strip()


def clear_platform_diagnostics(platform_name=None):
    with _DIAG_LOCK:
        if platform_name is None:
            _PLATFORM_DIAGNOSTICS.clear()
        else:
            _PLATFORM_DIAGNOSTICS.pop(str(platform_name), None)


def capture_page_diagnostics(page):
    """열려 있는 Playwright page에서 현재 URL과 실제 표시 중인 주요 버튼·클릭 요소 텍스트 목록을 빠르게 수집"""
    info = {"url": "", "buttons": []}
    if page is None or _is_page_closed_safely(page):
        return info
    try:
        info["url"] = str(page.url or "").strip()
    except Exception:
        pass
    try:
        js = """() => {
            const sels = 'button, a, [role="button"], [role="tab"], input[type="submit"], input[type="button"]';
            const nodes = Array.from(document.querySelectorAll(sels));
            const out = [];
            const seen = new Set();
            for (const el of nodes) {
                const st = window.getComputedStyle(el);
                if (!st || st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') === 0) continue;
                const r = el.getBoundingClientRect();
                if (!r || (r.width < 2 && r.height < 2)) continue;
                let txt = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('value') || el.getAttribute('title') || '').replace(/\\s+/g, ' ').trim();
                if (!txt) continue;
                if (txt.length > 60) txt = txt.slice(0, 60) + '…';
                const key = txt.toLowerCase();
                if (!seen.has(key)) {
                    seen.add(key);
                    out.push(txt);
                }
                if (out.length >= 45) break;
            }
            return out;
        }"""
        btns = page.evaluate(js)
        if isinstance(btns, list):
            info["buttons"] = [str(b).strip() for b in btns if str(b or "").strip()][:45]
    except Exception:
        pass
    return info


def attach_diagnostics_tracker(context, platform_name=""):
    """컨텍스트의 페이지 이동/로드 시점에 최신 URL과 표시 버튼 목록을 스레드 안전하게 캐시
    (강제 건너뛰기·브라우저 급종료 시에도 직전 페이지 URL/버튼 정보를 report.md에 보존)"""
    if context is None:
        return

    def _update_cache(pg):
        try:
            with _DIAG_LOCK:
                target_p = str(platform_name or _ACTIVE_DIAG_PLATFORM or "").strip()
            if not target_p:
                return
            snap = capture_page_diagnostics(pg)
            if not snap.get("url") and not snap.get("buttons"):
                return
            with _DIAG_LOCK:
                cur = _PLATFORM_DIAGNOSTICS.setdefault(target_p, {})
                if snap.get("url"):
                    cur["url"] = snap["url"]
                if snap.get("buttons"):
                    cur["buttons"] = snap["buttons"]
                cur["captured_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    def _hook_page(pg):
        if pg is None:
            return
        try:
            pg.on("framenavigated", lambda frame: _update_cache(pg) if frame == pg.main_frame else None)
            pg.on("load", lambda *_: _update_cache(pg))
            pg.on("domcontentloaded", lambda *_: _update_cache(pg))
        except Exception:
            pass

    try:
        for existing_pg in list(context.pages):
            _hook_page(existing_pg)
        context.on("page", _hook_page)
    except Exception:
        pass


def record_platform_diagnostics(platform_name, page=None, exc=None, reason=""):
    """플랫폼 실패·건너뜀 시 URL, 파이썬 에러/Traceback, 화면 버튼 목록을 기록"""
    pname = str(platform_name or "").strip()
    if not pname:
        with _DIAG_LOCK:
            pname = _ACTIVE_DIAG_PLATFORM
    if not pname:
        return {}
    live_snap = capture_page_diagnostics(page) if page is not None else {"url": "", "buttons": []}
    tb_text = ""
    err_msg = ""
    if exc is not None:
        err_msg = f"{type(exc).__name__}: {exc}"
        tb_raw = traceback.format_exc()
        if tb_raw and "NoneType: None" not in tb_raw:
            tb_text = tb_raw.strip()
        else:
            tb_text = err_msg
    with _DIAG_LOCK:
        cur = dict(_PLATFORM_DIAGNOSTICS.get(pname) or {})
        if live_snap.get("url"):
            cur["url"] = live_snap["url"]
        if live_snap.get("buttons"):
            cur["buttons"] = live_snap["buttons"]
        if err_msg:
            cur["error"] = err_msg
        if tb_text:
            cur["traceback"] = tb_text
        if reason:
            cur["reason"] = str(reason)
        cur["captured_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _PLATFORM_DIAGNOSTICS[pname] = cur
        return dict(cur)


def get_platform_diagnostics(platform_name):
    with _DIAG_LOCK:
        return dict(_PLATFORM_DIAGNOSTICS.get(str(platform_name or "").strip()) or {})


def set_blackbox_session_dir(dir_path="", session_kind="upload"):
    """업로드/테스트 실행 중 각 플랫폼 녹화 클립(.webm)을 1회 실행 임시 세션 폴더에 순차 수집하도록 지정"""
    global _TEST_BLACKBOX_DIR, _TEST_BLACKBOX_SEQ, _TEST_BLACKBOX_KIND
    with _TEST_BLACKBOX_LOCK:
        _TEST_BLACKBOX_DIR = str(dir_path or "").strip()
        _TEST_BLACKBOX_SEQ = 0
        _TEST_BLACKBOX_KIND = "test" if str(session_kind or "").strip().lower() == "test" else "upload"
        if _TEST_BLACKBOX_DIR:
            os.makedirs(_TEST_BLACKBOX_DIR, exist_ok=True)


def get_blackbox_session_dir():
    with _TEST_BLACKBOX_LOCK:
        return _TEST_BLACKBOX_DIR


def get_blackbox_session_kind():
    with _TEST_BLACKBOX_LOCK:
        return _TEST_BLACKBOX_KIND


def set_test_blackbox_dir(dir_path=""):
    """기존 호환 래퍼: 테스트 모드 녹화 세션 폴더 지정"""
    set_blackbox_session_dir(dir_path=dir_path, session_kind="test")


def get_test_blackbox_dir():
    return get_blackbox_session_dir()


def _next_test_blackbox_path(platform_name):
    global _TEST_BLACKBOX_SEQ
    with _TEST_BLACKBOX_LOCK:
        if not _TEST_BLACKBOX_DIR:
            return ""
        _TEST_BLACKBOX_SEQ += 1
        os.makedirs(_TEST_BLACKBOX_DIR, exist_ok=True)
        safe_name = re.sub(r"[^\w가-힣-]+", "_", str(platform_name or "clip"))
        return os.path.join(_TEST_BLACKBOX_DIR, f"{_TEST_BLACKBOX_SEQ:02d}_{safe_name}.webm")


def prune_blackbox_upload_dir(max_keep=3):
    """내부 blackbox_upload/ 폴더에 녹화 파일(.mp4 합본 및 .webm)을 최신 max_keep(기본 3개)만 유지하고 오래된 파일부터 자동 삭제"""
    upload_bb_dir = get_blackbox_upload_dir()
    if not os.path.isdir(upload_bb_dir):
        return []
    vids = []
    for fn in os.listdir(upload_bb_dir):
        fp = os.path.join(upload_bb_dir, fn)
        if os.path.isfile(fp) and fn.lower().endswith((".webm", ".mp4")):
            try:
                vids.append((os.path.getmtime(fp), fn, fp))
            except Exception:
                pass
    vids.sort(key=lambda x: (x[0], x[1]), reverse=True)
    removed = []
    for _, _, old_fp in vids[max_keep:]:
        try:
            os.remove(old_fp)
            removed.append(os.path.basename(old_fp))
        except Exception:
            pass
    return removed


def finalize_playwright_session(
    platform_name,
    mode=None,
    login_only=False,
    page=None,
    context=None,
    browser=None,
    video_obj=None,
    record_dir=None,
    platform_label=None,
):
    """finally 블록 공용 정리기:
    1) 종료 직전 진단 스냅샷(URL/버튼) 확보 및 최신 영구 프로필 쿠키를 auth_data/로 동기화
    2) video 경로 선확보
    3) page -> context -> browser(선택) 안전 종료(녹화 파일 flush)
    4) 테스트 모드면 임시 세션 폴더로, 일반 업로드면 blackbox_upload/(최신 3개 유지)로 안전 이동"""
    label = platform_label or platform_name
    resolved_mode = _resolve_blackbox_mode(mode=mode, login_only=login_only)
    test_target = _next_test_blackbox_path(platform_name) if resolved_mode == "upload" and get_test_blackbox_dir() else ""
    target_path = test_target or get_blackbox_path(platform_name, mode=resolved_mode)
    rel_target = os.path.relpath(target_path, get_base_dir()).replace("\\", "/")

    # 페이지가 아직 열려 있다면 닫기 직전 URL·버튼 진단 스냅샷 최신화
    try:
        if page is not None and not _is_page_closed_safely(page):
            record_platform_diagnostics(platform_name, page=page)
    except Exception:
        pass

    # persistent context 닫기 직전에 최신 쿠키를 auth_data/에 반영해 대시보드 신호등 자동 갱신
    try:
        if context is not None:
            sync_cookies_from_persistent_context(context)
    except Exception:
        pass

    vp = None
    try:
        v_ref = video_obj
        if v_ref is None and page is not None:
            v_ref = page.video
        if v_ref is not None:
            vp = v_ref.path()
    except Exception:
        vp = None

    # page -> context 순서로 닫아야 Playwright가 .webm 트레일러를 정상 기록하고 파일을 닫음
    try:
        if page is not None and not page.is_closed():
            page.close()
    except Exception:
        pass
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass

    # 녹화 파일 flush 및 OS 파일 잠금 해제 대기 (최대 약 1.8초)
    src_video = None
    last_size = -1
    for _ in range(12):
        cand = None
        if vp and os.path.isfile(vp):
            cand = vp
        elif record_dir and os.path.isdir(record_dir):
            webms = [
                os.path.join(record_dir, f)
                for f in os.listdir(record_dir)
                if f.lower().endswith(".webm") and os.path.isfile(os.path.join(record_dir, f))
            ]
            if webms:
                cand = max(webms, key=lambda x: os.path.getmtime(x))
        if cand and os.path.isfile(cand):
            try:
                sz = os.path.getsize(cand)
            except Exception:
                sz = 0
            if sz > 0 and sz == last_size:
                src_video = cand
                break
            last_size = sz
            src_video = cand
        time.sleep(0.15)

    saved_path = None
    if src_video and os.path.isfile(src_video) and os.path.getsize(src_video) > 0:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        for _ in range(4):
            try:
                shutil.move(src_video, target_path)
                saved_path = target_path
                break
            except Exception:
                try:
                    shutil.copy2(src_video, target_path)
                    saved_path = target_path
                    try:
                        os.remove(src_video)
                    except Exception:
                        pass
                    break
                except Exception:
                    time.sleep(0.25)

    if record_dir and os.path.isdir(record_dir):
        try:
            shutil.rmtree(record_dir, ignore_errors=True)
        except Exception:
            pass

    if saved_path and os.path.isfile(saved_path):
        if test_target:
            kind_label = "테스트 모드" if get_blackbox_session_kind() == "test" else "업로드 세션"
            print(f"🎥 [{label}] {kind_label} 녹화 클립 확보 완료", flush=True)
        else:
            if resolved_mode == "upload":
                prune_blackbox_upload_dir(max_keep=3)
            print(f"🎥 [{label}] 블랙박스 녹화 저장 완료: {rel_target}", flush=True)
    else:
        print(f"ℹ️ [{label}] 블랙박스 녹화 파일 미생성 (예정 경로: {rel_target})", flush=True)
    return saved_path


def get_profile_path():
    return get_auth_file("user_profile.json")


def is_valid_cookie_file(path, auto_clean=True):
    """쿠키 파일 존재·크기(>10B)·유효 JSON dict·비어있지 않은 cookies(또는 origins) 보유 여부를 검사.
    0바이트이거나 깨진 JSON인 경우 auto_clean=True이면 손상 파일을 안전하게 자동 삭제."""
    if not path or not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= 10:
            raise ValueError("cookie file is empty or too small")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("cookie file root is not dict")
        cookies = data.get("cookies")
        origins = data.get("origins")
        has_cookies = isinstance(cookies, list) and len(cookies) > 0
        has_origins = isinstance(origins, list) and len(origins) > 0
        return has_cookies or has_origins
    except Exception:
        if auto_clean:
            try:
                os.remove(path)
            except Exception:
                pass
        return False


def normalize_region_keyword(raw):
    """대표 지역/상권 키워드 자동 보정: 앞의 '#'과 뒤의 '미용실' 및 불필요 공백 제거"""
    s = str(raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[#\s]+", "", s)
    s = re.sub(r"\s*미용실\s*$", "", s)
    s = re.sub(r"^[#\s]+", "", s).strip()
    return s


def extract_naver_blog_id(raw):
    """네이버 블로그 전체 URL, 이메일(id@naver.com) 또는 아이디에서 순수 블로그 ID만 자동 추출"""
    s = str(raw or "").strip()
    if not s:
        return ""
    m_q = re.search(r"[?&]blogId=([A-Za-z0-9._-]+)", s, re.IGNORECASE)
    if m_q:
        return m_q.group(1).strip()
    m_u = re.search(r"(?:m\.)?blog\.naver\.com/([A-Za-z0-9._-]+)", s, re.IGNORECASE)
    if m_u:
        cand = m_u.group(1).strip()
        if cand.lower() not in ("postlist.naver", "postview.naver", "goblogwrite.naver", "myblog.naver"):
            return cand
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE).strip("/")
    s = s.split("/")[0].split("?")[0].strip().lstrip("@")
    if "@" in s:
        s = s.split("@", 1)[0].strip()
    return s


def extract_pinterest_id(raw):
    """핀터레스트 전체 URL, 이메일 또는 아이디에서 순수 프로필 ID만 자동 추출"""
    s = str(raw or "").strip()
    if not s:
        return ""
    m_u = re.search(r"pinterest\.[a-z.]+/([A-Za-z0-9._-]+)", s, re.IGNORECASE)
    if m_u:
        cand = m_u.group(1).strip()
        if cand.lower() not in ("pin", "pin-creation-tool", "pin-builder", "ideas", "search", "settings", "login"):
            return cand
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE).strip("/")
    s = s.split("/")[0].split("?")[0].strip().lstrip("@")
    if "@" in s:
        s = s.split("@", 1)[0].strip()
    return s


def _clean_blog_categories(raw_cats):
    """블로그 게시판 목록 정제 ('오시는 길' 고정글용 제외, 중복 제거)"""
    if isinstance(raw_cats, str):
        items = re.split(r"[,\n]+", raw_cats)
    elif isinstance(raw_cats, list):
        items = raw_cats
    else:
        items = []
    out = []
    seen = set()
    for item in items:
        c = str(item or "").strip()
        if not c:
            continue
        if c.replace(" ", "") in ("오시는길", "오시는길안내"):
            continue
        low = c.lower()
        if low not in seen:
            seen.add(low)
            out.append(c)
    return out


def _clean_enabled_platforms(raw_plats):
    if not isinstance(raw_plats, list):
        return list(ALL_PLATFORM_KEYS)
    cleaned = [p for p in raw_plats if p in ALL_PLATFORM_KEYS]
    return list(dict.fromkeys(cleaned))


def load_profile():
    """auth_data/user_profile.json 로드. 구버전 Men/Women 2칸 설정도 blog_categories로 자동 호환."""
    path = get_profile_path()
    prof = dict(EMPTY_PROFILE_TEMPLATE)
    prof["enabled_platforms"] = list(ALL_PLATFORM_KEYS)
    prof["blog_categories"] = []

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in EMPTY_PROFILE_TEMPLATE:
                    if k in data:
                        prof[k] = data[k]
                prof["enabled_platforms"] = _clean_enabled_platforms(data.get("enabled_platforms"))
                cats = _clean_blog_categories(data.get("blog_categories"))
                if not cats:
                    legacy_cats = []
                    for lk in ("naver_blog_cat_men", "naver_blog_cat_women"):
                        lv = str(data.get(lk) or "").strip()
                        if lv:
                            legacy_cats.append(lv)
                    cats = _clean_blog_categories(legacy_cats)
                prof["blog_categories"] = cats
                prof["region_keyword"] = normalize_region_keyword(prof.get("region_keyword", ""))
                prof["naver_blog_id"] = extract_naver_blog_id(prof.get("naver_blog_id", ""))
                prof["pinterest_id"] = extract_pinterest_id(prof.get("pinterest_id", ""))
                pm = str(prof.get("prompt_mode") or "standard").strip().lower()
                prof["prompt_mode"] = "custom" if pm == "custom" else "standard"
                prof["custom_style_guide"] = str(prof.get("custom_style_guide") or "").strip()
                raw_checks = data.get("onboarding_checks")
                prof["onboarding_checks"] = {str(k): bool(v) for k, v in raw_checks.items()} if isinstance(raw_checks, dict) else {}
                prof["prompt_copied"] = bool(data.get("prompt_copied", False))
                try:
                    prof["onboarding_step"] = max(0, min(4, int(prof.get("onboarding_step") or 0)))
                except Exception:
                    prof["onboarding_step"] = 0
                prof["onboarding_completed"] = bool(data.get("onboarding_completed", False))
                prof["is_configured"] = bool(data.get("is_configured", False)) and bool((prof.get("salon_name") or "").strip())
        except Exception:
            pass
    return prof


def save_profile(data, is_draft=False):
    """개인화·매장 설정을 auth_data/user_profile.json에 저장 (임시 저장 및 완료 저장 모두 지원)."""
    prof = load_profile()
    str_fields = [
        "salon_name",
        "region_keyword",
        "designer_name",
        "specialty",
        "booking_url",
        "naver_booking_url",
        "naver_blog_cat_men",
        "naver_blog_cat_women",
        "pinterest_board",
        "naver_blog_id",
        "pinterest_id",
        "place_search_keyword",
        "personal_banned_tags",
        "salon_address",
        "location_guide",
        "parking_guide",
        "business_hours",
        "custom_style_guide",
    ]
    for k in str_fields:
        if k in data:
            val = data.get(k)
            cleaned = "" if val is None else str(val).strip()
            if k == "region_keyword":
                cleaned = normalize_region_keyword(cleaned)
            elif k == "naver_blog_id":
                cleaned = extract_naver_blog_id(cleaned)
            elif k == "pinterest_id":
                cleaned = extract_pinterest_id(cleaned)
            prof[k] = cleaned

    if "prompt_mode" in data:
        pm = str(data.get("prompt_mode") or "standard").strip().lower()
        prof["prompt_mode"] = "custom" if pm == "custom" else "standard"

    if "onboarding_checks" in data and isinstance(data.get("onboarding_checks"), dict):
        prof["onboarding_checks"] = {str(k): bool(v) for k, v in data.get("onboarding_checks").items()}

    if "prompt_copied" in data:
        prof["prompt_copied"] = bool(data.get("prompt_copied"))

    if "salon_name" in data and not (data.get("place_search_keyword") or "").strip():
        prof["place_search_keyword"] = prof.get("salon_name", "")

    if "blog_categories" in data:
        prof["blog_categories"] = _clean_blog_categories(data.get("blog_categories"))
    elif "naver_blog_cat_men" in data or "naver_blog_cat_women" in data:
        prof["blog_categories"] = _clean_blog_categories(
            [prof.get("naver_blog_cat_men", ""), prof.get("naver_blog_cat_women", "")]
        )

    if "enabled_platforms" in data:
        prof["enabled_platforms"] = _clean_enabled_platforms(data.get("enabled_platforms"))

    if "onboarding_step" in data:
        try:
            prof["onboarding_step"] = max(0, min(4, int(data.get("onboarding_step") or 0)))
        except Exception:
            pass

    if "onboarding_completed" in data:
        prof["onboarding_completed"] = bool(data.get("onboarding_completed"))

    if not is_draft:
        prof["is_configured"] = bool((prof.get("salon_name") or "").strip())
        if prof["is_configured"] and prof.get("onboarding_step", 0) >= 4:
            prof["onboarding_completed"] = True
    else:
        if (prof.get("salon_name") or "").strip() and prof.get("onboarding_completed"):
            prof["is_configured"] = True

    path = get_profile_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=2)
    return prof


def get_salon_name(profile=None, fallback=""):
    prof = profile if isinstance(profile, dict) else load_profile()
    return (prof.get("salon_name") or "").strip() or fallback


def get_salon_address(profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    return (prof.get("salon_address") or "").strip()


def get_place_search_keyword(profile=None):
    """스마트플레이스 지도 검색어는 매장명과 통합하여 매장명을 최우선으로 사용"""
    prof = profile if isinstance(profile, dict) else load_profile()
    return (prof.get("salon_name") or prof.get("place_search_keyword") or "").strip()


def get_region_keyword(profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    return normalize_region_keyword(prof.get("region_keyword") or "")


def get_region_tags(profile=None):
    """사용자의 대표 지역/상권 키워드에서 자연 검색용 지역 해시태그(1~2개) 동적 생성"""
    raw = get_region_keyword(profile)
    if not raw:
        return []
    # 공백/특수문자/# 및 중복 '미용실'/'헤어샵' 접미사 제거
    clean = re.sub(r"[\s#,·•/]+", "", raw)
    clean = re.sub(r"(?:미용실|헤어샵|헤어살롱|헤어)$", "", clean).strip()
    if not clean:
        return []
    tags = []
    if clean.endswith("역") and len(clean) >= 3:
        base = clean[:-1]
        tags.append(f"{base}미용실")
        tags.append(f"{clean}미용실")
    else:
        tags.append(f"{clean}미용실")
    return list(dict.fromkeys(tags))


def get_blog_categories(profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    return _clean_blog_categories(prof.get("blog_categories"))


def is_profile_ready(profile=None):
    """업로드 진행에 필요한 최소 기본 정보(매장명)가 설정되어 있는지 확인"""
    prof = profile if isinstance(profile, dict) else load_profile()
    return bool((prof.get("salon_name") or "").strip())


def recommend_blog_category(text, categories, men_keywords=None):
    """사용자 등록 블로그 게시판 목록에서 본문 내용에 가장 잘 맞는 게시판 이름을 자동 추천"""
    cats = _clean_blog_categories(categories)
    if not cats:
        return ""
    t_low = (text or "").lower()

    # 1) 게시판 이름 자체가 본문에 직접 등장하면 우선 매칭 (Men/Women 외 커스텀 게시판 지원)
    for c in cats:
        c_low = c.lower().strip()
        if len(c_low) >= 2 and c_low not in ("men", "women", "남자", "여자") and c_low in t_low:
            return c
        # 슬래시/중점 구분 토큰 매칭 (예: '복구·클리닉' -> '복구', '클리닉')
        for part in re.split(r"[\s·•/,&+\-]+", c_low):
            if len(part) >= 2 and part not in ("men", "women", "남자", "여자", "스타일", "헤어") and part in t_low:
                return c

    # 2) 남성/여성 시술 키워드 판별 후 남/Men 또는 여/Women 게시판 자동 매칭
    m_kws = men_keywords or (
        "남자", "남성", "맨즈", "멘즈", "다운펌", "가르마", "애즈펌", "시스루댄디",
        "아이비리그", "리젠트", "가일", "포마드", "크롭", "쉐도우", "스핀스왈로",
        "페이드", "바버", "투블럭", "상고", "슬릭백", "드롭컷", "파일럿컷",
        "men", "mens", "male", "barber", "crop", "fade",
    )
    is_men = any(kw in t_low for kw in m_kws)
    if is_men:
        for c in cats:
            cl = c.lower()
            if any(k in cl for k in ("남", "men", "man", "맨즈", "멘즈", "바버")):
                return c
    else:
        for c in cats:
            cl = c.lower()
            if any(k in cl for k in ("여", "women", "woman", "레이디", "우먼")):
                return c

    return cats[0]


def get_effective_banned_tags(profile=None):
    """매장 공통 금지태그 + 개인 제외 태그 + 디자이너명(띄어쓰기 제거)을 소문자 set으로 병합 반환."""
    prof = profile if isinstance(profile, dict) else load_profile()
    banned = {t.strip().lower() for t in COMMON_BANNED_TAGS if t and t.strip()}

    raw_personal = prof.get("personal_banned_tags", "") or ""
    items = raw_personal if isinstance(raw_personal, list) else str(raw_personal).split(",")
    for item in items:
        cleaned = str(item).replace("#", "").strip().lower()
        if cleaned:
            banned.add(cleaned)

    d_name = (prof.get("designer_name") or "").strip()
    if d_name:
        compact_name = d_name.replace(" ", "").lower()
        if compact_name:
            banned.add(compact_name)

    return banned


def launch_chromium(playwright_obj, headless=False, args=None):
    """Chrome 채널 실행을 우선 시도하고, 크롬 미설치 PC에서는 기본 번들 Chromium으로 자동 폴백."""
    launch_args = list(args) if args is not None else ["--disable-blink-features=AutomationControlled"]
    try:
        return playwright_obj.chromium.launch(headless=headless, channel="chrome", args=launch_args)
    except Exception as e:
        print(f"ℹ️ [브라우저] Chrome 채널 실행 불가({e}) → 기본 Chromium으로 자동 전환합니다.", flush=True)
        return playwright_obj.chromium.launch(headless=headless, args=launch_args)


def copy_to_clipboard(text):
    """macOS(pbcopy)와 Windows(pyperclip / PowerShell UTF-8 Set-Clipboard) 모두에서 한글 깨짐 없이 클립보드 복사"""
    content = text or ""
    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=content.encode("utf-8"), check=True, timeout=2)
            return True
        except Exception:
            return False

    if sys.platform == "win32":
        try:
            import pyperclip
            pyperclip.copy(content)
            return True
        except Exception:
            pass
        try:
            ps_cmd = (
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                input=content.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception:
            return False

    try:
        import pyperclip
        pyperclip.copy(content)
        return True
    except Exception:
        return False


def wait_for_test_mode_close(context, label, stop_checker=None):
    """테스트 모드에서 게시 직전 입력 완료 상태를 유지한 채 사용자가 브라우저 창 [X]를 닫을 때까지 대기.
    - 사용자가 창 [X]를 닫으면 정상 완료(True) 반환 -> 기존 블랙박스 녹화본(.webm) 저장·합본으로 자연스럽게 이어짐
    - 대기 중 강제 중지 또는 [이 플랫폼만 건너뛰기] 요청(stop_checker() == True) 시 InterruptedError 발생"""
    def _stopped():
        if stop_checker is None:
            return False
        try:
            return bool(stop_checker())
        except Exception:
            return False

    print(
        f"🧪 [테스트 확인 대기 - {label}] 입력 내용을 확인한 뒤 브라우저 창 [X]를 닫으면 다음 플랫폼으로 넘어갑니다.",
        flush=True,
    )
    if context is None:
        return True

    closed_flag = [False]
    try:
        context.on("close", lambda *_: closed_flag.__setitem__(0, True))
    except Exception:
        pass

    while True:
        if _stopped():
            msg = "테스트 확인 대기 중 중지/건너뛰기 요청이 감지되었습니다."
            print(f"🛑 [{label}] {msg}", flush=True)
            raise InterruptedError(f"{label} {msg}")

        if closed_flag[0]:
            print(f"✅ [테스트 확인 완료 - {label}] 브라우저 창이 닫혀 다음 플랫폼으로 이동합니다.", flush=True)
            return True

        try:
            pages = list(context.pages)
        except Exception as e:
            if _stopped():
                raise InterruptedError(f"{label} 테스트 확인 대기 중 중단됨") from e
            print(f"✅ [테스트 확인 완료 - {label}] 브라우저 창이 닫혀 다음 플랫폼으로 이동합니다.", flush=True)
            return True

        open_pages = [pg for pg in pages if not _is_page_closed_safely(pg)]
        if not open_pages:
            print(f"✅ [테스트 확인 완료 - {label}] 브라우저 창이 닫혀 다음 플랫폼으로 이동합니다.", flush=True)
            return True

        try:
            open_pages[0].wait_for_timeout(500)
        except Exception as e:
            if _stopped():
                raise InterruptedError(f"{label} 테스트 확인 대기 중 중단됨") from e
            if is_target_closed_error(e) or closed_flag[0]:
                print(f"✅ [테스트 확인 완료 - {label}] 브라우저 창이 닫혀 다음 플랫폼으로 이동합니다.", flush=True)
                return True
            time.sleep(0.5)


PLATFORM_LOGOUT_CONFIG = {
    "yt": {
        "label": "유튜브 쇼츠",
        "cookie_file": "youtube_token.json",
        "domains": (),
        "origins": (),
        "is_youtube": True,
    },
    "naver": {
        "label": "네이버 블로그·클립",
        "cookie_file": "naver_cookies.json",
        "domains": ("naver.com",),
        "origins": (
            "https://www.naver.com",
            "https://nid.naver.com",
            "https://blog.naver.com",
            "https://clipcreators.naver.com",
        ),
    },
    "clip": {
        "label": "네이버 블로그·클립",
        "cookie_file": "naver_cookies.json",
        "domains": ("naver.com",),
        "origins": (
            "https://www.naver.com",
            "https://nid.naver.com",
            "https://blog.naver.com",
            "https://clipcreators.naver.com",
        ),
    },
    "tt": {
        "label": "틱톡",
        "cookie_file": "tiktok_cookies.json",
        "domains": ("tiktok.com",),
        "origins": ("https://www.tiktok.com", "https://tiktok.com"),
    },
    "ig": {
        "label": "인스타그램",
        "cookie_file": "instagram_cookies.json",
        "domains": ("instagram.com",),
        "origins": ("https://www.instagram.com", "https://instagram.com"),
    },
    "pin": {
        "label": "핀터레스트",
        "cookie_file": "pinterest_cookies.json",
        "domains": ("pinterest.com", "pinterest.co.kr"),
        "origins": (
            "https://www.pinterest.com",
            "https://kr.pinterest.com",
            "https://www.pinterest.co.kr",
        ),
    },
    "threads": {
        "label": "쓰레드",
        "cookie_file": "threads_cookies.json",
        "domains": ("threads.net", "threads.com"),
        "origins": (
            "https://www.threads.net",
            "https://www.threads.com",
            "https://threads.net",
            "https://threads.com",
        ),
    },
    "daangn": {
        "label": "당근 비즈",
        "cookie_file": "daangn_cookies.json",
        "domains": ("daangn.com", "karrotmarket.com"),
        "origins": (
            "https://business.daangn.com",
            "https://bizprofile.daangn.com",
            "https://accounts.daangn.com",
            "https://www.daangn.com",
            "https://www.karrotmarket.com",
        ),
    },
    "x": {
        "label": "엑스(X)",
        "cookie_file": "x_cookies.json",
        "domains": ("x.com", "twitter.com"),
        "origins": ("https://x.com", "https://twitter.com"),
    },
}

_LOGOUT_PROFILE_LOCK = threading.Lock()


def logout_platform_session(platform_key):
    """특정 플랫폼의 로그인 쿠키 파일과 영구 크롬 프로필 내 해당 도메인 쿠키·스토리지만 완전히 제거.
    (타 플랫폼 로그인 세션과 유튜브 client_secret.json은 절대 건드리지 않음)"""
    key = str(platform_key or "").strip().lower()
    cfg = PLATFORM_LOGOUT_CONFIG.get(key)
    if not cfg:
        return {"ok": False, "msg": f"지원하지 않는 플랫폼 키입니다: {platform_key}"}

    label = cfg["label"]
    cookie_file = cfg["cookie_file"]
    domains = cfg["domains"]
    origins = cfg.get("origins") or ()

    # 1) 유튜브: youtube_token.json만 삭제 (client_secret.json 안전 유지)
    if cfg.get("is_youtube"):
        tok_path = get_auth_file(cookie_file)
        removed = False
        if os.path.exists(tok_path):
            try:
                os.remove(tok_path)
                removed = True
            except Exception as e:
                return {"ok": False, "msg": f"[{label}] 토큰 파일 삭제 실패: {e}"}
        msg = (
            f"[{label}] 로그아웃 완료! (youtube_token.json 삭제 · client_secret.json 유지)"
            if removed
            else f"[{label}] 이미 로그아웃 상태입니다. (client_secret.json 유지)"
        )
        return {
            "ok": True,
            "platform": key,
            "label": label,
            "removed_file": removed,
            "cleared_cookies": 0,
            "msg": msg,
        }

    # 2) Playwright 기반 플랫폼: auth_data 쿠키 JSON 파일 삭제 + 영구 크롬 프로필 내 해당 도메인 쿠키·스토리지 제거
    with _LOGOUT_PROFILE_LOCK:
        cpath = get_auth_file(cookie_file)
        removed_file = False
        if os.path.exists(cpath):
            try:
                os.remove(cpath)
                removed_file = True
            except Exception as e:
                return {"ok": False, "msg": f"[{label}] 쿠키 파일 삭제 실패: {e}"}

        cleared_cookies = 0
        profile_dir = get_chrome_profile_dir()
        has_profile_data = os.path.isdir(profile_dir) and any(
            name not in (".DS_Store", "SingletonLock", "SingletonCookie", "SingletonSocket")
            for name in os.listdir(profile_dir)
        )

        if has_profile_data and domains:
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    ctx = launch_persistent_chromium(p, headless=True)
                    try:
                        all_cookies = ctx.cookies() or []
                        matched = [c for c in all_cookies if _domain_matches(c.get("domain"), domains)]
                        cleared_cookies = len(matched)

                        if matched:
                            target_doms = set()
                            for d in domains:
                                target_doms.add(d)
                                target_doms.add("." + d)
                            for c in matched:
                                d = (c.get("domain") or "").strip()
                                if d:
                                    target_doms.add(d)
                                    target_doms.add(d.lstrip("."))

                            domain_filter_supported = True
                            for d in target_doms:
                                try:
                                    ctx.clear_cookies(domain=d)
                                except TypeError:
                                    domain_filter_supported = False
                                    break
                                except Exception:
                                    pass

                            # 구버전 Playwright이거나 서브도메인 쿠키가 남은 경우 안전 필터링 폴백
                            remaining = [
                                c for c in (ctx.cookies() or [])
                                if _domain_matches(c.get("domain"), domains)
                            ]
                            if not domain_filter_supported or remaining:
                                keep = [
                                    c for c in (ctx.cookies() or [])
                                    if not _domain_matches(c.get("domain"), domains)
                                ]
                                ctx.clear_cookies()
                                if keep:
                                    ctx.add_cookies(keep)

                        # 해당 플랫폼 origin의 localStorage/IndexedDB/CacheStorage도 함께 제거
                        if origins:
                            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                            try:
                                cdp = ctx.new_cdp_session(pg)
                                for orig in origins:
                                    try:
                                        cdp.send(
                                            "Storage.clearDataForOrigin",
                                            {
                                                "origin": orig,
                                                "storageTypes": "cookies,local_storage,indexeddb,cache_storage,service_workers",
                                            },
                                        )
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    finally:
                        try:
                            ctx.close()
                        except Exception:
                            pass
                        cleanup_chrome_profile_locks()
            except Exception as e:
                print(f"⚠️ [{label}] 영구 크롬 프로필 쿠키 정리 중 경고 (쿠키 파일은 삭제됨): {e}", flush=True)

    return {
        "ok": True,
        "platform": key,
        "label": label,
        "removed_file": removed_file,
        "cleared_cookies": cleared_cookies,
        "msg": f"[{label}] 완전 로그아웃이 완료되었습니다. (쿠키 파일 및 브라우저 세션 초기화)",
    }
