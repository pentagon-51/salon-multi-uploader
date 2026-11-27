import os, re, sys, json, time, glob, stat, random, shutil, struct, hashlib, threading, traceback, subprocess, datetime, mimetypes
from urllib.parse import unquote, quote, parse_qs, urlsplit
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from uploaders.naver_blog_uploader import upload_to_naver_blog, classify_category
from uploaders.tiktok_uploader import upload_to_tiktok
from uploaders.instagram_uploader import upload_to_instagram
from auto_updater import (
    load_release_info,
    check_public_update,
    apply_public_update,
)
from os_utils import (
    get_base_dir,
    get_auth_dir,
    get_blackbox_dir,
    format_blackbox_filename,
    get_auth_file,
    migrate_legacy_auth_files,
    cleanup_chrome_profile_locks,
    launch_persistent_chromium,
    sync_cookies_from_persistent_context,
    normalize_branch_name,
    COMMON_BANNED_TAGS,
    load_profile,
    save_profile,
    get_effective_banned_tags,
    is_valid_cookie_file,
    is_platform_cookie_authenticated,
    check_cookie_login_redirect,
    get_salon_name,
    get_salon_address,
    get_place_search_keyword,
    get_region_keyword,
    get_region_tags,
    get_blog_categories,
    is_profile_ready,
    recommend_blog_category,
    set_active_diagnostics_platform,
    clear_platform_diagnostics,
    record_platform_diagnostics,
    get_platform_diagnostics,
    set_blackbox_session_dir,
    get_blackbox_session_dir,
    set_test_blackbox_dir,
    get_test_blackbox_dir,
    prune_blackbox_dir,
    is_blackbox_protected_file,
    logout_platform_session,
    auto_extract_and_save_profile_id,
    set_force_stop_keep_browser,
    is_force_stop_keep_browser,
    has_held_playwright_sessions,
    release_held_playwright_sessions,
    open_playwright_session,
    finalize_playwright_session,
    create_blackbox_record_dir,
    has_valid_facebook_auth_cookies,
    wait_chrome_profile_unlock,
)

BASE_DIR = get_base_dir()
AUTH_DIR = get_auth_dir()
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
BLACKBOX_DIR = get_blackbox_dir()
BLACKBOX_TMP_DIR = os.path.join(BLACKBOX_DIR, '_tmp')
LEGACY_BLACKBOX_LOGIN_DIR = os.path.join(BASE_DIR, 'blackbox_login')
LEGACY_BLACKBOX_UPLOAD_DIR = os.path.join(BASE_DIR, 'blackbox_upload')
LEGACY_DEBUG_DIR = os.path.join(BASE_DIR, 'debug_records')
UPLOAD_DIR = os.path.join(BASE_DIR, 'upload')
DONE_DIR = os.path.join(BASE_DIR, 'done')
BACKUP_DIR = os.path.join(BASE_DIR, '_old_backup')
THUMB_DIR = os.path.join(BASE_DIR, 'temp_thumbs')
UI_FILE = os.path.join(BASE_DIR, 'app_ui.html')
BUSY_TASK_MSG = '이미 다른 작업이 진행 중입니다. 중지하려면 우측 상단 [🛑 강제 중지] 버튼을 눌러주세요.'
migrate_legacy_auth_files()
DAILY_USAGE_PATH = get_auth_file('daily_usage.json')
USAGE_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
TEST_MERGE_LOCK = threading.Lock()

SAFE_DAILY_LIMITS = {
    'naver': 5,
    'clip': 5,
    'ig': 3,
    'tt': 5,
    'fb': 3,
    'fb_page': 3,
    'pin': 10,
}
YOUTUBE_POINTS_PER_UPLOAD = 1600
YOUTUBE_MAX_POINTS = 10000
YOUTUBE_MAX_COUNT = 6

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DONE_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(BLACKBOX_DIR, exist_ok=True)
STATE_LOCK = threading.Lock()
ACTIVE_WORKER_THREAD = None
STATE = {
    'video_path': '',
    'video_name': '',
    'busy': False,
    'stop_requested': False,
    'active_run_id': 0,
    'auth_check_seq': 0,
    'logs': ['준비 완료! 영상을 업로드하세요.'],
    'last_results': {},
    'active_history_id': '',
    'active_uploaded_platforms': [],
    'current_platform_key': '',
    'current_platform_label': '',
    'skip_platform_key': '',
    'skip_seq': 0,
    'is_test_mode': False,
    'test_session_dir': '',
    'test_start_dt': None,
    'test_merged_done': False,
    'force_stop_merging': False,
    'browser_left_open_by_stop': False,
    'active_run_ctx': None,
}

class UILogStream:
    """각 플랫폼 업로더(naver, clip, ig 등)의 print 출력을 웹 UI 실시간 로그창으로 자동 중계"""
    def __init__(self, orig):
        self.orig = orig
    def write(self, text):
        try:
            if self.orig:
                self.orig.write(text)
                self.orig.flush()
        except Exception:
            pass
        lines = [line.strip() for line in (text or '').splitlines()]
        with STATE_LOCK:
            for s in lines:
                if s and not s.startswith('127.0.0.1'):
                    if not STATE['logs'] or STATE['logs'][-1] != s:
                        STATE['logs'].append(s)
                        STATE['logs'] = STATE['logs'][-80:]
    def flush(self):
        try:
            if self.orig:
                self.orig.flush()
        except Exception: pass

sys.stdout = UILogStream(sys.stdout)

def log(msg):
    print(msg, flush=True)

ESSENTIAL_ITEMS = {
    'app_server.py', 'app_ui.html', 'os_utils.py', 'auto_updater.py',
    '2-1. 맥북 최초설치.command', '2-2. 맥북 프로그램 실행.command',
    'version.json', 'LICENSE.txt', '.gitignore', '.gitattributes',
    '0. 처음사용자 안내서.html', '1-1. 윈도우 최초설치.bat', '1-2. 윈도우 프로그램 실행.bat', '.installed_done', '_설치완료_보관함',
    'uploaders', 'assets', 'dev_tools',
    'app.log',
    'auth_data', 'blackbox', 'upload', 'done', '_old_backup', 'temp_thumbs', '__pycache__'
}

COOKIE_STALE_DAYS = 20
PREVIEW_TRANSCODE_LOCK = threading.Lock()

def _check_cookie_status_and_age(cookie_filename, deep=False):
    """auth_data/ 기준 플랫폼별 실제 로그인 인증 쿠키/세션 정밀 판별 및 경과 일수 반환: (state, age_days).
    단순 방문자 쿠키·비로그인 프로필은 반드시 'red'로 판별.
    deep=True일 때만 보호 URL 리다이렉트 정밀 검사(네이버)를 1회 수행."""
    cpath = get_auth_file(cookie_filename)
    if not is_platform_cookie_authenticated(cookie_filename, cpath, auto_clean=False):
        return 'red', None
    if deep:
        redirect_ok = check_cookie_login_redirect(cookie_filename, cpath, timeout=3.5)
        if redirect_ok is False:
            try:
                if os.path.exists(cpath):
                    os.remove(cpath)
            except Exception:
                pass
            return 'red', None
    try:
        elapsed = max(0.0, time.time() - os.path.getmtime(cpath))
        age_days = int(elapsed // 86400)
        if elapsed >= COOKIE_STALE_DAYS * 86400:
            return 'yellow', age_days
        return 'green', age_days
    except Exception:
        return 'green', 0

YOUTUBE_SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
]


def _is_valid_youtube_client_secret(secret_path):
    if not secret_path or not os.path.exists(secret_path) or os.path.getsize(secret_path) <= 10:
        return False
    try:
        with open(secret_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        node = data.get('installed') or data.get('web')
        return isinstance(node, dict) and bool(str(node.get('client_id') or '').strip())
    except Exception:
        return False


def validate_youtube_oauth_token(attempt_refresh=False):
    """auth_data/client_secret.json 및 auth_data/youtube_token.json의 실제 OAuth 유효성 검증.
    - 토큰이 없거나 손상되었거나, 만료되었는데 refresh_token이 없거나 갱신 실패 시 반드시 미로그인('red') 반환.
    - attempt_refresh=True(정밀 새로고침·테스트 모드): 만료된 토큰을 실제 creds.refresh(Request())로 갱신 검증 후 저장."""
    migrate_legacy_auth_files()
    secret_path = get_auth_file('client_secret.json')
    token_path = get_auth_file('youtube_token.json')
    has_secret = _is_valid_youtube_client_secret(secret_path)

    if not os.path.exists(token_path) or os.path.getsize(token_path) <= 10:
        reason = (
            '미로그인 · 유튜브 OAuth 토큰(auth_data/youtube_token.json)이 없습니다. 구글 계정 인증을 먼저 진행해 주세요.'
            if has_secret else
            '미설정 · auth_data/client_secret.json과 유튜브 OAuth 토큰이 모두 없습니다.'
        )
        return {
            'ok': False,
            'state': 'red',
            'has_secret': has_secret,
            'has_token': False,
            'reason': reason,
        }

    try:
        creds = Credentials.from_authorized_user_file(token_path, YOUTUBE_SCOPES)
    except Exception as e:
        return {
            'ok': False,
            'state': 'red',
            'has_secret': has_secret,
            'has_token': False,
            'reason': f'유튜브 OAuth 토큰 파일이 손상되었거나 형식이 올바르지 않습니다({type(e).__name__}). 재인증해 주세요.',
        }

    if not creds:
        return {
            'ok': False,
            'state': 'red',
            'has_secret': has_secret,
            'has_token': False,
            'reason': '유튜브 OAuth 토큰을 불러올 수 없습니다. 재인증해 주세요.',
        }

    if creds.valid:
        if not has_secret:
            return {
                'ok': False,
                'state': 'red',
                'has_secret': False,
                'has_token': True,
                'reason': '유튜브 OAuth 토큰은 있으나 auth_data/client_secret.json이 없거나 손상되었습니다.',
            }
        return {
            'ok': True,
            'state': 'green',
            'has_secret': True,
            'has_token': True,
            'reason': '유튜브 OAuth 토큰 유효 확인 완료',
        }

    # 액세스 토큰 만료 상태: refresh_token 보유 및 갱신 가능 여부 엄격 검증
    has_refresh = bool(getattr(creds, 'refresh_token', None))
    if not has_refresh:
        return {
            'ok': False,
            'state': 'red',
            'has_secret': has_secret,
            'has_token': False,
            'reason': '유튜브 OAuth 토큰이 만료되었고 갱신 토큰(refresh_token)이 없어 재인증이 필요합니다.',
        }

    if not getattr(creds, 'client_id', None) or not getattr(creds, 'client_secret', None):
        return {
            'ok': False,
            'state': 'red',
            'has_secret': has_secret,
            'has_token': False,
            'reason': '유튜브 OAuth 토큰의 클라이언트 정보가 누락되어 갱신할 수 없습니다. 재인증해 주세요.',
        }

    if attempt_refresh:
        try:
            creds.refresh(Request())
            if not creds.valid:
                raise RuntimeError('토큰 갱신 후에도 유효 상태가 아닙니다.')
            try:
                tmp_path = f"{token_path}.tmp"
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    f.write(creds.to_json())
                os.replace(tmp_path, token_path)
            except Exception:
                pass
        except Exception as e:
            return {
                'ok': False,
                'state': 'red',
                'has_secret': has_secret,
                'has_token': False,
                'reason': f'유튜브 OAuth 토큰 만료·갱신 실패({type(e).__name__}: {str(e)[:80]}) — 구글 계정 재인증이 필요합니다.',
            }
        if not has_secret:
            return {
                'ok': False,
                'state': 'red',
                'has_secret': False,
                'has_token': True,
                'reason': '유튜브 OAuth 토큰은 갱신되었으나 auth_data/client_secret.json이 없습니다.',
            }
        return {
            'ok': True,
            'state': 'green',
            'has_secret': True,
            'has_token': True,
            'reason': '유튜브 OAuth 토큰 자동 갱신 및 유효성 검증 완료',
        }

    if not has_secret:
        return {
            'ok': False,
            'state': 'red',
            'has_secret': False,
            'has_token': False,
            'reason': 'auth_data/client_secret.json이 없어 유튜브 OAuth 인증을 사용할 수 없습니다.',
        }
    return {
        'ok': True,
        'state': 'green',
        'has_secret': True,
        'has_token': True,
        'reason': '유튜브 OAuth 토큰 확인됨 (만료 시 자동 갱신 가능)',
    }


def worker_youtube_auth():
    """온보딩 1단계 [🔑 지금 구글 계정 인증하기] 전용 워커:
    auth_data/client_secret.json으로 로컬 브라우저 OAuth 인증(120초 타임아웃)을 실행해
    auth_data/youtube_token.json을 저장하고, 종료 시 대시보드·온보딩 신호등을 즉시 갱신."""
    global ACTIVE_WORKER_THREAD
    with STATE_LOCK:
        STATE['busy'] = True
        STATE['stop_requested'] = False
        STATE['active_run_id'] += 1
        my_run_id = STATE['active_run_id']
        ACTIVE_WORKER_THREAD = threading.current_thread()

    try:
        migrate_legacy_auth_files()
        secret_path = get_auth_file('client_secret.json')
        token_path = get_auth_file('youtube_token.json')
        if not _is_valid_youtube_client_secret(secret_path):
            log('⚠️ [유튜브 인증] auth_data/client_secret.json 파일이 없거나 형식이 올바르지 않습니다.')
            return

        log('🔑 [유튜브 인증] 브라우저에서 구글 계정 인증 창을 여는 중입니다... (120초 이내 로그인·권한 허용 완료)')
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, YOUTUBE_SCOPES)
        try:
            try:
                creds = flow.run_local_server(port=0, prompt='select_account', timeout_seconds=120)
            except TypeError:
                creds = flow.run_local_server(port=0, prompt='select_account')
        except Exception as auth_err:
            log(f'⚠️ [유튜브 인증] 구글 계정 인증이 취소되었거나 시간이 초과(120초)되었습니다: {auth_err}')
            return

        if not (creds and creds.valid):
            log('⚠️ [유튜브 인증] 유효한 인증 토큰을 받지 못했습니다. 다시 시도해 주세요.')
            return

        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        tmp_path = f'{token_path}.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())
        os.replace(tmp_path, token_path)
        log('✅ [유튜브 인증] 구글 계정 인증 및 youtube_token.json 저장이 완료되었습니다! (유튜브 쇼츠 🟢 연결됨)')
    except Exception as e:
        log(f'⚠️ [유튜브 인증 오류] {e}')
    finally:
        invalidate_deep_status_cache()
        with STATE_LOCK:
            STATE['auth_check_seq'] = int(STATE.get('auth_check_seq', 0)) + 1
            if STATE['active_run_id'] == my_run_id:
                STATE['busy'] = False
                STATE['stop_requested'] = False
            elif not STATE['busy']:
                STATE['stop_requested'] = False
            if ACTIVE_WORKER_THREAD is threading.current_thread():
                ACTIVE_WORKER_THREAD = None


DEEP_STATUS_COOLDOWN_SEC = 30 * 60  # 30분 쿨다운 캐시 (대시보드 재로딩 시 불필요한 네이버/SNS 로그인 이력 누적 방지)
DEEP_STATUS_CACHE_LOCK = threading.Lock()
DEEP_STATUS_CACHE = {
    'checked_at': 0.0,
    'result': None,
}


def invalidate_deep_status_cache():
    """로그인 창 닫힘·로그아웃·초기화 직후 30분 정밀 점검 캐시를 즉시 무효화."""
    with DEEP_STATUS_CACHE_LOCK:
        DEEP_STATUS_CACHE['checked_at'] = 0.0
        DEEP_STATUS_CACHE['result'] = None


def get_setup_status(deep=False, force=False):
    """8개 플랫폼 로그인/API 상태 신호등(green/yellow/red) 및 쿠키 경과 일수 반환.
    - deep=True & force=False: 최근 30분 이내 정밀 점검 캐시가 있으면 외부 접속 없이 캐시 결과를 즉시 반환
    - deep=True & force=True (수동 새로고침·로그인 창 닫힘·로그아웃 직후): 캐시 무시 후 즉시 1회 정밀 점검"""
    migrate_legacy_auth_files()
    now_ts = time.time()
    if deep and not force:
        with DEEP_STATUS_CACHE_LOCK:
            cached_res = DEEP_STATUS_CACHE.get('result')
            cached_at = float(DEEP_STATUS_CACHE.get('checked_at') or 0.0)
            age_sec = now_ts - cached_at
            if isinstance(cached_res, dict) and 0 <= age_sec < DEEP_STATUS_COOLDOWN_SEC:
                prof = load_profile()
                out = json.loads(json.dumps(cached_res))
                out['profile_ids'] = {
                    'naver_blog_id': prof.get('naver_blog_id', ''),
                    'pinterest_id': prof.get('pinterest_id', ''),
                    'instagram_id': prof.get('instagram_id', ''),
                }
                out['deep_cached'] = True
                out['deep_cache_age_sec'] = int(age_sec)
                return out

    yt_check = validate_youtube_oauth_token(attempt_refresh=bool(deep))
    yt_state = yt_check['state']
    has_secret = yt_check['has_secret']
    has_token = yt_check['has_token']

    naver_state, naver_age = _check_cookie_status_and_age('naver_cookies.json', deep=deep)
    tt_state, tt_age = _check_cookie_status_and_age('tiktok_cookies.json', deep=deep)
    ig_state, ig_age = _check_cookie_status_and_age('instagram_cookies.json', deep=deep)
    pin_state, pin_age = _check_cookie_status_and_age('pinterest_cookies.json', deep=deep)
    fb_state, fb_age = _check_cookie_status_and_age('facebook_cookies.json', deep=deep)

    prof = load_profile()
    res = {
        'yt': yt_state,
        'naver': naver_state,
        'clip': naver_state,
        'tt': tt_state,
        'ig': ig_state,
        'pin': pin_state,
        'fb': fb_state,
        'fb_page': fb_state,
        'cookie_ages': {
            'naver': naver_age,
            'clip': naver_age,
            'tt': tt_age,
            'ig': ig_age,
            'pin': pin_age,
            'fb': fb_age,
            'fb_page': fb_age,
        },
        'profile_ids': {
            'naver_blog_id': prof.get('naver_blog_id', ''),
            'pinterest_id': prof.get('pinterest_id', ''),
            'instagram_id': prof.get('instagram_id', ''),
        },
        'has_client_secret': has_secret,
        'has_youtube_token': has_token,
        'yt_reason': yt_check['reason'],
        'deep_cached': False,
        'deep_cache_age_sec': 0,
    }
    if deep:
        with DEEP_STATUS_CACHE_LOCK:
            DEEP_STATUS_CACHE['checked_at'] = time.time()
            DEEP_STATUS_CACHE['result'] = json.loads(json.dumps(res))
    return res


def _win_no_window_kwargs():
    """Windows(pythonw.exe) 환경에서 서브프로세스 실행 시 검은 콘솔 창 깜빡임 방지 옵션 반환"""
    if sys.platform == 'win32':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    return {}


def get_ffmpeg_bin():
    """시스템 PATH의 ffmpeg를 우선 반환하고, 없으면 imageio-ffmpeg 내장 바이너리 경로로 자동 폴백"""
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return 'ffmpeg'


def get_ffprobe_bin():
    """시스템 PATH의 ffprobe 경로를 반환 (미설치 시 빈 문자열)"""
    return shutil.which('ffprobe') or ''


def _parse_ffmpeg_inspect_stderr(stderr_text):
    """ffmpeg -i stderr 출력에서 코덱·픽셀포맷·HDR·해상도·회전·재생시간(초)을 안전 파싱"""
    parsed = {
        'codec_name': '',
        'pix_fmt': '',
        'color_transfer': '',
        'color_primaries': '',
        'width': 0,
        'height': 0,
        'duration_sec': 0.0,
    }
    text = str(stderr_text or '')
    if not text:
        return parsed

    m_dur = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    if m_dur:
        try:
            parsed['duration_sec'] = int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))
        except Exception:
            pass

    video_line = ''
    for ln in text.splitlines():
        if re.search(r'Stream\s+#\d+:\d+.*:\s*Video:', ln):
            video_line = ln
            break
    if video_line:
        m_codec = re.search(r'Video:\s*([a-zA-Z0-9_]+)', video_line)
        if m_codec:
            parsed['codec_name'] = m_codec.group(1).strip().lower()
        m_pix = re.search(r'\b(yuv[a-z0-9]+|nv12|p010le|rgb[a-z0-9]*|bgr[a-z0-9]*)\b', video_line, re.IGNORECASE)
        if m_pix:
            parsed['pix_fmt'] = m_pix.group(1).strip().lower()
        low_vl = video_line.lower()
        if 'arib-std-b67' in low_vl:
            parsed['color_transfer'] = 'arib-std-b67'
        elif 'smpte2084' in low_vl:
            parsed['color_transfer'] = 'smpte2084'
        elif 'bt709' in low_vl:
            parsed['color_transfer'] = 'bt709'
        if 'bt2020' in low_vl:
            parsed['color_primaries'] = 'bt2020'
        elif 'bt709' in low_vl:
            parsed['color_primaries'] = 'bt709'
        m_dim = re.search(r',\s*(\d{2,5})x(\d{2,5})(?:\s|\[|,|$)', video_line)
        if m_dim:
            try:
                w, h = int(m_dim.group(1)), int(m_dim.group(2))
                rot = 0
                m_rot = re.search(r'rotate\s*:\s*(-?\d+)', text, re.IGNORECASE)
                if not m_rot:
                    m_rot = re.search(r'displaymatrix:\s*rotation\s*of\s*(-?\d+(?:\.\d+)?)', text, re.IGNORECASE)
                if m_rot:
                    rot = int(round(float(m_rot.group(1))))
                if abs(rot) in (90, 270):
                    w, h = h, w
                parsed['width'], parsed['height'] = w, h
            except Exception:
                pass
    return parsed


def _inspect_video_with_ffmpeg(video_path):
    """ffprobe 미설치 PC용 ffmpeg -i stderr 메타데이터/코덱 조회 헬퍼"""
    try:
        proc = subprocess.run(
            [get_ffmpeg_bin(), '-hide_banner', '-i', video_path],
            capture_output=True, text=True, encoding='utf-8', errors='ignore',
            timeout=10, **_win_no_window_kwargs(),
        )
        return _parse_ffmpeg_inspect_stderr(proc.stderr)
    except Exception:
        return _parse_ffmpeg_inspect_stderr('')


def probe_video_codec_info(video_path):
    """ffprobe(또는 ffmpeg -i 폴백)로 영상 0번 비디오 스트림의 코덱·픽셀포맷·HDR 색공간 정보를 조회"""
    info = {
        'codec_name': '',
        'pix_fmt': '',
        'color_transfer': '',
        'color_primaries': '',
    }
    if not video_path or not os.path.isfile(video_path):
        return info
    ffprobe_bin = get_ffprobe_bin()
    if ffprobe_bin:
        try:
            proc = subprocess.run(
                [
                    ffprobe_bin, '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=codec_name,pix_fmt,color_transfer,color_primaries',
                    '-of', 'json', video_path,
                ],
                capture_output=True, text=True, timeout=10, **_win_no_window_kwargs(),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                streams = parsed.get('streams') or []
                if streams and isinstance(streams[0], dict):
                    st = streams[0]
                    info['codec_name'] = str(st.get('codec_name') or '').strip().lower()
                    info['pix_fmt'] = str(st.get('pix_fmt') or '').strip().lower()
                    info['color_transfer'] = str(st.get('color_transfer') or '').strip().lower()
                    info['color_primaries'] = str(st.get('color_primaries') or '').strip().lower()
        except Exception:
            pass
    if not info['codec_name']:
        fb = _inspect_video_with_ffmpeg(video_path)
        for k in ('codec_name', 'pix_fmt', 'color_transfer', 'color_primaries'):
            if fb.get(k) and not info.get(k):
                info[k] = fb[k]
    return info


def is_browser_standard_h264(video_path, codec_info=None):
    """브라우저 <video>에서 변환 없이 바로 재생 가능한 표준 H.264(8-bit yuv420p SDR .mp4) 여부 판별"""
    if not video_path or not os.path.isfile(video_path):
        return False
    ext = os.path.splitext(video_path)[1].lower()
    ci = codec_info if isinstance(codec_info, dict) else probe_video_codec_info(video_path)
    codec = ci.get('codec_name') or ''
    pix_fmt = ci.get('pix_fmt') or ''
    ctrc = ci.get('color_transfer') or ''
    is_hdr = ctrc in ('arib-std-b67', 'smpte2084') or '10' in pix_fmt or '12' in pix_fmt
    return ext == '.mp4' and codec == 'h264' and pix_fmt == 'yuv420p' and not is_hdr


def ensure_browser_preview_video(video_path):
    """미리보기 플레이어(<video>)용 호환 영상 URL을 반환.
    - 이미 표준 H.264(yuv420p, SDR, .mp4)면 변환 없이 원본 /video/<fname> 바로 재생
    - HEVC(H.265), 10-bit HDR, VP9, .mov 등 비호환 코덱이면 temp_thumbs/에 저용량 H.264(yuv420p) 임시 미리보기 생성
    - 원본 영상(upload/)은 절대 수정하지 않으며, 실제 SNS 업로드에는 항상 원본을 사용."""
    if not video_path or not os.path.isfile(video_path):
        return {'ok': False, 'preview_url': '', 'transcoded': False, 'codec': '', 'pix_fmt': ''}

    basename = os.path.basename(video_path)
    direct_url = f"/video/{quote(basename)}"
    ci = probe_video_codec_info(video_path)
    codec = ci.get('codec_name') or 'unknown'
    pix_fmt = ci.get('pix_fmt') or 'unknown'
    ctrc = ci.get('color_transfer') or ''

    if is_browser_standard_h264(video_path, ci):
        return {
            'ok': True,
            'preview_url': direct_url,
            'transcoded': False,
            'codec': codec,
            'pix_fmt': pix_fmt,
            'color_transfer': ctrc,
        }

    os.makedirs(THUMB_DIR, exist_ok=True)
    try:
        st = os.stat(video_path)
        sig = f"{basename}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        sig = basename
    cache_key = hashlib.sha1(sig.encode('utf-8')).hexdigest()[:12]
    preview_fname = f"preview_{cache_key}.mp4"
    preview_path = os.path.join(THUMB_DIR, preview_fname)
    preview_url = f"/preview_video/{quote(preview_fname)}"

    with PREVIEW_TRANSCODE_LOCK:
        if os.path.isfile(preview_path) and os.path.getsize(preview_path) > 1024:
            return {
                'ok': True,
                'preview_url': preview_url,
                'transcoded': True,
                'cached': True,
                'codec': codec,
                'pix_fmt': pix_fmt,
                'color_transfer': ctrc,
            }

        tmp_out = os.path.join(THUMB_DIR, f"preview_{cache_key}_{os.getpid()}.tmp.mp4")
        hdr_note = f"/{ctrc}" if ctrc in ('arib-std-b67', 'smpte2084') else ""
        log(f"🎬 [미리보기 호환 변환] {codec}/{pix_fmt}{hdr_note} 감지 → H.264(yuv420p) 미리보기 생성 중... (업로드는 원본 사용)")
        try:
            proc = subprocess.run(
                [
                    get_ffmpeg_bin(), '-y', '-i', video_path,
                    '-map', '0:v:0', '-an',
                    '-vf', "scale='trunc(min(720,iw)/2)*2':-2:flags=fast_bilinear,format=yuv420p",
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
                    '-pix_fmt', 'yuv420p',
                    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                    '-movflags', '+faststart',
                    tmp_out,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                **_win_no_window_kwargs(),
            )
            if proc.returncode == 0 and os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 1024:
                os.replace(tmp_out, preview_path)
                mb = round(os.path.getsize(preview_path) / (1024.0 * 1024.0), 2)
                log(f"✅ [미리보기 호환 변환 완료] {preview_fname} ({mb}MB · temp_thumbs 저장, 원본 유지)")
                return {
                    'ok': True,
                    'preview_url': preview_url,
                    'transcoded': True,
                    'cached': False,
                    'codec': codec,
                    'pix_fmt': pix_fmt,
                    'color_transfer': ctrc,
                }
        except Exception as e:
            log(f"⚠️ [미리보기 호환 변환 실패] 원본 직접 재생으로 폴백합니다: {e}")
        finally:
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except Exception:
                    pass

    return {
        'ok': True,
        'preview_url': direct_url,
        'transcoded': False,
        'codec': codec,
        'pix_fmt': pix_fmt,
        'color_transfer': ctrc,
    }

def probe_video_metadata(video_path):
    """영상 길이·해상도·용량·비율 측정 및 플랫폼별 규격 사전 경고 반환 (cv2 우선, ffprobe 폴백)"""
    meta = {
        'ok': False,
        'duration_sec': 0.0,
        'width': 0,
        'height': 0,
        'size_mb': 0.0,
        'orientation': '알 수 없음',
        'warnings': [],
    }
    if not video_path or not os.path.exists(video_path):
        return meta

    try:
        meta['size_mb'] = round(os.path.getsize(video_path) / (1024 * 1024), 2)
    except Exception:
        pass

    width, height, duration = 0, 0, 0.0
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            if fps > 0 and frames > 0:
                duration = frames / fps
        cap.release()
    except Exception:
        pass

    if width <= 0 or height <= 0 or duration <= 0:
        ffprobe_bin = get_ffprobe_bin()
        if ffprobe_bin:
            try:
                proc = subprocess.run(
                    [
                        ffprobe_bin, '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height,side_data_list:stream_tags=rotate',
                        '-show_entries', 'format=duration',
                        '-of', 'json', video_path,
                    ],
                    capture_output=True, text=True, timeout=10, **_win_no_window_kwargs(),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    info = json.loads(proc.stdout)
                    streams = info.get('streams') or []
                    if streams:
                        st = streams[0]
                        w = int(st.get('width') or 0)
                        h = int(st.get('height') or 0)
                        rot = 0
                        try:
                            rot = int((st.get('tags') or {}).get('rotate') or 0)
                        except Exception:
                            rot = 0
                        if rot == 0:
                            for sd in (st.get('side_data_list') or []):
                                if isinstance(sd, dict) and sd.get('rotation') is not None:
                                    try:
                                        rot = int(round(float(sd.get('rotation'))))
                                        break
                                    except Exception:
                                        pass
                        if abs(rot) in (90, 270):
                            w, h = h, w
                        if width <= 0:
                            width = w
                        if height <= 0:
                            height = h
                    fmt = info.get('format') or {}
                    if duration <= 0 and fmt.get('duration'):
                        duration = float(fmt.get('duration'))
            except Exception:
                pass
        if width <= 0 or height <= 0 or duration <= 0:
            fb = _inspect_video_with_ffmpeg(video_path)
            if width <= 0 and fb.get('width', 0) > 0:
                width = fb['width']
            if height <= 0 and fb.get('height', 0) > 0:
                height = fb['height']
            if duration <= 0 and fb.get('duration_sec', 0.0) > 0:
                duration = fb['duration_sec']

    meta['width'] = width
    meta['height'] = height
    meta['duration_sec'] = round(duration, 1)
    if width > 0 and height > 0:
        if height > width:
            meta['orientation'] = '세로'
        elif width > height:
            meta['orientation'] = '가로'
        else:
            meta['orientation'] = '정방형'
        meta['ok'] = True
    elif duration > 0 or meta['size_mb'] > 0:
        meta['ok'] = True

    warnings = []
    if meta['duration_sec'] > 60:
        warnings.append('60초 초과 · 유튜브 쇼츠/네이버 클립 길이 제한 주의')
    if meta['duration_sec'] > 90:
        warnings.append('90초 초과 · 인스타그램 릴스 길이 제한 주의')
    if meta['orientation'] == '가로':
        warnings.append('가로 영상 · 숏폼(9:16 세로) 플랫폼 비율 주의')
    meta['warnings'] = warnings
    return meta

def _empty_daily_usage(today_str):
    counts = {'yt': 0}
    for k in SAFE_DAILY_LIMITS:
        counts[k] = 0
    return {'date': today_str, 'counts': counts}

def load_daily_usage():
    """오늘 날짜(YYYY-MM-DD) 기준 일일 사용량 로드 (날짜 변경 시 자동 0 초기화)"""
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    with USAGE_LOCK:
        data = None
        if os.path.exists(DAILY_USAGE_PATH):
            try:
                with open(DAILY_USAGE_PATH, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded.get('date') == today_str:
                    data = loaded
            except Exception:
                data = None
        if not data:
            data = _empty_daily_usage(today_str)
            try:
                with open(DAILY_USAGE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        counts = data.setdefault('counts', {})
        counts.setdefault('yt', 0)
        for k in SAFE_DAILY_LIMITS:
            counts.setdefault(k, 0)
        return data

def record_daily_usage(platform_key):
    """플랫폼 업로드 성공 시 당일 카운트 +1 저장"""
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    with USAGE_LOCK:
        data = None
        if os.path.exists(DAILY_USAGE_PATH):
            try:
                with open(DAILY_USAGE_PATH, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded.get('date') == today_str:
                    data = loaded
            except Exception:
                data = None
        if not data:
            data = _empty_daily_usage(today_str)
        counts = data.setdefault('counts', {})
        counts[platform_key] = int(counts.get(platform_key, 0)) + 1
        try:
            with open(DAILY_USAGE_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return data

def get_quota_status():
    """유튜브 일일 포인트 한도 + 8개 플랫폼 계정 보호 권장선 사용량 반환"""
    data = load_daily_usage()
    counts = data.get('counts', {})
    yt_cnt = int(counts.get('yt', 0))
    platforms_info = {}
    for k, lim in SAFE_DAILY_LIMITS.items():
        platforms_info[k] = {'used': int(counts.get(k, 0)), 'limit': lim}
    return {
        'ok': True,
        'date': data.get('date'),
        'youtube': {
            'used_points': yt_cnt * YOUTUBE_POINTS_PER_UPLOAD,
            'max_points': YOUTUBE_MAX_POINTS,
            'used_count': yt_cnt,
            'max_count': YOUTUBE_MAX_COUNT,
            'points_per_upload': YOUTUBE_POINTS_PER_UPLOAD,
        },
        'platforms': platforms_info,
        'transform_cost': 0,
    }

def _handle_remove_readonly(func, path, exc_info):
    """Windows 읽기 전용(Read-only) 파일·폴더 권한을 해제한 뒤 재삭제"""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass

def _force_rmtree(path):
    """Python 3.12+(onexc) 및 하위 버전(onerror) 모두 호환되는 안전 폴더 영구 삭제"""
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_handle_remove_readonly)
        else:
            shutil.rmtree(path, onerror=_handle_remove_readonly)
        return not os.path.exists(path)
    except Exception:
        return False

def reset_personal_data(confirm_text):
    """confirm_text가 정확히 '초기화'일 때만 개인·임시 데이터 삭제 (순수 코드, updater_config/version/.gitignore/.installed_done/_설치완료_보관함 보호)"""
    if confirm_text != '초기화':
        return {'ok': False, 'msg': "'초기화' 세 글자를 정확히 입력해야 초기화가 진행됩니다."}
    with STATE_LOCK:
        is_busy = STATE.get('busy')
    if is_busy:
        return {'ok': False, 'msg': BUSY_TASK_MSG}

    deleted_files = []
    exact_targets = [
        'youtube_token.json', 'client_secret.json',
        'user_profile.json', 'daily_usage.json', 'login_credentials.json', 'app.log',
        'report.md', 'errorReport.md',
    ]
    glob_patterns = ['*_cookies.json', '블랙박스_*.webm']

    for fname in exact_targets:
        fp = os.path.join(BASE_DIR, fname)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                deleted_files.append(fname)
            except Exception:
                pass

    for pat in glob_patterns:
        for fp in glob.glob(os.path.join(BASE_DIR, pat)):
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                    deleted_files.append(os.path.basename(fp))
                except Exception:
                    pass

    for dpath in [AUTH_DIR, BLACKBOX_DIR, UPLOAD_DIR, DONE_DIR, THUMB_DIR]:
        if os.path.isdir(dpath):
            for entry in os.listdir(dpath):
                full_p = os.path.join(dpath, entry)
                try:
                    if os.path.isdir(full_p):
                        _force_rmtree(full_p)
                    else:
                        os.remove(full_p)
                    deleted_files.append(f'{os.path.basename(dpath)}/{entry}')
                except Exception:
                    pass

    # 구버전 분리 폴더(blackbox_login, blackbox_upload, debug_records)가 남아 있으면 영구 삭제
    for legacy_bb in (LEGACY_BLACKBOX_LOGIN_DIR, LEGACY_BLACKBOX_UPLOAD_DIR, LEGACY_DEBUG_DIR):
        if os.path.exists(legacy_bb):
            if _force_rmtree(legacy_bb):
                deleted_files.append(os.path.basename(legacy_bb) + '/')

    # OS 무관하게 _old_backup 폴더 영구 삭제
    if os.path.exists(BACKUP_DIR):
        if _force_rmtree(BACKUP_DIR):
            deleted_files.append('_old_backup/')

    # Windows(직원 PC)에서만 .git 폴더 영구 삭제 (macOS 원장님 마스터 PC의 .git은 절대 보호)
    if sys.platform == 'win32':
        git_dir = os.path.join(BASE_DIR, '.git')
        if os.path.exists(git_dir):
            if _force_rmtree(git_dir):
                deleted_files.append('.git/')

    with STATE_LOCK:
        STATE['video_path'] = ''
        STATE['video_name'] = ''
        STATE['last_results'] = {}
        STATE['logs'] = ['🧹 개인 데이터 초기화가 완료되었습니다. 초기 설정을 진행해 주세요.']
    return {
        'ok': True,
        'msg': f'개인 정보·쿠키·임시 파일이 모두 초기화되었습니다! (삭제 항목 {len(deleted_files)}건 + 임시 폴더 비움)',
    }

def _kill_automation_chrome():
    """Playwright가 띄운 전용 영구 프로필(chrome_profile) 자동화 크롬 프로세스만 타겟팅하여 안전 종료(graceful → 강제) (개인 크롬 창은 절대 유지)"""
    try:
        profile_marker = 'chrome_profile'
        if sys.platform == 'win32':
            ps_cmd = (
                "$procs = @(Get-CimInstance Win32_Process | "
                "Where-Object { ($_.Name -match 'chrome|chromium') -and ($_.CommandLine -like '*chrome_profile*') }); "
                "if ($procs.Count -gt 0) { "
                "$procs | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }; "
                "Start-Sleep -Milliseconds 300; "
                "$procs | ForEach-Object { "
                "if (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue) { "
                "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } }"
            )
            subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        else:
            import signal
            my_pid = os.getpid()
            proc = subprocess.run(
                ['ps', '-ax', '-o', 'pid=,command='],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout:
                target_pids = []
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) < 2:
                        continue
                    try:
                        pid = int(parts[0])
                    except Exception:
                        continue
                    if pid == my_pid:
                        continue
                    cmd_low = parts[1].lower()
                    if ('chrome' in cmd_low or 'chromium' in cmd_low) and profile_marker in cmd_low:
                        target_pids.append(pid)
                if target_pids:
                    for pid in target_pids:
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass
                    time.sleep(0.2)
                    for pid in target_pids:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            continue
                        except Exception:
                            pass
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except Exception:
                            pass
    except Exception:
        pass


def _cleanup_paused_stop_browser_if_needed():
    """직전 강제 중지로 열어둔 자동화 브라우저가 남아 있다면 새 작업 시작 전에 안전하게 닫고 프로필 잠금을 정리"""
    with STATE_LOCK:
        left_open = bool(STATE.get('browser_left_open_by_stop'))
    if not (left_open or has_held_playwright_sessions() or is_force_stop_keep_browser()):
        return False
    log('🧹 [사전 정리] 직전 강제 중지로 열어둔 자동화 브라우저 창을 안전하게 닫고 새 작업을 시작합니다...')
    release_held_playwright_sessions(kill_chrome_fn=_kill_automation_chrome)
    if sys.platform == 'win32':
        wait_chrome_profile_unlock(timeout_sec=3.0)
    with STATE_LOCK:
        STATE['browser_left_open_by_stop'] = False
    return True


def _rescue_debug_webms_into_dir(target_dir):
    """강제 중지 등으로 blackbox/_tmp/_rec_* 폴더에 남은 비어 있지 않은 .webm 녹화본을 target_dir로 안전 회수"""
    if not target_dir or not os.path.isdir(BLACKBOX_TMP_DIR):
        return 0
    os.makedirs(target_dir, exist_ok=True)
    keep_open_mode = is_force_stop_keep_browser()
    rescued = 0
    existing = [f for f in os.listdir(target_dir) if f.lower().endswith('.webm')]
    next_idx = len(existing) + 1
    entries = []
    for entry in os.listdir(BLACKBOX_TMP_DIR):
        sub_p = os.path.join(BLACKBOX_TMP_DIR, entry)
        if os.path.isdir(sub_p) and entry.startswith('_rec_') and '_login_' not in entry and not entry.endswith('_login'):
            try:
                entries.append((os.path.getmtime(sub_p), entry, sub_p))
            except Exception:
                entries.append((0, entry, sub_p))
    entries.sort(key=lambda x: x[0])
    for _, entry, sub_p in entries:
        webms = [
            os.path.join(sub_p, f)
            for f in os.listdir(sub_p)
            if f.lower().endswith('.webm') and os.path.isfile(os.path.join(sub_p, f))
        ]
        non_empty = []
        for w in webms:
            try:
                if os.path.getsize(w) > 0:
                    non_empty.append(w)
            except Exception:
                pass
        if non_empty:
            newest_w = max(non_empty, key=lambda x: os.path.getmtime(x))
            safe_tag = re.sub(r'[^\w가-힣-]+', '_', entry.replace('_rec_', ''))[:32]
            dst = os.path.join(target_dir, f"{next_idx:02d}_rescued_{safe_tag}.webm")
            if keep_open_mode:
                try:
                    shutil.copy2(newest_w, dst)
                    next_idx += 1
                    rescued += 1
                except Exception:
                    pass
            else:
                try:
                    shutil.move(newest_w, dst)
                    next_idx += 1
                    rescued += 1
                except Exception:
                    try:
                        shutil.copy2(newest_w, dst)
                        next_idx += 1
                        rescued += 1
                    except Exception:
                        pass
        if not keep_open_mode:
            _force_rmtree(sub_p)
    return rescued


def merge_blackbox_session_to_mp4(session_dir, out_dir=None, start_dt=None, is_test_mode=False):
    """1회 실행(테스트 모드 또는 일반 업로드) 중 수집된 각 플랫폼 .webm 블랙박스 클립을 FFmpeg로 단일 MP4로 합본해
    통합 blackbox/ 폴더에 upload.mp4 또는 test.mp4 고정 파일명으로 덮어쓰기 저장."""
    if not session_dir:
        return ''
    target_dir = out_dir or BLACKBOX_DIR
    with TEST_MERGE_LOCK:
        _rescue_debug_webms_into_dir(session_dir)
        if not os.path.isdir(session_dir):
            return ''
        raw_clips = []
        for fn in sorted(os.listdir(session_dir)):
            fp = os.path.join(session_dir, fn)
            if os.path.isfile(fp) and fn.lower().endswith(('.webm', '.mp4')):
                try:
                    if os.path.getsize(fp) > 512:
                        raw_clips.append(fp)
                except Exception:
                    pass
        if not raw_clips:
            _force_rmtree(session_dir)
            return ''

        dt = start_dt if isinstance(start_dt, datetime.datetime) else datetime.datetime.now()
        kind = 'test' if is_test_mode else 'upload'
        out_name = format_blackbox_filename(kind, 'mp4', dt=dt, is_report=False)
        os.makedirs(target_dir, exist_ok=True)
        out_path = os.path.join(target_dir, out_name)

        norm_dir = os.path.join(session_dir, '_norm')
        os.makedirs(norm_dir, exist_ok=True)
        norm_clips = []
        for idx_c, src_clip in enumerate(raw_clips, start=1):
            norm_mp4 = os.path.join(norm_dir, f"part_{idx_c:02d}.mp4")
            try:
                proc = subprocess.run(
                    [
                        get_ffmpeg_bin(), '-y', '-err_detect', 'ignore_err', '-i', src_clip,
                        '-map', '0:v:0', '-an',
                        '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=15,format=yuv420p',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                        norm_mp4,
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                    **_win_no_window_kwargs(),
                )
                if proc.returncode == 0 and os.path.isfile(norm_mp4) and os.path.getsize(norm_mp4) > 512:
                    norm_clips.append(norm_mp4)
            except Exception:
                pass

        saved_ok = False
        if len(norm_clips) == 1:
            try:
                shutil.copy2(norm_clips[0], out_path)
                saved_ok = os.path.isfile(out_path) and os.path.getsize(out_path) > 512
            except Exception:
                saved_ok = False
        elif len(norm_clips) >= 2:
            list_txt = os.path.join(norm_dir, 'concat_list.txt')
            try:
                with open(list_txt, 'w', encoding='utf-8') as lf:
                    for nc in norm_clips:
                        escaped = nc.replace("'", "'\\''")
                        lf.write(f"file '{escaped}'\n")
                proc = subprocess.run(
                    [
                        get_ffmpeg_bin(), '-y', '-f', 'concat', '-safe', '0', '-i', list_txt,
                        '-c', 'copy', '-movflags', '+faststart', out_path,
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                    **_win_no_window_kwargs(),
                )
                saved_ok = proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 512
            except Exception:
                saved_ok = False

        _force_rmtree(session_dir)
        if saved_ok:
            prune_blackbox_dir(max_keep=3)
            if is_test_mode:
                log(f"🎬 테스트 블랙박스 합본 영상이 저장되었습니다: blackbox/{out_name} (고정 파일 덮어쓰기 · 오류 문의 시 [📂 오류 폴더 열기]로 첨부해 주세요)")
            else:
                log(f"🎬 업로드 블랙박스 합본 영상이 저장되었습니다: blackbox/{out_name} (고정 파일 덮어쓰기)")
            return out_path
        if is_test_mode:
            log("⚠️ [테스트 모드] 합본할 수 있는 유효한 블랙박스 녹화본이 없어 blackbox 저장을 건너뛰었습니다.")
        else:
            log("⚠️ [업로드 블랙박스] 합본할 수 있는 유효한 녹화본이 없어 MP4 저장을 건너뛰었습니다.")
        return ''


def _finalize_blackbox_session_merge_once():
    """실행(테스트 모드 또는 일반 업로드) 세션당 정확히 1회만 FFmpeg 단일 MP4 합본을 blackbox/에 저장"""
    with STATE_LOCK:
        if STATE.get('test_merged_done'):
            return ''
        sdir = STATE.get('test_session_dir') or ''
        sdt = STATE.get('test_start_dt')
        is_test = bool(STATE.get('is_test_mode'))
        STATE['test_merged_done'] = True
        STATE['is_test_mode'] = False
        STATE['test_session_dir'] = ''
        STATE['test_start_dt'] = None
    set_blackbox_session_dir('')
    if not sdir:
        return ''
    return merge_blackbox_session_to_mp4(sdir, BLACKBOX_DIR, start_dt=sdt, is_test_mode=is_test)


def _finalize_test_mode_merge_once():
    """기존 호환 래퍼"""
    return _finalize_blackbox_session_merge_once()


def _format_duration_ko(elapsed_sec):
    sec = max(0.0, float(elapsed_sec or 0.0))
    if sec < 60.0:
        return f"{sec:.1f}초"
    mins = int(sec // 60)
    rem = sec - (mins * 60)
    if mins < 60:
        return f"{mins}분 {rem:.1f}초 ({sec:.1f}초)"
    hrs = mins // 60
    rem_m = mins % 60
    return f"{hrs}시간 {rem_m}분 {int(rem)}초 ({sec:.1f}초)"


def _diagnose_run_issue(item):
    """실패·건너뜀·중단 항목의 에러 메시지/Traceback/URL을 분석해 1줄 원인 유형을 자동 분류"""
    status = str(item.get('status') or '').strip()
    err_msg = str(item.get('error') or item.get('msg') or '').strip()
    tb_text = str(item.get('traceback') or '').strip()
    url = str(item.get('url') or '').strip()
    combined = f"{err_msg}\n{tb_text}\n{url}".lower()

    if status == '건너뜀' or '건너뛰기' in err_msg or 'skipped by user' in combined:
        return '사용자 건너뜀 ([이 플랫폼만 건너뛰기] 요청)'
    if status == '중단' or '강제 중지' in err_msg or 'interruptederror' in combined:
        return '사용자 강제 중지 ([강제 중지] 요청으로 중단)'
    if '조준 검증 실패' in err_msg:
        return '테스트 모드 최종 게시 버튼 조준 검증 실패'
    if any(k in combined for k in (
        'targetclosederror',
        'target page, context or browser has been closed',
        'browser has been closed',
        'context has been closed',
        'page has been closed',
        'target closed',
        'frame was detached',
        '창이 닫혀',
    )):
        return '브라우저 닫힘 (TargetClosedError / 창 종료)'
    if any(k in combined for k in (
        '로그인', 'login', 'nidlogin', 'accounts.google', 'unauthorized', 'invalid_grant',
        '2단계 인증', '보안 확인', 'checkpoint', 'challenge', 'cookie', '쿠키', '세션 만료',
    )):
        return '로그인 필요·세션 만료 또는 보안 인증'
    if any(k in combined for k in (
        'timeouterror', 'timeout', 'timed out', '시간 초과',
        'waiting for locator', 'waiting for selector', 'strict mode violation',
        '찾을 수 없', '미발견', 'not found', '요소',
    )):
        return '요소 미발견·타임아웃 (화면 UI 변경 또는 로딩 지연)'
    if any(k in combined for k in (
        'quotaexceeded', 'quota', 'rate limit', '429', 'too many requests', '한도',
    )):
        return '플랫폼 업로드 한도·요청 제한 초과'
    if any(k in combined for k in (
        'filenotfounderror', 'no such file', '파일이 없', '영상', '사진 첨부 실패',
    )):
        return '미디어 파일 누락 또는 첨부 실패'
    if any(k in combined for k in (
        'net::err_', 'err_connection', 'err_name_not_resolved', 'connectionerror',
        'ssl', 'dns', '네트워크',
    )):
        return '네트워크 연결·페이지 로딩 오류'
    short_err = (err_msg.splitlines()[0] if err_msg else '상세 오류 없음')[:60]
    return f'기타 실행 오류 ({short_err})'


def _md_cell(text):
    return str(text or '').replace('\r', ' ').replace('\n', ' ').replace('|', '\\|').strip()


def write_run_report(platform_results, mode_label='실업로드', video_name='', start_dt=None, end_dt=None, stopped_by_user=False, report_kind=None, run_logs=None):
    """성공·실패·건너뜀·강제 중지·테스트 모드·로그인 점검 오류 결과를 고정 파일명(blackbox/report_test.md, report_upload.md, report_login.md)에 덮어쓰기 저장"""
    end_t = end_dt if isinstance(end_dt, datetime.datetime) else datetime.datetime.now()
    start_t = start_dt if isinstance(start_dt, datetime.datetime) else end_t
    elapsed_sec = max(0.0, (end_t - start_t).total_seconds())
    now_str = end_t.strftime('%Y-%m-%d %H:%M:%S')

    if report_kind in ('login', 'upload', 'test'):
        kind = report_kind
    elif '테스트' in str(mode_label or ''):
        kind = 'test'
    elif '로그인' in str(mode_label or ''):
        kind = 'login'
    else:
        kind = 'upload'
    report_fname = format_blackbox_filename(kind, 'md', dt=end_t, is_report=True)
    report_path = os.path.join(BLACKBOX_DIR, report_fname)

    items = list(platform_results or [])
    ok_cnt = sum(1 for x in items if x.get('status') == '성공')
    fail_cnt = sum(1 for x in items if x.get('status') == '실패')
    skip_cnt = sum(1 for x in items if x.get('status') == '건너뜀')
    stop_cnt = sum(1 for x in items if x.get('status') in ('중단', '강제 중지'))
    problem_items = [x for x in items if x.get('status') in ('실패', '건너뜀', '중단', '강제 중지')]

    if stopped_by_user:
        finish_label = '강제 중지'
    elif fail_cnt > 0 or skip_cnt > 0:
        finish_label = '일부 실패·건너뜀'
    else:
        finish_label = '정상 완료'

    result_summary = f'성공 {ok_cnt}건 / 실패 {fail_cnt}건 / 건너뜀 {skip_cnt}건'
    if stop_cnt > 0:
        result_summary += f' / 강제 중지 {stop_cnt}건'

    lines = [
        f'# 실행 결과 리포트 (`blackbox/{report_fname}`)',
        '',
        '## 1. 실행 요약',
        f'- **기록 시각**: {now_str}',
        f'- **실행 모드**: {mode_label}',
        f'- **종료 상태**: {finish_label}',
        f'- **대상 영상**: `{os.path.basename(video_name) if video_name else "(없음)"}`',
        f'- **총 소요 시간**: {_format_duration_ko(elapsed_sec)}',
        f'- **전체 결과**: 총 {len(items)}건 ({result_summary})',
        '',
        '## 2. 파이썬 1차 자동 진단',
    ]

    if not problem_items:
        lines.append('- ✅ 모든 선택 플랫폼이 오류 없이 정상 완료되었습니다.')
    else:
        for pitem in problem_items:
            p_label = pitem.get('label') or pitem.get('key') or '알 수 없음'
            p_status = pitem.get('status') or '실패'
            diag_cause = _diagnose_run_issue(pitem)
            p_err = (pitem.get('error') or pitem.get('msg') or '').strip()
            p_err_line = p_err.splitlines()[0][:100] if p_err else ''
            if p_err_line:
                lines.append(f'- **[{p_label}]** ({p_status}): `{diag_cause}` — `{p_err_line}`')
            else:
                lines.append(f'- **[{p_label}]** ({p_status}): `{diag_cause}`')

    lines.extend([
        '',
        '## 3. 플랫폼별 실행 결과',
        '| 플랫폼 | 상태 | 최종 메시지 | 도달 URL |',
        '| --- | --- | --- | --- |',
    ])

    status_badge_map = {
        '성공': '✅ 성공',
        '실패': '❌ 실패',
        '건너뜀': '⏭️ 건너뜀',
        '중단': '🛑 강제 중지',
        '강제 중지': '🛑 강제 중지',
    }
    if items:
        for item in items:
            p_label = _md_cell(item.get('label') or item.get('key') or '알 수 없음')
            p_status = status_badge_map.get(item.get('status'), _md_cell(item.get('status') or '-'))
            p_msg = _md_cell(item.get('msg') or item.get('error') or '-')
            raw_url = (item.get('url') or '').strip()
            p_url = f'`{_md_cell(raw_url)}`' if raw_url else '-'
            lines.append(f'| {p_label} | {p_status} | {p_msg} | {p_url} |')
    else:
        lines.append('| (선택된 플랫폼 없음) | - | - | - |')

    if problem_items:
        lines.extend([
            '',
            '---',
            '',
            '## 4. 상세 진단 (실패·건너뜀·강제 중지 항목)',
            '',
        ])
        for idx_e, item in enumerate(problem_items, start=1):
            label = item.get('label') or item.get('key') or '알 수 없음'
            status = item.get('status') or '실패'
            url = (item.get('url') or '').strip() or '(페이지 URL 미확인)'
            err_msg = (item.get('error') or item.get('msg') or '').strip() or '(상세 에러 메시지 없음)'
            tb_text = (item.get('traceback') or '').strip() or err_msg
            buttons = item.get('buttons') or []
            at_str = item.get('captured_at') or now_str
            diag_cause = _diagnose_run_issue(item)

            lines.append(f'### 4-{idx_e}. [{label}] — {status} ({at_str})')
            lines.append('')
            lines.append(f'- **1차 자동 진단**: `{diag_cause}`')
            lines.append(f'- **중단/실패 직전 페이지 URL**: `{url}`')
            lines.append(f'- **오류/상태 요약**: `{err_msg}`')
            lines.append('')
            lines.append('#### 파이썬 상세 메시지 및 Traceback')
            lines.append('```text')
            lines.append(tb_text)
            lines.append('```')
            lines.append('')
            lines.append('#### 해당 시점 화면의 주요 버튼·클릭 요소 텍스트 목록')
            if buttons:
                for btxt in buttons:
                    lines.append(f'- {btxt}')
            else:
                lines.append('- (화면 클릭 요소 정보 없음 — 브라우저 닫힘 또는 페이지 로드 전 발생)')
            lines.append('')
            lines.append('---')
            lines.append('')

    cleaned_logs = [str(ln).strip() for ln in (run_logs or []) if str(ln or '').strip()]
    if cleaned_logs:
        log_sec_num = 5 if problem_items else 4
        lines.extend([
            '',
            f'## {log_sec_num}. 실행 로그 (중단/종료 시점까지)',
            '```text',
            *cleaned_logs[-80:],
            '```',
            '',
        ])

    try:
        os.makedirs(BLACKBOX_DIR, exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines).rstrip() + '\n')
        prune_blackbox_dir(max_keep=3)
        return report_path
    except Exception:
        return ''


def _build_and_write_run_report_snapshot(run_ctx, stopped_by_user=False, active_platform_key='', top_level_exc=None, top_level_tb=''):
    """진행 중 또는 종료된 업로드/테스트 세션 컨텍스트를 바탕으로 report_upload.md / report_test.md를 즉시 생성·저장."""
    if not isinstance(run_ctx, dict):
        return ''
    now_end_dt = datetime.datetime.now()
    now_end_str = now_end_dt.strftime('%Y-%m-%d %H:%M:%S')
    selected_platforms = list(run_ctx.get('selected_platforms') or [])
    platform_records = dict(run_ctx.get('platform_records') or {})
    mode_label = run_ctx.get('mode_label') or ('테스트 모드' if run_ctx.get('report_kind') == 'test' else '실업로드')
    report_kind = run_ctx.get('report_kind') or ('test' if '테스트' in str(mode_label) else 'upload')
    video_name = run_ctx.get('video_name') or ''
    start_dt = run_ctx.get('start_dt') or now_end_dt

    with STATE_LOCK:
        cur_logs = list(STATE.get('logs') or [])

    final_report_items = []
    for pkey, plabel, bb_name in selected_platforms:
        rec = platform_records.get(pkey)
        if rec is not None:
            rec_copy = dict(rec)
            if rec_copy.get('status') == '중단':
                rec_copy['status'] = '강제 중지'
            final_report_items.append(rec_copy)
        elif stopped_by_user and active_platform_key and pkey == active_platform_key:
            diag = get_platform_diagnostics(bb_name) or {}
            final_report_items.append({
                'key': pkey,
                'label': plabel,
                'status': '강제 중지',
                'msg': '강제 중지 (진행 중 중단 — 브라우저 화면 유지)',
                'url': diag.get('url') or ('(YouTube Data API)' if pkey == 'yt' else ''),
                'error': diag.get('error') or '사용자 강제 중지 요청으로 진행 중 중단됨 (브라우저 화면 유지)',
                'traceback': diag.get('traceback') or 'Stopped by user during execution (browser kept open)',
                'buttons': diag.get('buttons') or [],
                'captured_at': diag.get('captured_at') or now_end_str,
            })
        else:
            final_report_items.append({
                'key': pkey,
                'label': plabel,
                'status': '강제 중지' if stopped_by_user else '실패',
                'msg': '강제 중지 (미실행)' if stopped_by_user else '실행되지 않음',
                'url': '',
                'error': '사용자 강제 중지 요청으로 미실행' if stopped_by_user else (f'{type(top_level_exc).__name__}: {top_level_exc}' if top_level_exc else '예기치 않은 중단'),
                'traceback': top_level_tb or ('Stopped by user before execution' if stopped_by_user else 'Not executed'),
                'buttons': [],
                'captured_at': now_end_str,
            })

    if top_level_exc is not None and not any(x.get('status') == '실패' for x in final_report_items):
        final_report_items.append({
            'key': 'system',
            'label': '공통 실행기',
            'status': '실패',
            'msg': str(top_level_exc)[:80],
            'url': '',
            'error': f'{type(top_level_exc).__name__}: {top_level_exc}',
            'traceback': top_level_tb or str(top_level_exc),
            'buttons': [],
            'captured_at': now_end_str,
        })

    return write_run_report(
        final_report_items,
        mode_label=mode_label,
        video_name=video_name,
        start_dt=start_dt,
        end_dt=now_end_dt,
        stopped_by_user=stopped_by_user,
        report_kind=report_kind,
        run_logs=cur_logs,
    )


def skip_current_platform():
    """전체 업로드를 멈추지 않고 현재 진행 중인 플랫폼의 브라우저/작업만 즉시 종료해 다음 플랫폼으로 넘김 (타임아웃 없음)"""
    with STATE_LOCK:
        is_busy = bool(STATE.get('busy'))
        cur_key = str(STATE.get('current_platform_key') or '').strip()
        cur_label = str(STATE.get('current_platform_label') or cur_key).strip()
        cur_run_id = int(STATE.get('active_run_id', 0))
        if not is_busy or not cur_key:
            return {'ok': False, 'msg': '현재 건너뛸 수 있는 진행 중 플랫폼 작업이 없습니다.'}
        STATE['skip_platform_key'] = cur_key
        STATE['skip_seq'] = int(STATE.get('skip_seq', 0)) + 1

    log(f'⏭️ [건너뛰기 요청] [{cur_label}] 작업을 종료하고 다음 플랫폼으로 이동합니다...')
    # stop_checker 기반 graceful 종료에 잠시 기회를 준 뒤 아직 같은 플랫폼에 걸려 있으면 자동화 브라우저만 즉시 종료
    for _ in range(4):
        time.sleep(0.1)
        with STATE_LOCK:
            if STATE.get('active_run_id') != cur_run_id or STATE.get('current_platform_key') != cur_key:
                return {'ok': True, 'skipped': cur_key, 'msg': f'[{cur_label}] 건너뛰기 완료'}
    if cur_key != 'yt':
        _kill_automation_chrome()
    return {'ok': True, 'skipped': cur_key, 'msg': f'[{cur_label}] 건너뛰기 요청을 적용했습니다.'}


def force_stop_active_task():
    """강제 중지 요청 처리:
    1) 자동화 크롬 창을 닫지 않고(keep_browser_open=True) 파이썬 자동화 제어 루프만 즉시 중단해
       원장님이 멈춘 지점의 브라우저 화면을 그대로 확인할 수 있도록 유지
    2) 서버 상태(busy=False)와 UI 버튼 상태를 즉시 대기 상태로 복귀
    3) 중단 시점까지의 플랫폼별 진행 상태·로그를 report_upload.md / report_test.md로 반드시 저장하고
       블랙박스 녹화본(upload.mp4 / test.mp4)도 안전 복사·합본 저장"""
    global ACTIVE_WORKER_THREAD
    set_force_stop_keep_browser(True)
    with STATE_LOCK:
        STATE['stop_requested'] = True
        STATE['active_run_id'] += 1
        STATE['browser_left_open_by_stop'] = True
        worker_t = ACTIVE_WORKER_THREAD
        run_ctx = STATE.get('active_run_ctx')
        cur_pkey = str(STATE.get('current_platform_key') or '').strip()
        cur_plabel = str(STATE.get('current_platform_label') or '').strip()
        is_test_now = bool(STATE.get('is_test_mode'))
        cur_vn = str(STATE.get('video_name') or '').strip()
        cur_start_dt = STATE.get('test_start_dt')
        has_bb_session = bool(STATE.get('test_session_dir')) and not bool(STATE.get('test_merged_done'))
        if has_bb_session:
            STATE['force_stop_merging'] = True

    log('🛑 [강제 중지 요청] 자동화 제어만 즉시 중단합니다 (현재 브라우저 창은 화면 확인용으로 유지합니다)...')

    if worker_t is not None and worker_t.is_alive() and worker_t is not threading.current_thread():
        try:
            worker_t.join(timeout=2.5)
        except Exception:
            pass
    else:
        time.sleep(0.2)

    worker_still_alive = bool(worker_t is not None and worker_t.is_alive() and worker_t is not threading.current_thread())
    with STATE_LOCK:
        STATE['busy'] = False
        STATE['current_platform_key'] = ''
        STATE['current_platform_label'] = ''
        STATE['skip_platform_key'] = ''
        if not worker_still_alive:
            STATE['stop_requested'] = False
        if ACTIVE_WORKER_THREAD is worker_t:
            ACTIVE_WORKER_THREAD = None

    # 강제 중지 시 진단 리포트(report_upload.md / report_test.md) 반드시 생성·저장
    if isinstance(run_ctx, dict):
        saved_rp = _build_and_write_run_report_snapshot(
            run_ctx,
            stopped_by_user=True,
            active_platform_key=cur_pkey,
        )
    elif has_bb_session or cur_pkey:
        with STATE_LOCK:
            cur_logs = list(STATE.get('logs') or [])
        fallback_items = [{
            'key': cur_pkey or 'system',
            'label': cur_plabel or '진행 중 작업',
            'status': '강제 중지',
            'msg': '강제 중지 (진행 중 중단 — 브라우저 화면 유지)',
            'url': '',
            'error': '사용자 강제 중지 요청으로 중단됨 (브라우저 화면 유지)',
            'traceback': 'Stopped by user (browser kept open)',
            'buttons': [],
            'captured_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }]
        saved_rp = write_run_report(
            fallback_items,
            mode_label=('테스트 모드' if is_test_now else '실업로드'),
            video_name=cur_vn,
            start_dt=cur_start_dt,
            end_dt=datetime.datetime.now(),
            stopped_by_user=True,
            report_kind=('test' if is_test_now else 'upload'),
            run_logs=cur_logs,
        )
    else:
        saved_rp = ''

    if saved_rp:
        log(f'📝 [실행 리포트 저장] blackbox/{os.path.basename(saved_rp)}에 강제 중지 시점까지의 진행 상태와 로그를 기록했습니다.')

    if has_bb_session:
        _finalize_blackbox_session_merge_once()
        with STATE_LOCK:
            STATE['force_stop_merging'] = False
    clean_and_unify_blackbox_files(clear_thumbs=False)
    log('✅ [강제 중지 완료] 자동화를 중단했으며 현재 브라우저 창은 그대로 유지했습니다. (다음 업로드 시작 시 기존 창을 안전 정리합니다.)')
    return {'ok': True, 'msg': '자동화를 강제 중지했습니다. 현재 브라우저 화면을 그대로 확인하실 수 있습니다.'}

def clean_and_unify_blackbox_files(clear_thumbs=False):
    """1) 구버전 auth 파일 전용 폴더 이관 및 불필요한 루트 .png/구 리포트 삭제
    2) 통합 blackbox/ 폴더 생성 및 구버전 blackbox_login/·blackbox_upload/ 안전 정리
    3) debug_records/temp_thumbs 임시 찌꺼기 정리 및 blackbox/ 유형별 최신 3개 자동 유지"""
    try:
        migrate_legacy_auth_files()
        os.makedirs(BLACKBOX_DIR, exist_ok=True)

        for fn in os.listdir(BASE_DIR):
            if fn in ('errorReport.md', 'report.md') or (fn.lower().endswith('.png') and not _is_protected_root_file(fn)):
                try: os.remove(os.path.join(BASE_DIR, fn))
                except Exception: pass

        with STATE_LOCK:
            is_busy = STATE.get('busy')

        # 구버전 분리 폴더(blackbox_login/, blackbox_upload/, debug_records/) 및 임시 폴더(blackbox/_tmp) 정리 (작업 미진행·창 미유지 시)
        if not is_busy:
            cleanup_dirs = [LEGACY_BLACKBOX_LOGIN_DIR, LEGACY_BLACKBOX_UPLOAD_DIR, LEGACY_DEBUG_DIR]
            if not is_force_stop_keep_browser():
                cleanup_dirs.append(BLACKBOX_TMP_DIR)
            for legacy_dir in cleanup_dirs:
                if os.path.isdir(legacy_dir):
                    _force_rmtree(legacy_dir)

        if clear_thumbs and not is_busy and os.path.exists(THUMB_DIR):
            for tf in os.listdir(THUMB_DIR):
                fp = os.path.join(THUMB_DIR, tf)
                if os.path.isfile(fp):
                    try: os.remove(fp)
                    except Exception: pass

        # 통합 blackbox/ 폴더는 유형별(영상·리포트·스샷·로그) 최신 3개만 자동 유지
        if not is_busy:
            prune_blackbox_dir(max_keep=3)
    except Exception:
        pass

def has_backup_files():
    return os.path.exists(BACKUP_DIR) and len(os.listdir(BACKUP_DIR)) > 0

PROTECTED_CODE_EXTS = ('.py', '.html', '.bat', '.command')
PROTECTED_ROOT_FILES = {
    'version.json', 'LICENSE.txt', '.gitignore', '.gitattributes', '.installed_done',
    'salon_uploader.ico', 'favicon.png', 'salon_uploader_icon.png',
    '0. 처음사용자 안내서.html', '1-1. 윈도우 최초설치.bat', '1-2. 윈도우 프로그램 실행.bat',
    '2-1. 맥북 최초설치.command', '2-2. 맥북 프로그램 실행.command',
    'AI_RULES.md', 'export_code.py', 'check_personal_leak.py', 'build_release.py',
    'app.log',
    'youtube_token.json', 'client_secret.json', 'user_profile.json', 'daily_usage.json', 'login_credentials.json',
}
TEMP_ROOT_FILES = {'total_code.txt', 'export_error.log', 'preview_thumb.jpg'}

def _is_protected_root_file(fn):
    """통합 파일 정리 시 절대 삭제해서는 안 되는 핵심 코드·설정·인증 파일 판별"""
    if fn.startswith('.') or fn in ESSENTIAL_ITEMS or fn in PROTECTED_ROOT_FILES:
        return True
    if fn.lower().endswith(PROTECTED_CODE_EXTS):
        return True
    if fn.endswith('_cookies.json') or (fn.startswith('블랙박스_') and fn.endswith('.webm')):
        return True
    return False

CHROME_CACHE_TARGET_DIRS = {
    'Cache',
    'Code Cache',
    'GPUCache',
    'GrShaderCache',
    'ShaderCache',
    'DawnCache',
    'Media Cache',
    'CacheStorage',
    'ScriptCache',
    'blob_storage',
    'Crashpad',
}

CHROME_PROTECTED_SESSION_NAMES = {
    'Cookies',
    'Cookies-journal',
    'Local Storage',
    'Session Storage',
    'IndexedDB',
    'Preferences',
    'Secure Preferences',
    'Login Data',
    'Login Data For Account',
    'Web Data',
    'Network',
}


def cleanup_chrome_profile_caches(auth_root=None, check_busy=True):
    """auth_data/ 하위를 순회하며 로그인 세션·쿠키·설정은 100% 보호하고
    오직 크롬 캐시·임시 폴더(CHROME_CACHE_TARGET_DIRS)만 안전하게 삭제해 확보 용량(MB) 반환"""
    if check_busy:
        with STATE_LOCK:
            if STATE.get('busy'):
                return {
                    'skipped_busy': True,
                    'freed_bytes': 0,
                    'freed_mb': 0.0,
                    'removed_dirs': 0,
                    'removed_files': 0,
                    'skipped_locked': 0,
                }

    base_auth = auth_root or AUTH_DIR
    if not base_auth or not os.path.isdir(base_auth):
        return {
            'skipped_busy': False,
            'freed_bytes': 0,
            'freed_mb': 0.0,
            'removed_dirs': 0,
            'removed_files': 0,
            'skipped_locked': 0,
        }

    freed_bytes = 0
    removed_dirs = 0
    removed_files = 0
    skipped_locked = 0

    for root, dirs, _ in os.walk(base_auth, topdown=True, followlinks=False):
        # 보호 대상 세션 폴더 내부로는 절대 진입하지 않음
        kept_dirs = []
        target_dirs = []
        for d in list(dirs):
            if d in CHROME_PROTECTED_SESSION_NAMES:
                continue
            if d in CHROME_CACHE_TARGET_DIRS:
                target_dirs.append(d)
            else:
                kept_dirs.append(d)
        dirs[:] = kept_dirs

        for td in target_dirs:
            full_td = os.path.join(root, td)
            if os.path.islink(full_td) or not os.path.isdir(full_td):
                continue
            had_locked = False
            for sub_r, sub_ds, sub_fs in os.walk(full_td, topdown=False, followlinks=False):
                for sf in sub_fs:
                    sfp = os.path.join(sub_r, sf)
                    try:
                        fsize = os.path.getsize(sfp) if (not os.path.islink(sfp) and os.path.isfile(sfp)) else 0
                    except Exception:
                        fsize = 0
                    try:
                        os.chmod(sfp, stat.S_IWRITE | stat.S_IREAD)
                    except Exception:
                        pass
                    try:
                        os.remove(sfp)
                        freed_bytes += fsize
                        removed_files += 1
                    except Exception:
                        had_locked = True
                        skipped_locked += 1
                for sd in sub_ds:
                    sdp = os.path.join(sub_r, sd)
                    try:
                        os.rmdir(sdp)
                    except Exception:
                        had_locked = True
            try:
                os.rmdir(full_td)
                removed_dirs += 1
            except Exception:
                if not had_locked:
                    try:
                        shutil.rmtree(full_td)
                        removed_dirs += 1
                    except Exception:
                        skipped_locked += 1

    freed_mb = round(freed_bytes / (1024.0 * 1024.0), 1)
    return {
        'skipped_busy': False,
        'freed_bytes': freed_bytes,
        'freed_mb': freed_mb,
        'removed_dirs': removed_dirs,
        'removed_files': removed_files,
        'skipped_locked': skipped_locked,
    }


def handle_cleanup(action='clean_all'):
    """단일 통합 파일 정리 (clean_all 및 기존 quarantine/purge 호환).
    로그인 세션(Cookies/Local Storage 등)·핵심 코드·대기 중인 upload/·지난 기록(history.json)에 연결된
    완료 영상/GIF·blackbox 보호 파일(AI_RULES.md·*.command·code_*.txt)은 철저히 보호하고,
    임시 스냅샷(.png), blackbox 녹화/리포트, temp_thumbs, _old_backup,
    history.json 미참조 완료 영상·GIF, auth_data 크롬 캐시 찌꺼기를 한 번에 정리."""
    with STATE_LOCK:
        is_busy = STATE.get('busy')
    if is_busy:
        msg = BUSY_TASK_MSG
        log(f'⚠️ [파일 정리 보류] {msg}')
        return {'ok': False, 'msg': msg, 'has_backup': has_backup_files()}

    act = (action or 'clean_all').strip()
    if act not in ('clean_all', 'quarantine', 'purge'):
        return {'ok': False, 'msg': '알 수 없는 정리 명령입니다.', 'has_backup': has_backup_files()}

    # 1) 루트 쿠키·설정 파일 auth_data/ 안전 이관 및 블랙박스 통일
    migrate_legacy_auth_files()

    removed_png = 0
    removed_temp_root = []
    removed_debug = 0
    removed_blackbox = 0
    removed_thumbs = 0
    removed_backup = 0

    # 루트 .png 스냅샷 및 임시 산출물 정리 (favicon.png 등 보호 파일은 절대 제외)
    for fn in list(os.listdir(BASE_DIR)):
        src = os.path.join(BASE_DIR, fn)
        if not os.path.isfile(src):
            continue
        if _is_protected_root_file(fn):
            continue
        if fn.lower().endswith('.png'):
            try:
                os.remove(src)
                removed_png += 1
            except Exception:
                pass
            continue
        is_total_code_temp = fn.lower().startswith('total_code') and fn.lower().endswith('.txt')
        if fn in TEMP_ROOT_FILES or is_total_code_temp or fn.lower().endswith(('.tmp', '.bak', '.old')) or not _is_protected_root_file(fn):
            try:
                os.remove(src)
                removed_temp_root.append(fn)
            except Exception:
                pass

    # blackbox/_tmp 및 temp_thumbs 내 잔여 파일 수 먼저 집계 후 정리
    if os.path.isdir(BLACKBOX_TMP_DIR):
        removed_debug = len(os.listdir(BLACKBOX_TMP_DIR))
    if os.path.isdir(THUMB_DIR):
        removed_thumbs = len(os.listdir(THUMB_DIR))

    # 블랙박스 임시 폴더 및 temp_thumbs 정리
    clean_and_unify_blackbox_files(clear_thumbs=True)

    # 하위 디렉터리가 남아 있는 경우까지 안전 정리
    for target_dir in (BLACKBOX_TMP_DIR, THUMB_DIR):
        if os.path.isdir(target_dir):
            for entry in list(os.listdir(target_dir)):
                fp = os.path.join(target_dir, entry)
                try:
                    if os.path.isdir(fp):
                        _force_rmtree(fp)
                    else:
                        os.remove(fp)
                except Exception:
                    pass

    # blackbox/ 폴더 내 보호 파일(AI_RULES.md·*.command·code_*.txt) 외 녹화·리포트 파일 정리
    if os.path.isdir(BLACKBOX_DIR):
        for entry in list(os.listdir(BLACKBOX_DIR)):
            if is_blackbox_protected_file(entry):
                continue
            ep = os.path.join(BLACKBOX_DIR, entry)
            try:
                if os.path.isdir(ep):
                    for _, _, sub_files in os.walk(ep):
                        removed_blackbox += len(sub_files)
                    _force_rmtree(ep)
                elif os.path.isfile(ep) or os.path.islink(ep):
                    os.remove(ep)
                    removed_blackbox += 1
            except Exception:
                pass

    # _old_backup 구버전 찌꺼기 폴더 안전 영구 삭제
    if os.path.exists(BACKUP_DIR):
        try:
            removed_backup = len(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else 1
        except Exception:
            removed_backup = 1
        _force_rmtree(BACKUP_DIR)

    # 지난 기록(done/history.json)에서 참조하지 않는 미참조 완료 영상·GIF 안전 정리
    orphan_res = _cleanup_all_unreferenced_done_media()
    removed_orphan_videos = orphan_res.get('removed_videos', 0)
    removed_orphan_gifs = orphan_res.get('removed_gifs', 0)
    orphan_freed_mb = orphan_res.get('freed_mb', 0.0)
    if removed_orphan_videos or removed_orphan_gifs:
        log(f"🧹 미참조 완료 파일 정리 완료: 영상 {removed_orphan_videos}개 · GIF {removed_orphan_gifs}개 ({orphan_freed_mb} MB 확보)")

    # auth_data/ 내부 크롬 캐시 찌꺼기만 안전 다이어트 (로그인 세션 100% 유지)
    cache_res = cleanup_chrome_profile_caches()
    cache_freed_mb = cache_res.get('freed_mb', 0.0)
    if cache_res.get('freed_bytes', 0) > 0 or cache_res.get('removed_dirs', 0) > 0:
        log(f"🧹 크롬 캐시 정리 완료: {cache_freed_mb} MB 확보 (로그인 세션 유지)")

    parts = []
    if removed_png:
        parts.append(f"스냅샷(.png) {removed_png}개")
    if removed_temp_root:
        parts.append(f"임시 파일 {len(removed_temp_root)}개({', '.join(removed_temp_root[:5])}{' 등' if len(removed_temp_root) > 5 else ''})")
    if removed_debug:
        parts.append(f"임시 녹화 {removed_debug}개")
    if removed_blackbox:
        parts.append(f"블랙박스 파일 {removed_blackbox}개")
    if removed_thumbs:
        parts.append(f"임시 썸네일 {removed_thumbs}개")
    if removed_backup:
        parts.append(f"_old_backup 구버전 백업 {removed_backup}개")
    if removed_orphan_videos or removed_orphan_gifs:
        parts.append(f"미참조 완료 파일 {removed_orphan_videos + removed_orphan_gifs}개({orphan_freed_mb} MB 확보)")
    if cache_res.get('freed_bytes', 0) > 0 or cache_res.get('removed_dirs', 0) > 0:
        parts.append(f"크롬 캐시 {cache_freed_mb} MB 확보")

    if parts:
        summary = " · ".join(parts)
        msg = f"🧹 파일 정리 완료! ({summary}) — 로그인 세션·핵심 코드·upload·기록에 연결된 파일은 안전하게 보호되었습니다."
    else:
        msg = "🧹 이미 깨끗한 상태입니다! (정리할 임시/미참조/캐시 파일 없음 · 로그인 세션 및 핵심 파일 보호됨)"

    log(msg)
    return {
        'ok': True,
        'msg': msg,
        'has_backup': has_backup_files(),
        'cache_freed_mb': cache_freed_mb,
    }

def extract_10_thumbnails(video_path, count=6, prefix='thumb_'):
    """영상에서 고화질 컷 추출 (기본 6장: 0번=85% 후반부 대표사진 완성 컷, 1~5번=영상 전체 구간 균등 추출).
    해상도 축소 없이 FFmpeg -q:v 1 / OpenCV JPEG 98 최상급 화질 유지."""
    if not video_path or not os.path.exists(video_path):
        return []
    os.makedirs(THUMB_DIR, exist_ok=True)
    safe_prefix = re.sub(r'[^A-Za-z0-9_-]+', '', str(prefix or 'thumb_')) or 'thumb_'
    for old_f in os.listdir(THUMB_DIR):
        if old_f.startswith(safe_prefix) and old_f.endswith('.jpg'):
            try: os.remove(os.path.join(THUMB_DIR, old_f))
            except Exception: pass

    ts = int(time.time())
    thumbnails = []

    if count <= 1:
        ratios = [0.85]
    else:
        body_count = count - 1
        if body_count == 1:
            body_ratios = [0.35]
        else:
            body_ratios = [0.08 + (0.72 * i / (body_count - 1)) for i in range(body_count)]
        ratios = [0.85] + body_ratios

    # 1순위: FFmpeg 최상급 화질(-q:v 1, 해상도 무축소, 아이폰 .mov 회전 메타데이터 안정 지원)
    try:
        duration = 0.0
        ffprobe_bin = get_ffprobe_bin()
        if ffprobe_bin:
            try:
                probe = subprocess.run(
                    [ffprobe_bin, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
                    capture_output=True, text=True, timeout=10, **_win_no_window_kwargs(),
                )
                duration = float(probe.stdout.strip() or 0)
            except Exception:
                duration = 0.0
        if duration <= 0:
            try:
                import cv2
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                    if fps > 0 and frames > 0:
                        duration = frames / fps
                cap.release()
            except Exception:
                pass
        if duration <= 0:
            duration = float(_inspect_video_with_ffmpeg(video_path).get('duration_sec') or 0.0)
        if duration > 0:
            ffmpeg_bin = get_ffmpeg_bin()
            for i, ratio in enumerate(ratios):
                sec = max(0.0, min(max(0.0, duration - 0.1), duration * ratio))
                fname = f"{safe_prefix}{i+1:02d}.jpg"
                fpath = os.path.join(THUMB_DIR, fname)
                subprocess.run(
                    [ffmpeg_bin, '-y', '-ss', f"{sec:.3f}", '-i', video_path, '-frames:v', '1', '-q:v', '1', fpath],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                    **_win_no_window_kwargs(),
                )
                if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                    thumbnails.append({'url': f'/thumbs/{fname}?t={ts}', 'path': fpath})
        if thumbnails:
            log(f"📸 (FFmpeg 고화질) 영상에서 캡처 사진 {len(thumbnails)}장을 추출했습니다 (대표 85% + 본문 균등).")
            return thumbnails
    except Exception:
        pass

    # 2순위: OpenCV 최상급 화질(JPEG 98, 해상도 무축소) 폴백
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames > 0:
            target_frames = [min(total_frames - 1, max(0, int(total_frames * r))) for r in ratios]
            for idx, f_no in enumerate(target_frames, start=1):
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_no)
                ret, frame = cap.read()
                if ret:
                    fname = f"{safe_prefix}{idx:02d}.jpg"
                    fpath = os.path.join(THUMB_DIR, fname)
                    cv2.imwrite(fpath, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 98])
                    thumbnails.append({'url': f'/thumbs/{fname}?t={ts}', 'path': fpath})
        cap.release()
        if thumbnails:
            log(f"📸 (CV2 고화질) 영상에서 캡처 사진 {len(thumbnails)}장을 추출했습니다 (대표 85% + 본문 균등).")
            return thumbnails
    except Exception as e:
        log(f"⚠️ 영상 캡처 추출 실패: {e}")

    return thumbnails


def capture_frame_at_time(video_path, time_sec):
    """영상의 지정 시점(time_sec)에서 최상급 고화질 캡처 JPEG 1장을 추출해 temp_thumbs/에 저장"""
    if not video_path or not os.path.exists(video_path):
        return {'ok': False, 'msg': '첨부된 영상 파일을 찾을 수 없습니다.'}
    os.makedirs(THUMB_DIR, exist_ok=True)
    try:
        t = max(0.0, float(time_sec or 0.0))
    except Exception:
        t = 0.0

    ts_ms = int(time.time() * 1000)
    fname = f"custom_frame_{ts_ms}.jpg"
    fpath = os.path.join(THUMB_DIR, fname)

    # 1순위: FFmpeg 최상급 화질(-q:v 1, 해상도 무축소, 아이폰 .mov 회전 메타데이터 안정 지원)
    try:
        proc = subprocess.run(
            [get_ffmpeg_bin(), '-y', '-ss', f"{t:.3f}", '-i', video_path, '-frames:v', '1', '-q:v', '1', fpath],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            **_win_no_window_kwargs(),
        )
        if proc.returncode == 0 and os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
            log(f"📸 [장면 캡처] {t:.2f}초 고화질 장면 저장 완료: {fname}")
            return {'ok': True, 'url': f'/thumbs/{fname}?t={ts_ms}', 'path': fpath, 'time_sec': round(t, 2), 'kind': 'image'}
    except Exception:
        pass

    # 2순위: OpenCV 최상급 화질(JPEG 98, 해상도 무축소) 폴백
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(fpath, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 98])
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    log(f"📸 [장면 캡처(CV2)] {t:.2f}초 고화질 장면 저장 완료: {fname}")
                    return {'ok': True, 'url': f'/thumbs/{fname}?t={ts_ms}', 'path': fpath, 'time_sec': round(t, 2), 'kind': 'image'}
    except Exception as e:
        log(f"⚠️ 장면 캡처 실패: {e}")

    return {'ok': False, 'msg': '해당 시점의 화면을 캡처하지 못했습니다. (ffmpeg 또는 opencv 확인 필요)'}


def create_gif_from_video(video_path, start_sec, end_sec, speed=1.0):
    """영상의 지정 구간(start_sec ~ end_sec)과 재생배속(speed)으로 팔레트 최적화 고화질 GIF를 생성해 temp_thumbs/에 저장"""
    if not video_path or not os.path.exists(video_path):
        return {'ok': False, 'msg': '첨부된 영상 파일을 찾을 수 없습니다.'}
    os.makedirs(THUMB_DIR, exist_ok=True)

    try:
        s_t = max(0.0, float(start_sec or 0.0))
        e_t = max(0.0, float(end_sec or 0.0))
    except Exception:
        return {'ok': False, 'msg': '시작/끝 시간 값이 올바르지 않습니다.'}

    try:
        spd = float(speed or 1.0)
    except Exception:
        spd = 1.0
    spd = max(0.5, min(2.0, round(spd, 2)))

    if e_t <= s_t:
        e_t = s_t + 2.5
    duration = e_t - s_t
    if duration < 0.4:
        duration = 0.4
        e_t = s_t + duration
    elif duration > 8.0:
        duration = 8.0
        e_t = s_t + duration

    out_duration = round(duration / spd, 2)
    ts_ms = int(time.time() * 1000)
    fname = f"custom_gif_{ts_ms}.gif"
    fpath = os.path.join(THUMB_DIR, fname)

    # 1순위: FFmpeg setpts + palettegen/paletteuse 2패스 필터 (배속·색감·용량 최적)
    try:
        pts_prefix = f"setpts=(PTS-STARTPTS)/{spd:.4f}," if abs(spd - 1.0) > 1e-3 else "setpts=PTS-STARTPTS,"
        vf_filter = (
            f"{pts_prefix}fps=12,scale=480:-1:flags=lanczos,"
            "split[s0][s1];"
            "[s0]palettegen=max_colors=128:stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        proc = subprocess.run(
            [
                get_ffmpeg_bin(), '-y',
                '-ss', f"{s_t:.3f}",
                '-t', f"{duration:.3f}",
                '-i', video_path,
                '-vf', vf_filter,
                '-loop', '0',
                fpath,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45,
            **_win_no_window_kwargs(),
        )
        if proc.returncode == 0 and os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
            size_kb = round(os.path.getsize(fpath) / 1024.0, 1)
            log(f"🎞️ [GIF 생성 완료] {s_t:.1f}~{e_t:.1f}초 · {spd:g}x (결과 약 {out_duration:.1f}초 · {size_kb}KB) -> {fname}")
            return {
                'ok': True,
                'url': f'/thumbs/{fname}?t={ts_ms}',
                'path': fpath,
                'start_sec': round(s_t, 2),
                'end_sec': round(e_t, 2),
                'duration_sec': round(duration, 2),
                'out_duration_sec': out_duration,
                'speed': spd,
                'size_kb': size_kb,
                'kind': 'gif',
            }
    except Exception:
        pass

    # 2순위: OpenCV + Pillow 폴백 (선택 구간에서 균등 추출 후 배속 기준 프레임 간격 계산)
    try:
        import cv2
        from PIL import Image
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            start_f = int(s_t * fps)
            end_f = max(start_f + 1, int(e_t * fps))
            target_count = min(96, max(4, int(round(out_duration * 12.0))))
            total_span_f = max(1, end_f - start_f)
            sample_indices = sorted({
                min(end_f, start_f + int(round(i * total_span_f / float(max(1, target_count - 1)))))
                for i in range(target_count)
            })
            frames = []
            for f_no in sample_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_no)
                ret, bgr = cap.read()
                if not ret:
                    continue
                h, w = bgr.shape[:2]
                new_w = 480
                new_h = max(1, int(round(h * (new_w / float(w)))))
                resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(rgb))
            cap.release()
            if frames:
                frame_ms = max(20, int(round((out_duration * 1000.0) / len(frames))))
                frames[0].save(
                    fpath,
                    save_all=True,
                    append_images=frames[1:],
                    duration=frame_ms,
                    loop=0,
                    optimize=True,
                )
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    size_kb = round(os.path.getsize(fpath) / 1024.0, 1)
                    log(f"🎞️ [GIF 생성 완료(CV2)] {s_t:.1f}~{e_t:.1f}초 · {spd:g}x (결과 약 {out_duration:.1f}초 · {size_kb}KB)")
                    return {
                        'ok': True,
                        'url': f'/thumbs/{fname}?t={ts_ms}',
                        'path': fpath,
                        'start_sec': round(s_t, 2),
                        'end_sec': round(e_t, 2),
                        'duration_sec': round(duration, 2),
                        'out_duration_sec': out_duration,
                        'speed': spd,
                        'size_kb': size_kb,
                        'kind': 'gif',
                    }
    except Exception as e:
        log(f"⚠️ GIF 생성 실패: {e}")

    return {'ok': False, 'msg': 'GIF 생성에 실패했습니다. (ffmpeg 또는 opencv+Pillow 설치 확인 필요)'}


def _sanitize_tag_token(raw_tag):
    """해시태그 토큰에서 #, 쉼표, 따옴표, 보이지 않는 공백 등 찌꺼기를 제거"""
    s = INVISIBLE_SPACE_RE.sub('', str(raw_tag or ''))
    s = s.replace('#', '').strip()
    s = re.sub(r'^[,.\s·•|/\\"\'\[\]()]+|[,.\s·•|/\\"\'\[\]()]+$', '', s)
    s = re.sub(r'\s+', '', s)
    return s


def format_clean_tags(tags_iter):
    """태그 목록을 쉼표/공백 찌꺼기 없이 깔끔한 ', ' 문자열로 정리"""
    cleaned = []
    seen = set()
    for t in (tags_iter or []):
        tok = _sanitize_tag_token(t)
        if tok and tok.lower() not in seen:
            seen.add(tok.lower())
            cleaned.append(tok)
    return ', '.join(cleaned)


def smart_trim_text(text, max_len):
    """글자 수(max_len) 초과 시 단어 중간을 강제로 자르지 않고,
    완성된 문장(마침표·물음표·느낌표·줄바꿈) 또는 어절(띄어쓰기) 단위로 자연스럽게 축약"""
    s = (text or '').strip()
    if not s or max_len <= 0:
        return ''
    if len(s) <= max_len:
        return s

    candidate = s[:max_len]
    next_char = s[max_len] if len(s) > max_len else ''

    # 딱 경계 직후가 공백·구두점·줄바꿈이면 어절이 끊기지 않은 상태
    if next_char in ('', ' ', '\n', '\t', ',', '.', '!', '?', '·', '|'):
        trimmed = candidate.rstrip(' ,·|-:/')
        if trimmed:
            return trimmed

    # 1순위: 완성 문장 끝(. ! ? 또는 줄바꿈) 우선 채택 (최소 45% 이상 유지 시)
    min_sent_keep = max(8, int(max_len * 0.45))
    best_sent_end = -1
    for m in re.finditer(r'(?:[.!?](?=\s|$)|\n)', candidate):
        if m.end() >= min_sent_keep:
            best_sent_end = m.end()
    if best_sent_end != -1:
        trimmed = candidate[:best_sent_end].strip().rstrip(' ,·|-:/')
        if trimmed:
            return trimmed

    # 2순위: 어절(띄어쓰기·구분자) 경계에서 깔끔하게 마무리
    cut_pos = max(candidate.rfind(' '), candidate.rfind('\n'), candidate.rfind(' | '), candidate.rfind(' · '))
    if cut_pos >= max(4, int(max_len * 0.3)):
        trimmed = candidate[:cut_pos].rstrip(' ,·|-:/')
        if trimmed:
            return trimmed

    return candidate.rstrip(' ,·|-:/')


DEFAULT_BLOCKED_TAGS = {
    '숱많은머리',
    '숱많은단발',
    '남자뒷머리',
    '뒷통수볼륨',
    '예쁜머리',
    '머리스타일',
    '머리손질법',
}


def build_smart_tags(main_tags, sub_tags=None):
    """원고의 [해시태그]와 [대댓글 유도용 텍스트] 서브 해시태그에서
    비검색·추상 차단 태그(DEFAULT_BLOCKED_TAGS + get_effective_banned_tags)와 중복을 제거해
    플랫폼별 개수 규격에 맞춰 반환:
    - tags_5: [해시태그] 최대 5개 (클립·틱톡·인스타·핀터·페북 개인/매장)
    - tags_10: [해시태그] + 서브 해시태그 순서대로 합산 최대 10개 (유튜브·네이버 블로그)"""
    blocked_tags = set()
    for raw_blocked in DEFAULT_BLOCKED_TAGS:
        norm = re.sub(r'\s+', '', str(raw_blocked or '').replace('#', '').strip()).lower()
        if norm:
            blocked_tags.add(norm)
    try:
        for raw_banned in (get_effective_banned_tags() or set()):
            norm = re.sub(r'\s+', '', str(raw_banned or '').replace('#', '').strip()).lower()
            if norm:
                blocked_tags.add(norm)
    except Exception:
        pass

    def _dedupe(items):
        out = []
        seen = set()
        for raw in (items or []):
            tok = _sanitize_tag_token(raw)
            if not tok:
                continue
            low = tok.lower()
            if low in blocked_tags or low in seen:
                continue
            seen.add(low)
            out.append(tok)
        return out

    clean_main = _dedupe(main_tags)
    clean_sub = _dedupe(sub_tags)
    combined = _dedupe(clean_main + clean_sub)
    return clean_main, clean_main[:5], combined[:10]

INVISIBLE_SPACE_RE = re.compile(r'[\u3164\uffa0\u2800\u200b\u200c\u200d\ufeff\u00a0]+')
DIVIDER_LINE_RE = re.compile(r'^[\s\-_=━─┈┄·•*★☆~]+$')
MEDIA_FILENAME_RE = re.compile(r'^[^\s/\\]+\.(?:mp4|mov|m4v|avi|webm|mkv|jpg|jpeg|png)$', re.IGNORECASE)
BLOG_MEDIA_MARKER_ANY_RE = re.compile(
    r'\[(?:미디어\s*자료\s*\d+|사진\s*\d+(?:\s*[·•\-]?\s*GIF)?|GIF(?:\s*\d+)?)\]',
    re.IGNORECASE,
)
MOBILE_BREAK_CONNECTOR_RE = re.compile(
    r'(?:,|하고|는데|지만|면서|해서|되어|이면|라면|에는|으로|에서|까지|거나|보다|부터|처럼)\s+'
)
INLINE_MARKUP_SPAN_RE = re.compile(r'(\[\[[^\]]+\]\]|\*\*[^*]+\*\*)')
BLOG_SUMMARY_HEADER_RE = re.compile(r'^(?:\*\*\s*)?오늘의\s*시술\s*요약(?:\s*\*\*)?$')
BLOG_SUMMARY_ITEM_RE = re.compile(r'^(?:\*\*\s*)?(?:시술명|모발\s*상태|추천\s*대상)(?:\s*\*\*)?\s*[:：]\s*.+$')


def _is_blog_summary_header(line):
    return bool(BLOG_SUMMARY_HEADER_RE.fullmatch((line or '').strip()))


def _is_blog_summary_item(line):
    return bool(BLOG_SUMMARY_ITEM_RE.fullmatch((line or '').strip()))


def _is_blog_summary_line(line):
    return _is_blog_summary_header(line) or _is_blog_summary_item(line)


def _visible_len(s):
    """[[...]] / **...** 서식 기호를 제외한 실제 모바일 표시 글자 수 계산"""
    return len(re.sub(r'\[\[|\]\]|\*\*', '', s or ''))


def _wrap_mobile_line(line, max_len=28):
    """단일 긴 문장을 서식([[...]], **...**)을 깨지 않고 모바일 호흡 단위로 자연스럽게 줄바꿈"""
    s = (line or '').strip()
    if not s or _visible_len(s) <= max_len:
        return [s] if s else []
    if s.startswith(('http://', 'https://')) or _is_divider_line(s):
        return [s]
    if BLOG_MEDIA_MARKER_ANY_RE.fullmatch(s):
        return [s]
    # '오늘의 시술 요약' 헤더 및 '시술명 :' / '모발 상태 :' / '추천 대상 :' 항목은 단독 줄 원형 유지
    if _is_blog_summary_line(s):
        return [s]
    # 소제목 전체가 [[...]]로 감싸진 줄은 줄바꿈하지 않고 원형 유지
    if s.startswith('[[') and s.endswith(']]') and s.count('[[') == 1:
        return [s]

    # 1) 문장 끝(. ! ?) 뒤 공백 기준으로 먼저 자연 분리
    sentences = [seg.strip() for seg in re.split(r'(?<=[.!?])\s+(?=[^\s])', s) if seg.strip()]
    out_lines = []

    for sent in sentences:
        if _visible_len(sent) <= max_len:
            out_lines.append(sent)
            continue

        # 2) [[...]] / **...** 블록과 붙어 있는 조사('**라인**이' 등)는 절대 쪼개지지 않도록 토큰화
        raw_tokens = re.findall(r'(?:\[\[[^\]]+\]\]|\*\*[^*]+\*\*|[^\s])+', sent)

        if len(raw_tokens) <= 1:
            out_lines.append(sent)
            continue

        # 3) 목표 폭(max_len) 내에서 쉼표·연결어미 호흡점을 우선 선택해 줄바꿈
        cur = ''
        for idx_t, tok in enumerate(raw_tokens):
            cand = f"{cur} {tok}".strip() if cur else tok
            if not cur:
                cur = tok
                continue

            cur_vlen = _visible_len(cur)
            cand_vlen = _visible_len(cand)
            rem_text = ' '.join(raw_tokens[idx_t:])
            rem_vlen = _visible_len(rem_text)

            # 쉼표/연결어미 직후이고 현재 줄이 충분히 찼으며 남은 텍스트도 충분하면 자연 호흡 줄바꿈
            ends_breath = bool(
                cur.endswith((',', '·'))
                or re.search(r'(?:하고|는데|지만|면서|해서|되어|이면|라면|에는|으로|에서|까지|거나|보다|부터|처럼)$', re.sub(r'\*\*|\[\[|\]\]', '', cur))
            )
            if ends_breath and cur_vlen >= int(max_len * 0.58) and rem_vlen >= 7 and cand_vlen > int(max_len * 0.85):
                out_lines.append(cur)
                cur = tok
            elif cand_vlen > max_len and cur_vlen >= 8:
                # 다음 줄에 너무 짧은 1~4글자 꼬리만 남는 현상 방지
                if rem_vlen <= 4 and cand_vlen <= max_len + 5:
                    cur = cand
                else:
                    out_lines.append(cur)
                    cur = tok
            else:
                cur = cand

        if cur:
            # 마지막 줄이 지나치게 짧으면(5자 미만) 직전 줄과 합쳐도 과하지 않을 때 병합
            if out_lines and _visible_len(cur) <= 5 and (_visible_len(out_lines[-1]) + _visible_len(cur) + 1) <= max_len + 6:
                out_lines[-1] = f"{out_lines[-1]} {cur}".strip()
            else:
                out_lines.append(cur)

    return out_lines


def format_mobile_readable(text, max_line_len=28, preserve_media_markers=True):
    """모바일 가독성 기준으로 긴 줄을 자연스럽게 호흡 단위 줄바꿈하고 과도한 빈 줄·장식을 정리.
    [미디어 자료N], [사진 N], [GIF] 마커는 절대 삭제되거나 문장에 섞이지 않고 단독 줄로 100% 원형 보존."""
    nl = chr(10)
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not raw:
        return ''

    # 1) 문장 안에 섞여 있을 수 있는 미디어 마커를 단독 줄로 안전 분리 (원형 100% 보존)
    if preserve_media_markers and BLOG_MEDIA_MARKER_ANY_RE.search(raw):
        raw = BLOG_MEDIA_MARKER_ANY_RE.sub(lambda m: f"\n\n{m.group(0)}\n\n", raw)

    # 2) 줄별 정리 및 모바일 줄바꿈
    out = []
    prev_blank = False
    in_summary_block = False
    for raw_line in raw.splitlines():
        cleaned = _clean_invisible_line(raw_line)
        if not cleaned:
            if in_summary_block:
                in_summary_block = False
            if out and not prev_blank:
                out.append('')
                prev_blank = True
            continue

        # 과한 점선 장식(┈┈┈┈┈┈ 등) 제거 (하단 공식 구분선 ━━━ 는 유지)
        if _is_divider_line(cleaned) and not cleaned.startswith('━━━'):
            continue

        # '오늘의 시술 요약' 블록 줄은 단독 줄로 보호하고 앞뒤 빈 줄 호흡 확보
        if _is_blog_summary_line(cleaned):
            if not in_summary_block:
                if out and not prev_blank:
                    out.append('')
                in_summary_block = True
            out.append(cleaned)
            prev_blank = False
            continue

        if in_summary_block:
            if out and not prev_blank:
                out.append('')
                prev_blank = True
            in_summary_block = False

        # 미디어 마커는 무조건 단독 줄로 원형 유지 + 앞뒤 호흡 확보
        if preserve_media_markers and BLOG_MEDIA_MARKER_ANY_RE.fullmatch(cleaned):
            if out and not prev_blank:
                out.append('')
            out.append(cleaned)
            out.append('')
            prev_blank = True
            continue

        wrapped = _wrap_mobile_line(cleaned, max_len=max_line_len)
        for wl in wrapped:
            out.append(wl)
            prev_blank = False

    # 3) 앞뒤 빈 줄 및 연속 빈 줄(최대 1개) 정리
    joined = nl.join(out).strip()
    joined = re.sub(r'\n{3,}', '\n\n', joined)
    return joined


BLOG_POINT_PREFIX_RE = re.compile(r'^((?:[✔✓•▪▸▶]|💡|👉|\d+\))\s*)(.+)$')
BLOG_HEADING_PREFIX_RE = re.compile(
    r'^(?:\d+\.\s+|Q[\.\)]\s*|STEP\s*\d+|[✂️📌✨🔥💬📍🎯🌿💎🫧⭐️🌟👍]\s*)',
    re.IGNORECASE,
)
BLOG_GREETING_RE = re.compile(r'^(?:안녕하세요|반갑습니다|안녕하십니까)')
BLOG_TIP_CUE_RE = re.compile(r'(?:포인트|핵심|추천|손질|꿀팁|비결|중요|유지법|주의)')


def _emphasize_intro_core_phrase(line):
    """도입부 본문 줄에서 모바일 줄바꿈(28자)을 깨뜨리지 않는 6~24자 핵심 문장·구절 1곳에 **...** 강조 적용."""
    s = (line or '').strip()
    if not s or '[[' in s or '**' in s or len(s) < 6:
        return None

    # 1) 짧은 단일 문장·구절(6~24자)이면 통째로 강조
    if len(s) <= 24:
        return f"**{s}**"

    # 2) 여러 문장이 한 줄에 있으면 그중 6~24자 핵심 문장 1개 강조
    sents = [seg.strip() for seg in re.split(r'(?<=[.!?])\s+(?=[^\s])', s) if seg.strip()]
    if len(sents) >= 2:
        for i, seg in enumerate(sents):
            if 6 <= len(seg) <= 24:
                sents[i] = f"**{seg}**"
                return ' '.join(sents)

    # 3) 쉼표·가운뎃점·콜론 앞 핵심 구절(6~22자) 강조
    m_clause = re.match(r'^([^,·:]{6,22})([,·:]\s*.+)$', s)
    if m_clause:
        return f"**{m_clause.group(1).strip()}**{m_clause.group(2)}"

    # 4) 어절 단위로 자연스럽게 끊기는 앞쪽 핵심 구절(6~22자) 강조
    words = s.split()
    if len(words) >= 3:
        acc = ''
        chosen_idx = -1
        for i, w in enumerate(words[:-1]):
            cand = f"{acc} {w}".strip() if acc else w
            if len(cand) > 22:
                break
            acc = cand
            if len(acc) >= 6:
                chosen_idx = i
                if len(acc) >= 12:
                    break
        if chosen_idx >= 0 and acc:
            rest = ' '.join(words[chosen_idx + 1:]).strip()
            if rest:
                return f"**{acc}** {rest}"

    return None


def apply_blog_auto_styling(text):
    """순수 텍스트 블로그 본문에 소제목([[...]])과 핵심 포인트·손질 팁(**...**) 서식을 자동 적용.
    - 이미 [[...]] 또는 **...**가 포함된 줄은 중복 적용하지 않음
    - [미디어 자료N], URL, 구분선, 인사말 첫 줄은 원형 그대로 보존
    - 인사말 직후 초반 도입부(2~4번째 콘텐츠 줄 / 첫 소제목·미디어 전)에 핵심 구절 1곳 **...** 강조 및 호흡 정리
    - **...** 강조는 문단당 최대 2곳으로 절제"""
    nl = chr(10)
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    if not raw.strip():
        return ''
    lines = raw.splitlines()
    out = []
    content_count = 0
    para_bold_count = 0
    in_intro_zone = True
    intro_bold_done = False

    def _is_boundary(idx):
        if idx < 0 or idx >= len(lines):
            return True
        s_i = lines[idx].strip()
        if not s_i:
            return True
        if BLOG_MEDIA_MARKER_ANY_RE.fullmatch(s_i):
            return True
        if _is_blog_summary_line(s_i):
            return True
        if _is_divider_line(s_i):
            return True
        return False

    for idx, line in enumerate(lines):
        s = line.strip()
        if not s:
            out.append('')
            para_bold_count = 0
            continue

        # '오늘의 시술 요약' 블록(헤더 및 시술명/모발 상태/추천 대상)은 소제목·도입부 강조 변형 없이 원형 단독 줄 보호
        if _is_blog_summary_line(s):
            prev_out_summary = bool(out and _is_blog_summary_line(out[-1]))
            if not prev_out_summary and out and out[-1] != '':
                out.append('')
            if _is_blog_summary_header(s):
                out.append('**오늘의 시술 요약**')
            else:
                out.append(s)
            para_bold_count = 0
            in_intro_zone = False
            if idx + 1 < len(lines):
                nxt_s = lines[idx + 1].strip()
                if nxt_s and not _is_blog_summary_line(nxt_s):
                    out.append('')
            continue

        # 미디어 마커·URL·구분선·해시태그 전용 줄은 절대 건드리지 않음
        if (
            BLOG_MEDIA_MARKER_ANY_RE.fullmatch(s)
            or re.match(r'^https?://\S+$', s, re.IGNORECASE)
            or _is_divider_line(s)
            or s.startswith('━━━')
            or re.fullmatch(r'(?:#[^\s#]+\s*)+', s)
        ):
            if BLOG_MEDIA_MARKER_ANY_RE.fullmatch(s) or _is_divider_line(s) or s.startswith('━━━'):
                in_intro_zone = False
            out.append(s)
            para_bold_count = 0
            continue

        content_count += 1
        is_first_content = (content_count == 1)

        # 이미 스타일 기호가 들어있는 줄은 중복 적용 방지
        if '[[' in s or '**' in s:
            if '[[' in s:
                in_intro_zone = False
            if '**' in s:
                para_bold_count += 1
                if in_intro_zone:
                    intro_bold_done = True
            out.append(s)
            continue

        # 인사말 첫 줄은 원형 그대로 보존 (바로 다음 줄이 본문이면 호흡 빈 줄 1개 확보)
        if is_first_content or BLOG_GREETING_RE.match(s):
            out.append(s)
            if idx + 1 < len(lines):
                nxt = lines[idx + 1].strip()
                if (
                    nxt
                    and not BLOG_MEDIA_MARKER_ANY_RE.fullmatch(nxt)
                    and not _is_divider_line(nxt)
                    and not nxt.startswith('━━━')
                ):
                    out.append('')
                    para_bold_count = 0
            continue

        prev_b = _is_boundary(idx - 1)
        next_b = _is_boundary(idx + 1)

        # 1) 포인트·손질 팁 줄 (**...** 강조, 문단당 최대 2곳)
        m_pt = BLOG_POINT_PREFIX_RE.match(s)
        if m_pt:
            if para_bold_count < 2:
                prefix, rest = m_pt.group(1), m_pt.group(2).strip()
                if ':' in rest:
                    head, tail = rest.split(':', 1)
                    head_s, tail_s = head.strip(), tail.strip()
                    if 2 <= len(head_s) <= 20:
                        styled = f"{prefix}**{head_s}:** {tail_s}".rstrip() if tail_s else f"{prefix}**{head_s}**"
                        out.append(styled)
                        para_bold_count += 1
                        if in_intro_zone:
                            intro_bold_done = True
                        continue
                if len(rest) <= 28:
                    out.append(f"{prefix}**{rest}**")
                    para_bold_count += 1
                    if in_intro_zone:
                        intro_bold_done = True
                    continue
                # 긴 포인트 줄은 앞쪽 핵심 구절만 절제 강조
                m_clause = re.match(r'^([^,·/]{4,22})([,·/]\s*.+)$', rest)
                if m_clause:
                    out.append(f"{prefix}**{m_clause.group(1).strip()}**{m_clause.group(2)}")
                    para_bold_count += 1
                    if in_intro_zone:
                        intro_bold_done = True
                    continue
            out.append(s)
            continue

        # 2) 소제목 자동 감지 → [[소제목]] (길이 제한 32자)
        is_standalone_heading = prev_b and next_b and (2 <= len(s) <= 32) and (not s.endswith('.'))
        is_cued_heading = (
            prev_b
            and (2 <= len(s) <= 32)
            and (not s.endswith('.'))
            and (bool(BLOG_HEADING_PREFIX_RE.match(s)) or s.endswith('?'))
        )
        if is_standalone_heading or is_cued_heading:
            in_intro_zone = False
            out.append(f"[[{s}]]")
            para_bold_count = 0
            continue

        # 3) 초반 도입부(2~4번째 콘텐츠 줄) 호흡 정리 및 핵심 문장·구절 1곳 자연 강조
        if in_intro_zone and 2 <= content_count <= 4:
            intro_sents = [seg.strip() for seg in re.split(r'(?<=[.!?])\s+(?=[^\s])', s) if seg.strip()]
            if len(intro_sents) >= 3 and len(s) >= 75:
                first_chunk = ' '.join(intro_sents[:2]).strip()
                rest_chunk = ' '.join(intro_sents[2:]).strip()
                if not intro_bold_done and para_bold_count < 2:
                    styled_first = _emphasize_intro_core_phrase(first_chunk)
                    if styled_first:
                        first_chunk = styled_first
                        intro_bold_done = True
                        para_bold_count += 1
                out.append(first_chunk)
                out.append('')
                out.append(rest_chunk)
                para_bold_count = 0
                continue
            if not intro_bold_done and para_bold_count < 2:
                styled_intro = _emphasize_intro_core_phrase(s)
                if styled_intro:
                    out.append(styled_intro)
                    intro_bold_done = True
                    para_bold_count += 1
                    continue

        # 4) 짧은 핵심 요약/손질 팁 줄 절제 강조 (문단당 아직 강조 없을 때만 1곳)
        if para_bold_count == 0 and (6 <= len(s) <= 26) and BLOG_TIP_CUE_RE.search(s) and not next_b:
            out.append(f"**{s}**")
            para_bold_count += 1
            continue

        out.append(s)

    return nl.join(out)


def ensure_blog_media_placeholders(body_text):
    """블로그 본문에 [미디어 자료N]/[사진 N]/[GIF] 마커가 이미 있으면 그대로 유지하고,
    전혀 없을 때만 문단 흐름에 맞춰 [미디어 자료1], [미디어 자료2], ... 자리를 자동 배치"""
    nl = chr(10)
    text = (body_text or '').strip()
    if not text:
        return text
    if BLOG_MEDIA_MARKER_ANY_RE.search(text):
        return text

    # 하단 구분선(━━━)이 있으면 본문 영역만 대상으로 문단 배치
    divider_m = re.search(r'\n*━{3,}', text)
    if divider_m:
        main_part = text[:divider_m.start()].strip()
        tail_part = text[divider_m.start():].lstrip('\n')
    else:
        main_part = text
        tail_part = ''

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', main_part) if p.strip()]
    if len(paragraphs) <= 1:
        # 빈 줄 구분이 없는 본문은 3~4줄 단위로 자연스럽게 문단 분할
        lines = [l.strip() for l in main_part.splitlines() if l.strip()]
        if len(lines) >= 6:
            chunk = max(2, (len(lines) + 3) // 4)
            paragraphs = [nl.join(lines[i:i + chunk]) for i in range(0, len(lines), chunk)]
        elif len(lines) >= 3:
            mid = len(lines) // 2
            paragraphs = [nl.join(lines[:mid]), nl.join(lines[mid:])]

    n_para = len(paragraphs)
    if n_para <= 1:
        placed = f"{main_part}{nl}{nl}[미디어 자료1]"
    else:
        if n_para >= 8:
            n_markers = 6
        elif n_para >= 4:
            n_markers = 4
        else:
            n_markers = n_para

        # 첫 문단 뒤부터 마지막 문단 직전/직후까지 문단 흐름에 맞춰 고르게 위치 선정
        insert_after = []
        if n_markers <= n_para - 1:
            for k in range(n_markers):
                pos = int(round((k + 1) * (n_para - 1) / float(n_markers + 1)))
                pos = max(0, min(n_para - 2, pos))
                while pos in insert_after and pos < n_para - 2:
                    pos += 1
                if pos not in insert_after:
                    insert_after.append(pos)
            # 개수가 부족하면 남은 간격에 순서대로 보충
            for p_idx in range(n_para - 1):
                if len(insert_after) >= n_markers:
                    break
                if p_idx not in insert_after:
                    insert_after.append(p_idx)
            insert_after.sort()
        else:
            insert_after = list(range(n_para))

        out_blocks = []
        marker_no = 1
        insert_set = set(insert_after)
        for p_idx, para in enumerate(paragraphs):
            out_blocks.append(para)
            if p_idx in insert_set and marker_no <= n_markers:
                out_blocks.append(f"[미디어 자료{marker_no}]")
                marker_no += 1
        placed = f"{nl}{nl}".join(out_blocks)

    if tail_part:
        return f"{placed}{nl}{nl}{tail_part}"
    return placed
SECTION_HEADER_PATTERNS = [
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*(?:SEO\s*마케팅\s*파일명|SEO\s*파일명|파일명|파일\s*제목)\s*\](?:\*\*)?\s*:?\s*(.*)$', re.IGNORECASE), 'SEO마케팅파일명'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*(?:1\s*단계\s*[:\-·]?\s*)?메인\s*본문[^\]]*\](?:\*\*)?\s*:?\s*(.*)$', re.IGNORECASE), '메인본문'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*(?:2\s*단계\s*[:\-·]?\s*[^\]]*|세컨드[^\]]*댓글[^\]]*|보충\s*댓글[^\]]*)\](?:\*\*)?\s*:?\s*(.*)$', re.IGNORECASE), '보충댓글'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*제목\s*3종\s*\](?:\*\*)?\s*:?\s*(.*)$'), '제목3종'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*해시태그[^\]]*\](?:\*\*)?\s*:?\s*(.*)$'), '해시태그'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*숏폼\s*전용\s*본문\s*\](?:\*\*)?\s*:?\s*(.*)$'), '숏폼전용본문'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*블로그\s*전용\s*본문\s*\](?:\*\*)?\s*:?\s*(.*)$'), '블로그전용본문'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?\[\s*대댓글\s*유도용\s*텍스트\s*\](?:\*\*)?\s*:?\s*(.*)$'), '대댓글유도용텍스트'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?1\s*단계\s*[:\-·]\s*메인\s*본문(?:\*\*)?\s*:?\s*(.*)$', re.IGNORECASE), '메인본문'),
    (re.compile(r'^(?:[^\w가-힣\[\]]*\s*)?(?:\*\*|#{1,4}\s*)?2\s*단계\s*[:\-·]\s*(?:세컨드|보충\s*댓글)[^\n]*$', re.IGNORECASE), '보충댓글'),
]

def _clean_invisible_line(line):
    """인스타/본문뽑기 빈 줄용 투명 공백 문자(ㅤ 등) 제거 및 앞뒤 공백 정리"""
    cleaned = INVISIBLE_SPACE_RE.sub(' ', line or '')
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    return cleaned.strip()

def _is_divider_line(line):
    s = _clean_invisible_line(line)
    return bool(s) and bool(DIVIDER_LINE_RE.match(s))

def _match_section_header(line):
    """섹션 머리말 줄이면 (section_key, inline_rest) 반환, 아니면 (None, '')"""
    s = _clean_invisible_line(line)
    if not s:
        return None, ''
    for pat, key in SECTION_HEADER_PATTERNS:
        m = pat.match(s)
        if m:
            rest = (m.group(1) if m.lastindex else '').strip()
            return key, rest
    return None, ''

def _is_noise_label_line(line):
    """본문·제목에 들어가면 안 되는 머리말/구분선/단독 파일명 줄 판별"""
    s = _clean_invisible_line(line)
    if not s:
        return True
    if _is_divider_line(s):
        return True
    sec_key, _ = _match_section_header(s)
    if sec_key is not None:
        return True
    if MEDIA_FILENAME_RE.match(s):
        return True
    return False

def parse_multi_block_sections(data_text):
    """기존 멀티블록([숏폼 전용 본문], [블로그 전용 본문], [제목 3종], [해시태그])과
    「본문뽑기」([SEO 마케팅 파일명], [1단계: 메인 본문], [2단계: 세컨드(부계정) 업로드용 보충 댓글 세트]) 포맷을 모두 파싱"""
    sections = {}
    unassigned = []
    current_key = None

    for raw_line in (data_text or '').splitlines():
        cleaned = _clean_invisible_line(raw_line)
        # 단일 코드박스(```text ... ```) 채로 복사해 붙여넣은 경우 바깥 펜스 줄을 안전하게 무시
        if re.match(r'^`{3,}[a-zA-Z0-9_-]*$', cleaned):
            continue
        sec_key, inline_rest = _match_section_header(cleaned)
        if sec_key is not None:
            current_key = sec_key
            sections.setdefault(current_key, [])
            if inline_rest:
                sections[current_key].append(inline_rest)
            continue

        if _is_divider_line(cleaned):
            # [SEO 마케팅 파일명] 바로 뒤 구분선이 나오면 파일명 구간 종료
            if current_key == 'SEO마케팅파일명' and sections.get('SEO마케팅파일명'):
                current_key = None
            elif current_key in ('블로그전용본문', '숏폼전용본문'):
                sections[current_key].append(cleaned)
            continue

        if current_key is not None:
            sections[current_key].append(cleaned)
        else:
            unassigned.append(cleaned)

    result = {k: "\n".join(v).strip() for k, v in sections.items()}
    # 기존 키 호환: '파일제목' 조회 시에도 SEO마케팅파일명 접근 가능
    if 'SEO마케팅파일명' in result and '파일제목' not in result:
        result['파일제목'] = result['SEO마케팅파일명']
    unassigned_text = "\n".join(unassigned).strip()
    if unassigned_text:
        result['_unassigned'] = unassigned_text
    return result


IG_HANDLE_RE = re.compile(r'(?<![A-Za-z0-9._])@[A-Za-z0-9._]{2,}')
IG_DM_FOOTER_RE = re.compile(
    r'(?:(?<![A-Za-z])dm(?![A-Za-z])|디엠|예약\s*(?:및\s*)?문의|상담\s*문의|가격\s*문의|프로필\s*링크|오픈채팅|카카오톡|카톡\s*문의|link\s*in\s*bio|book(?:ing)?\s*(?:via|by|in)|reservation|for\s+appointments?)',
    re.IGNORECASE,
)
IG_SIGN_EMOJI_RE = re.compile(r'[📍✂️💈📩💌📞☎️]')
ENG_SENTENCE_RE = re.compile(r'[A-Za-z]{2,}(?:\s+[A-Za-z0-9&\-\'/,]+){1,}')


def _is_ig_signature_or_dm_line(line, salon_name='', designer_name=''):
    """타 플랫폼(유튜브/클립 등)용 핵심 문장 추출 시 인스타 커스텀 서명·DM·@아이디 줄을 제외하기 위한 판별기"""
    s = _clean_invisible_line(line)
    if not s:
        return True
    if s.startswith(('#', '📍', '📩', '💌', '📞', '☎️')):
        return True
    if IG_HANDLE_RE.search(s):
        return True
    if IG_DM_FOOTER_RE.search(s):
        return True
    if salon_name and salon_name != '우리 매장':
        s_nospace = re.sub(r'\s+', '', s)
        salon_nospace = re.sub(r'\s+', '', salon_name)
        if salon_nospace and salon_nospace in s_nospace:
            if (designer_name and designer_name in s) or any(role in s for role in ('원장', '실장', '디자이너', '팀장')) or IG_SIGN_EMOJI_RE.search(s) or '|' in s:
                return True
    if s.startswith(('✂️', '💈')) and ((designer_name and designer_name in s) or any(role in s for role in ('원장', '실장', '디자이너', '팀장', 'designer', 'director', 'stylist'))):
        return True
    return False


def _is_pure_english_line(line):
    """한글 없이 영문 문장/구절 위주로 이루어진 줄인지 판별"""
    s = _clean_invisible_line(line)
    if not s or re.search(r'[가-힣]', s):
        return False
    return bool(re.search(r'[A-Za-z]{2,}', s))


def _has_english_copy(text):
    """본문에 영문 소개 문구(2단어 이상 영문 구절 또는 순수 영문 줄)가 포함되어 있는지 판별"""
    if not text:
        return False
    for raw_line in text.splitlines():
        s = _clean_invisible_line(raw_line)
        if not s or s.startswith('#'):
            continue
        cleaned_no_handle = IG_HANDLE_RE.sub('', s)
        if _is_pure_english_line(cleaned_no_handle) and len(re.findall(r'[A-Za-z]{2,}', cleaned_no_handle)) >= 2:
            return True
        if ENG_SENTENCE_RE.search(cleaned_no_handle) and not re.search(r'[가-힣]', cleaned_no_handle):
            return True
    return False


def _has_custom_ig_footer(text, salon_name='', designer_name=''):
    """본문에 이미 매장명, @인스타아이디, 📍/✂️ 위치·디자이너 서명, DM/예약 푸터가 들어있는지 판별 (중복 푸터 방지)"""
    if not text:
        return False
    if salon_name and salon_name != '우리 매장':
        if re.sub(r'\s+', '', salon_name) in re.sub(r'\s+', '', text):
            return True
    if IG_HANDLE_RE.search(text):
        return True
    if IG_DM_FOOTER_RE.search(text):
        return True
    for raw_line in text.splitlines():
        s = _clean_invisible_line(raw_line)
        if not s:
            continue
        if s.startswith('📍'):
            return True
        if s.startswith(('✂️', '💈')) and ((designer_name and designer_name in s) or any(role in s.lower() for role in ('원장', '실장', '디자이너', '팀장', 'designer', 'director', 'stylist', '|', '/'))):
            return True
        if designer_name and designer_name in s and ('|' in s or IG_SIGN_EMOJI_RE.search(s)):
            return True
    return False


INCOMPLETE_LINE_NOUN_EXCEPTIONS_RE = re.compile(
    r'(?:헤어|레이어|케어|드라이어|스퀘어|코어|투어|퓨어|아이디어|파노라마|드라마|테마|아로마|매니아)$'
)
COMPLETE_SENTENCE_END_RE = re.compile(
    r'(?:[.!?…~]["\'\)\]]*$|(?:습니다|읍니다|입니다|랍니다|답니다|됩니다|합니다|세요|해요|돼요|네요|예요|에요|어요|아요|워요|져요|려요|줘요|나요|가요|까요|군요|거든요|잖아요|죠|요|다|까)["\'\)\]]*$)'
)
INCOMPLETE_CONNECTIVE_END_RE = re.compile(
    r'(?:,|،|'
    r'하면서|되면서|있으면서|없으면서|으로써|으로서|에서는|에서도|으로는|으로도|'
    r'하는데|되는데|있는데|없는데|겠지만|하지만|이지만|면서도|더라도|이라면|하려면|'
    r'되도록|하도록|만들어|올려주어|잡아주어|덜어내어|살려주어|채워주어|눌러주어|정리하여|보완하여|맞추어|연결하여|'
    r'하여|해서|돼서|되어|라서|이라서|여서|니까|으니까|므로|으므로|는데|은데|ㄴ데|'
    r'면서|으며|지며|하며|되며|지만|으나|거나|든지|도록|려고|으려고|고자|고서|'
    r'하고|되고|있고|없고|잡고|넣고|살리고|줄이고|덜고|누르고|채우고|올리고|내리고|자르고|다듬고|맞추고|정리하고|보완하고|연결하고|말리고|'
    r'이라|에서|으로|까지|부터|처럼|없이|위해|통해|'
    r'고|며|서|어|아|해|워|져|려)$'
)
SHORTFORM_BULLET_OR_FOOTER_START_RE = re.compile(
    r'^(?:[•✔✓💡📍📌✂️💈📩💌📞☎️👉👇✨🔹🔸\-]\s*|\d+[\.\)]\s*|@)'
)


def _is_incomplete_korean_line(line):
    """줄 끝이 쉼표 또는 연결어미로 끝나고 문장이 종결되지 않은 미완결 줄인지 판별"""
    s = _clean_invisible_line(line)
    if not s:
        return False
    if COMPLETE_SENTENCE_END_RE.search(s):
        return False
    if s.endswith((',', '،')):
        return True
    if INCOMPLETE_LINE_NOUN_EXCEPTIONS_RE.search(s):
        return False
    return bool(INCOMPLETE_CONNECTIVE_END_RE.search(s))


def merge_incomplete_lines(lines):
    """줄바꿈으로 쪼개진 미완결 문장(쉼표·연결어미 끝)을 다음 줄과 공백 1칸으로 자동 병합해 완결 문장 리스트 반환.
    '오늘의 시술 요약' 블록 줄(헤더·시술명/모발 상태/추천 대상)은 앞뒤 문장과 병합하지 않고 단독 줄로 보호."""
    cleaned = [_clean_invisible_line(l) for l in (lines or []) if _clean_invisible_line(l)]
    if not cleaned:
        return []
    merged = []
    idx = 0
    while idx < len(cleaned):
        cur = cleaned[idx]
        while (
            idx + 1 < len(cleaned)
            and not _is_blog_summary_line(cur)
            and _is_incomplete_korean_line(cur)
            and not _is_blog_summary_line(cleaned[idx + 1])
            and not SHORTFORM_BULLET_OR_FOOTER_START_RE.match(cleaned[idx + 1])
        ):
            nxt = cleaned[idx + 1]
            cur = f"{cur} {nxt}".strip()
            idx += 1
        merged.append(cur)
        idx += 1
    return merged


def format_shortform_readable(text):
    """숏폼·소통 플랫폼 전용 포맷터:
    글자 수(24~28자) 기준의 기계적 강제 줄바꿈을 하지 않고,
    완결된 문장 단위 줄바꿈과 문단 사이 빈 줄 1칸(\n\n)만 깔끔하게 정리"""
    nl = chr(10)
    raw = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not raw:
        return ''
    out = []
    blank_streak = 0
    for raw_line in raw.splitlines():
        s = _clean_invisible_line(raw_line)
        if not s:
            if out and blank_streak == 0:
                out.append('')
                blank_streak += 1
            continue
        out.append(s)
        blank_streak = 0
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return nl.join(out).strip()


def _is_ig_footer_line(line, salon_name='', designer_name=''):
    s = _clean_invisible_line(line)
    if not s:
        return False
    if s.startswith(('📍', '✂️', '📌', '💈', '📩', '💌', '☎️', '📞', '@')):
        return True
    if IG_HANDLE_RE.search(s) or IG_DM_FOOTER_RE.search(s):
        return True
    if salon_name and salon_name != '우리 매장' and re.sub(r'\s+', '', salon_name) in re.sub(r'\s+', '', s):
        return True
    if designer_name and designer_name in s and ('|' in s or '/' in s or IG_SIGN_EMOJI_RE.search(s)):
        return True
    return False


def _add_instagram_breathing_room(text, salon_name='', designer_name=''):
    """인스타그램 원문 말투·이모지·영문·커스텀 서명을 100% 보존하되,
    원문에 빈 줄이 하나도 없이 4줄 이상 빽빽하게 붙어 있는 경우에만
    도입부 / 본문 / 손질 팁 / 하단 푸터 사이에 빈 줄 1칸(\n\n)을 자동 삽입"""
    nl = chr(10)
    base = format_shortform_readable(text)
    if not base or f"{nl}{nl}" in base:
        return base
    lines = [l for l in base.splitlines() if l.strip()]
    if len(lines) < 4:
        return base

    # 하단 연속 푸터 영역 분리
    footer_start = len(lines)
    while footer_start > 1 and _is_ig_footer_line(lines[footer_start - 1], salon_name=salon_name, designer_name=designer_name):
        footer_start -= 1
    body_lines = lines[:footer_start]
    footer_lines = lines[footer_start:]

    # 본문 줄들을 쉼표/연결어미 연속 줄이 갈라지지 않게 최소 문장 블록으로 묶음
    blocks = []
    i = 0
    while i < len(body_lines):
        grp = [body_lines[i]]
        while i + 1 < len(body_lines) and _is_incomplete_korean_line(body_lines[i]) and not SHORTFORM_BULLET_OR_FOOTER_START_RE.match(body_lines[i + 1]):
            i += 1
            grp.append(body_lines[i])
        blocks.append(nl.join(grp))
        i += 1

    paragraphs = []
    if len(blocks) >= 3:
        paragraphs.append(blocks[0])
        if len(blocks) == 3:
            paragraphs.append(blocks[1])
            paragraphs.append(blocks[2])
        else:
            paragraphs.append(nl.join(blocks[1:-1]))
            paragraphs.append(blocks[-1])
    elif len(blocks) == 2:
        paragraphs.append(blocks[0])
        paragraphs.append(blocks[1])
    elif blocks:
        paragraphs.append(blocks[0])

    if footer_lines:
        paragraphs.append(nl.join(footer_lines))

    return f"{nl}{nl}".join([p for p in paragraphs if p.strip()]).strip()


def _prepare_instagram_preserved_body(raw_text):
    """[숏폼 전용 본문]의 말투·이모지·줄바꿈·영문 문구·커스텀 서명을 원형 그대로 보존 (순수 해시태그 줄·섹션 헤더만 안전 제거)"""
    nl = chr(10)
    out = []
    blank_streak = 0
    for raw_line in (raw_text or '').splitlines():
        cleaned = _clean_invisible_line(raw_line)
        if not cleaned:
            if out and blank_streak == 0:
                out.append('')
                blank_streak += 1
            continue
        if _match_section_header(cleaned)[0] is not None:
            continue
        if MEDIA_FILENAME_RE.match(cleaned):
            continue
        # 순수 해시태그로만 구성된 줄은 인스타그램 태그 칸으로 분리되므로 본문에서 제외
        without_tags = _clean_invisible_line(re.sub(r'#[^\s#]+', '', cleaned))
        if not without_tags:
            continue
        # 문장 끝에 붙은 해시태그 뭉치만 정리 (본문 텍스트·@아이디·이모지·영문은 그대로 유지)
        line_val = re.sub(r'(?:\s+#[^\s#]+)+$', '', cleaned).strip()
        if not line_val:
            continue
        out.append(line_val)
        blank_streak = 0

    # 앞뒤 빈 줄 및 섹션 경계용 앞뒤 단독 구분선만 정리 (본문 내부 구분선은 유지)
    while out and (not out[0] or _is_divider_line(out[0])):
        out.pop(0)
    while out and (not out[-1] or _is_divider_line(out[-1])):
        out.pop()
    return nl.join(out).strip()


_TITLE_LABEL_CORE_RE = r'(?:메인\s*후킹형|결과\s*중심형|로컬\s*검색형|후킹형|검색형|블로그형)'


def _parse_titles_block(titles_block):
    """[제목3종] 블록에서 번호·따옴표·괄호·역할 라벨 전용 줄/접두사를 정제해 실제 제목 목록만 추출"""
    parsed = []
    for l in (titles_block or '').splitlines():
        if _is_noise_label_line(l):
            continue
        s = _clean_invisible_line(l).strip()
        if not s:
            continue
        # 앞쪽 불릿 및 번호(1., 2., 3., 1), 2), 3)) 제거
        s = re.sub(r'^(?:[-•*]\s*)?(?:[1-3][\.\)]\s*)?', '', s).strip()
        s = s.strip('*').strip()
        if not s:
            continue

        # 1) 줄 전체가 라벨 전용 줄인지 판별(앞뒤 괄호·따옴표·구분자 포함)
        label_probe = re.sub(r'^[\s"\'\u201c\u201d\u2018\u2019\[\(\{\【\「\『\*]+', '', s)
        label_probe = re.sub(r'[\s"\'\u201c\u201d\u2018\u2019\]\)\}\】\」\』\*:\-|—–·]+$', '', label_probe)
        if re.fullmatch(_TITLE_LABEL_CORE_RE, label_probe):
            continue

        # 2) 줄 앞 라벨 접두사 제거 ([메인 후킹형] ..., 메인 후킹형: ..., 결과 중심형 - ..., 로컬 검색형 | ...)
        s = re.sub(
            rf'^(?:[\[\(\{{\【\「\『\*]+)\s*{_TITLE_LABEL_CORE_RE}\s*(?:[\]\)\}}\】\」\』\*]+)\s*(?:[:\-|—–·]\s*)?',
            '',
            s,
        ).strip()
        s = re.sub(
            rf'^{_TITLE_LABEL_CORE_RE}\s*[:\-|—–·]\s*',
            '',
            s,
        ).strip()

        # 3) 앞뒤 따옴표·괄호 장식 정리
        s = s.strip().strip('*').strip()
        while len(s) >= 2 and (
            (s[0] == '"' and s[-1] == '"')
            or (s[0] == "'" and s[-1] == "'")
            or (s[0] == '\u201c' and s[-1] == '\u201d')
            or (s[0] == '\u2018' and s[-1] == '\u2019')
            or (s[0] == '(' and s[-1] == ')' and s.count('(') == 1 and s.count(')') == 1)
            or (s[0] == '[' and s[-1] == ']' and s.count('[') == 1 and s.count(']') == 1)
        ):
            s = s[1:-1].strip()
        s = s.strip('"').strip("'").strip()

        if not s or re.fullmatch(rf'{_TITLE_LABEL_CORE_RE}[\s:\-|—–·]*', s):
            continue
        parsed.append(normalize_branch_name(s))
    return parsed


DEMO_FALLBACK_PROFILE = {
    'salon_name': '플랜에이치 마곡나루점',
    'region_keyword': '마곡나루미용실, 마곡미용실, 발산역미용실',
    'designer_name': '카일 원장',
    'specialty': '남자 다운펌·드롭컷·열펌 / 집에서 손질 편한 머리를 추구합니다',
    'salon_address': '서울 강서구 마곡센트럴타워2 310, 311호',
    'location_guide': '마곡나루역 1번출구',
    'parking_guide': '건물 지하주차장 매장이용시간 지원',
}


def _build_effective_demo_profile(prof=None):
    """미설정 프로필 필드를 메모리에서만 DEMO_FALLBACK_PROFILE로 임시 병합 (user_profile.json 미저장)."""
    base = dict(prof) if isinstance(prof, dict) else dict(load_profile() or {})
    used_demo = False
    for k, demo_val in DEMO_FALLBACK_PROFILE.items():
        if not str(base.get(k) or '').strip():
            base[k] = demo_val
            used_demo = True
    return base, used_demo


def build_demo_sample_master_text(profile=None):
    """온보딩 2단계 예시(또는 현재 프로필)를 반영한 풍성한 시술 마스터 샘플 원고 조립."""
    eff_prof, _ = _build_effective_demo_profile(profile)
    salon_name = str(eff_prof.get('salon_name') or DEMO_FALLBACK_PROFILE['salon_name']).strip()
    designer_name = str(eff_prof.get('designer_name') or DEMO_FALLBACK_PROFILE['designer_name']).strip()
    specialty = str(eff_prof.get('specialty') or DEMO_FALLBACK_PROFILE['specialty']).strip()
    raw_regions = [
        r.strip().lstrip('#')
        for r in re.split(r'[,\n]+', str(eff_prof.get('region_keyword') or DEMO_FALLBACK_PROFILE['region_keyword']))
        if r.strip().lstrip('#')
    ]
    if not raw_regions:
        raw_regions = ['마곡나루미용실', '마곡미용실', '발산역미용실']

    primary_region_tag = raw_regions[0]
    region_base = re.sub(r'(미용실|헤어샵|헤어살롱|헤어)$', '', primary_region_tag).strip() or '마곡나루'
    region_hash_list = []
    for r in raw_regions[:3]:
        clean_r = re.sub(r'\s+', '', r)
        if clean_r and clean_r not in region_hash_list:
            region_hash_list.append(clean_r)

    main_tags = region_hash_list + [
        '남자드롭컷', '전체다운펌', '남자짧은머리', '옆머리다운펌',
        '반곱슬남자머리', '손질편한머리', '남자머리추천', '남자헤어스타일',
        '다운펌잘하는곳',
    ]
    seen_main = set()
    dedup_main_tags = []
    for t in main_tags:
        tl = t.lower()
        if tl not in seen_main:
            seen_main.add(tl)
            dedup_main_tags.append(f'#{t}')

    sub_tags = ['뜨는옆머리', '구레나룻다운펌', '남자직모머리', '직장인남자머리', '남자드라이팁']
    dedup_sub_tags = [f'#{t}' for t in sub_tags if t.lower() not in seen_main][:5]

    file_region = re.sub(r'\s+', '', primary_region_tag) or '마곡나루미용실'
    specialty_note = ''
    if '/' in specialty:
        specialty_note = specialty.split('/', 1)[1].strip()
    if not specialty_note:
        specialty_note = '집에서 손질 편한 머리를 추구합니다'

    intro_greeting = f"안녕하세요. {region_base} 미용실 {salon_name} {designer_name}입니다."
    short_signature = f"{salon_name} {designer_name}"

    return f"""[파일제목]
{file_region}_남자_드롭컷_전체다운펌_반곱슬_손질편한_짧은머리

[제목3종]
1. 숱 많고 뜨는 반곱슬 옆머리, 드롭컷 + 전체 다운펌으로 10초 완성
2. 아침마다 부스스하게 뜨는 남자 짧은 머리, 전체 다운펌으로 깔끔하게 정리
3. {region_base} 미용실 남자 드롭컷 전체 다운펌 자연스러운 볼륨과 슬림한 라인

[해시태그]
{' '.join(dedup_main_tags)}

[숏폼 전용 본문]
숱이 많고 반곱슬이라 조금만 길어도 옆머리가 붕 뜨고
아침마다 눌러주느라 스트레스받던 모발입니다.

얼굴형에 맞춰 앞머리 기장을 정돈한 드롭컷으로 가벼운 결감을 살리고,
강하게 뜨는 옆머리와 구레나룻·뒷머리 아래쪽은 두상에 맞게 전체 다운펌으로 차분하게 밀착시켰습니다.

시술 후에는 샴푸하고 타월 드라이한 뒤
앞쪽 방향으로 10초만 툭툭 털어 말려 주시면 별다른 세팅 없이도 깔끔한 실루엣이 유지됩니다.

{specialty_note}.
편한 손질과 단정한 인상을 원하신다면 {short_signature}에게 상담해 주세요.

[블로그 전용 본문]
{intro_greeting}
{specialty_note}. 오늘은 숱이 많고 반곱슬 기질이 있어 옆머리가 금방 부해지던 고객님의 **드롭컷과 전체 다운펌 시술 과정**을 정리해 드리겠습니다.

**오늘의 시술 요약**
시술명 : 남자 드롭컷 + 전체 다운펌
모발 상태 : 숱이 많고 옆·뒷머리가 강하게 뜨는 반곱슬 모발
추천 대상 : 아침 10초 드라이만으로 깔끔한 짧은 머리를 유지하고 싶은 분

[미디어 자료1]

모발 상태와 두상 고민 진단

시술 전 상담에서 가장 크게 말씀해 주신 고민은 머리가 조금만 자라도 관자놀이와 옆머리가 바깥으로 뻗쳐 얼굴형이 넓어 보인다는 점이었습니다.
반곱슬 특유의 부스스함과 많은 머리숱이 겹치면서 윗머리 볼륨과 옆머리 라인의 경계가 무너져 보이기 쉬운 상태였습니다.
무조건 짧게 밀어 올리기보다 두상 굴곡에 맞춰 옆선은 슬림하게 눌러 주고 윗머리의 자연스러운 입체감은 살리는 방향으로 디자인을 잡았습니다.

[미디어 자료2]

얼굴형 맞춘 드롭컷 라인 설계

앞머리는 이마를 과하게 가리지 않으면서도 부드러운 인상을 주는 눈썹 위 기장으로 설정했습니다.
드롭컷의 핵심은 앞쪽 중앙으로 자연스럽게 모이는 흐름과 가벼운 질감 처리이기 때문에, 숱이 뭉치기 쉬운 구역을 꼼꼼하게 덜어내어 말리기만 해도 결이 살아나도록 커트했습니다.
구레나룻과 귀 뒤 라인은 깔끔하게 정돈해 정면은 물론 옆모습까지 단정하게 연결했습니다.

[미디어 자료3]

모질에 맞춘 전체 다운펌 과정

옆머리와 뒷머리 하단처럼 뻗치는 힘이 강한 구간은 모발 굵기와 자라는 방향을 확인해 약제를 세밀하게 도포했습니다.
과하게 눌려 두상에 납작하게 붙어 보이지 않도록 관자놀이와 뒤통수 볼륨 포인트는 자연스럽게 연결해 입체감을 유지했습니다.
반곱슬로 부해지기 쉬운 부분의 결을 함께 정돈해 시간이 지나 머리가 자라도 지저분하게 뜨지 않도록 마무리했습니다.

[미디어 자료4]

아침 10초 털어 말리는 손질법

집에서 손질하실 때는 샴푸 후 수건으로 물기를 충분히 털어낸 뒤 드라이기 바람을 위에서 앞쪽 방향으로 보내 10초 정도 가볍게 털어 말려 주시면 됩니다.
옆머리는 바람을 살짝 쐰 뒤 손바닥으로 2~3초 눌러 열을 식혀 주시면 하루 종일 슬림한 라인이 안정적으로 유지됩니다.
매트 왁스나 가벼운 에센스를 소량만 손끝에 덜어 앞머리 결감만 살짝 잡아 주셔도 깔끔한 스타일이 완성됩니다.

[미디어 자료5]

숱이 많거나 반곱슬 때문에 짧은 머리를 망설이셨다면 두상과 모질에 맞춘 드롭컷과 전체 다운펌 조합이 좋은 해답이 될 수 있습니다.
{salon_name} {designer_name}이 고객님 한 분 한 분의 모발 방향과 손질 습관에 맞춰 집에서도 가장 편하게 유지할 수 있는 스타일을 제안해 드리겠습니다.

[대댓글 유도용 텍스트]
평소 옆머리가 금방 뜨거나 반곱슬 때문에 아침 손질이 번거로우셨나요?
현재 기장이나 모발 고민을 댓글로 남겨주시면 어울리는 드롭컷 기장과 다운펌 방향을 편하게 안내해 드릴게요!
{' '.join(dedup_sub_tags)}"""


DEMO_SAMPLE_MASTER_TEXT = build_demo_sample_master_text(DEMO_FALLBACK_PROFILE)


def convert_master_content(data_text, video_name='', profile_override=None):
    """순수 파이썬 메인 원고 변환 엔진 (API 키 불필요 · 8개 플랫폼 파싱·규격 분배 전용)"""
    nl = chr(10)
    prof = profile_override if isinstance(profile_override, dict) else load_profile()
    salon_name = get_salon_name(prof, fallback='우리 매장')
    region_kw = get_region_keyword(prof)
    region_title_prefix = f"{region_kw} 미용실 " if region_kw else (f"{salon_name} " if salon_name != '우리 매장' else "")

    designer_name = (prof.get('designer_name') or '').strip()
    designer_label = f" {designer_name}" if designer_name else ""
    designer_pipe = f" | {designer_name}" if designer_name else ""

    data_text = normalize_branch_name(data_text or '')
    sections = parse_multi_block_sections(data_text)
    is_multi_block = ('숏폼전용본문' in sections or '블로그전용본문' in sections)
    is_bonmun_format = (not is_multi_block) and any(k in sections for k in ('메인본문', 'SEO마케팅파일명', '보충댓글', '대댓글유도용텍스트', '제목3종', '해시태그'))
    seo_filename = sections.get('SEO마케팅파일명', '') or sections.get('파일제목', '')
    short_section_raw = sections.get('숏폼전용본문', '')
    sub_comment_block = sections.get('대댓글유도용텍스트', '') or sections.get('보충댓글', '')

    if is_multi_block:
        short_raw = short_section_raw or sections.get('메인본문', '') or sections.get('_unassigned', '') or data_text
        blog_custom_body = sections.get('블로그전용본문', '')
        titles_block = sections.get('제목3종', '')
        tags_block = sections.get('해시태그', '')
        main_tags_raw = re.findall(r'#([^\s#]+)', tags_block) if tags_block else re.findall(r'#([^\s#]+)', short_raw)
        raw_lines = [_clean_invisible_line(l) for l in short_raw.splitlines() if _clean_invisible_line(l)]
    elif is_bonmun_format:
        short_raw = sections.get('메인본문', '') or sections.get('_unassigned', '')
        blog_custom_body = ''
        titles_block = sections.get('제목3종', '')
        tags_block = sections.get('해시태그', '')
        main_tags_raw = re.findall(r'#([^\s#]+)', tags_block) if tags_block else re.findall(r'#([^\s#]+)', short_raw)
        raw_lines = [_clean_invisible_line(l) for l in short_raw.splitlines() if _clean_invisible_line(l)]
    else:
        short_raw = data_text
        blog_custom_body = ''
        titles_block = ''
        raw_lines = [_clean_invisible_line(l) for l in data_text.splitlines() if _clean_invisible_line(l)]
        main_tags_raw = re.findall(r'#([^\s#]+)', data_text)

    sub_tags_raw = re.findall(r'#([^\s#]+)', sub_comment_block) if sub_comment_block else []

    if blog_custom_body:
        blog_custom_body = nl.join([
            _clean_invisible_line(l) for l in blog_custom_body.splitlines()
            if _match_section_header(l)[0] is None
        ]).strip()

    all_non_sig_lines = []
    kr_content_lines = []
    for l in raw_lines:
        if _is_noise_label_line(l):
            continue
        if _is_ig_signature_or_dm_line(l, salon_name=salon_name, designer_name=designer_name):
            continue
        stripped_line = _clean_invisible_line(re.sub(r'#[^\s#]+', '', l))
        if stripped_line and not _is_noise_label_line(stripped_line):
            all_non_sig_lines.append(stripped_line)
            if not _is_pure_english_line(stripped_line):
                kr_content_lines.append(stripped_line)

    raw_content_lines = kr_content_lines or all_non_sig_lines or ['차분하고 깔끔하게 정돈된 맞춤 헤어 스타일입니다.']
    content_lines = merge_incomplete_lines(raw_content_lines) or ['차분하고 깔끔하게 정돈된 맞춤 헤어 스타일입니다.']

    seo_hint = re.sub(r'[_\-\.]+', ' ', _clean_invisible_line(seo_filename))
    full_line_text = ' '.join(content_lines)
    combined_hint = f"{full_line_text} {blog_custom_body} {seo_hint} {' '.join(main_tags_raw)} {video_name or STATE.get('video_name', '')}"
    user_blog_cats = get_blog_categories(prof)
    recommended_blog_cat = recommend_blog_category(combined_hint, user_blog_cats) if user_blog_cats else ""
    style_kws, tags_5, tags_10 = build_smart_tags(main_tags_raw, sub_tags_raw)

    kr_style_kws = [k for k in style_kws if re.search(r'[가-힣]', k)] or style_kws
    main_kw = ' '.join(kr_style_kws[:2]) if kr_style_kws else '맞춤 헤어'
    sub_kw = kr_style_kws[2] if len(kr_style_kws) > 2 else (kr_style_kws[0] if kr_style_kws else '커트')
    desc_phrase = (
        f"차르르 떨어지는 {sub_kw}".strip()
        if '차르르' in full_line_text
        else (f"끝선이 깔끔한 {sub_kw}".strip() if '끝선' in full_line_text else f"자연스럽게 떨어지는 {sub_kw}".strip())
    )

    hook_line = normalize_branch_name(content_lines[0].strip().strip('"').strip("'"))
    if len(content_lines) >= 3:
        mid_lines = [normalize_branch_name(x) for x in content_lines[1:-1]]
        care_comment = normalize_branch_name(content_lines[-1].strip())
    elif len(content_lines) == 2:
        mid_lines = [normalize_branch_name(content_lines[1].strip())]
        care_comment = normalize_branch_name(content_lines[1].strip())
    else:
        mid_lines = []
        care_comment = "샴푸 후 두피부터 털어 말리고 에센스만 소량 바르면 라인이 잡힙니다."

    parsed_titles = _parse_titles_block(titles_block)
    if parsed_titles:
        hook_line = parsed_titles[0] or hook_line
    naver_title = smart_trim_text(
        normalize_branch_name(
            parsed_titles[2] if len(parsed_titles) >= 3 else f"{region_title_prefix}{main_kw}, {desc_phrase}".strip()
        ),
        50,
    )

    style_core = kr_style_kws[0] if kr_style_kws else main_kw

    blog_source_body = blog_custom_body if blog_custom_body else nl.join(content_lines).strip()
    naver_body_styled = apply_blog_auto_styling(
        ensure_blog_media_placeholders(normalize_branch_name(blog_source_body))
    )
    naver_body = format_mobile_readable(
        naver_body_styled,
        max_line_len=28,
        preserve_media_markers=True,
    )

    # 플랫폼별 인기 글쓰기 틀 적용 (숏폼·소통 플랫폼은 문장·문단 호흡 중심 포맷터 적용 · 문장/어절 단위 안전 축약)
    yt_title_cand = normalize_branch_name(f"{hook_line} | {style_core}" if style_core not in hook_line else hook_line)
    yt_title = yt_title_cand if len(yt_title_cand) <= 55 else smart_trim_text(normalize_branch_name(hook_line), 55)
    yt_points = [f"• {l}" for l in mid_lines[:2]] if mid_lines else [f"• {main_kw} 맞춤 디자인"]
    yt_body = format_shortform_readable(
        normalize_branch_name(
            f"{hook_line}{nl}{nl}"
            f"{nl.join(yt_points)}{nl}"
            f"• 손질 팁: {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}"
        )
    )

    clip_title_cand = normalize_branch_name(hook_line if style_core in hook_line else f"{style_core} | {hook_line}")
    clip_title = smart_trim_text(clip_title_cand, 38)
    clip_mid = nl.join([f"✔ {l}" for l in mid_lines[:2]]) if mid_lines else f"✔ {main_kw} 맞춤 시술"
    clip_body = format_shortform_readable(
        normalize_branch_name(
            f"{hook_line}{nl}{nl}"
            f"{clip_mid}{nl}{nl}"
            f"💡 {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}{nl}"
            f"아래 장소 태그에서 바로 예약 가능해요."
        )
    )

    tt_title = smart_trim_text(normalize_branch_name(hook_line), 45)
    tt_main = mid_lines[0] if mid_lines else (content_lines[1] if len(content_lines) >= 2 else '')
    if tt_main and tt_main != care_comment:
        tt_raw = (
            f"{tt_main}{nl}{nl}"
            f"💡 {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}"
        )
    else:
        tt_raw = (
            f"{hook_line}{nl}{nl}"
            f"💡 {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}"
        )
    tt_body = format_shortform_readable(normalize_branch_name(tt_raw))

    ig_preserved = _prepare_instagram_preserved_body(short_section_raw if short_section_raw else short_raw)
    ig_has_custom_footer = _has_custom_ig_footer(ig_preserved, salon_name=salon_name, designer_name=designer_name)
    ig_has_english = _has_english_copy(ig_preserved)

    if ig_preserved and (bool(short_section_raw) or ig_has_custom_footer or ig_has_english):
        ig_spaced = _add_instagram_breathing_room(ig_preserved, salon_name=salon_name, designer_name=designer_name)
        if ig_has_custom_footer:
            ig_body = normalize_branch_name(ig_spaced)
        else:
            ig_body = format_shortform_readable(
                normalize_branch_name(
                    f"{ig_spaced}{nl}{nl}"
                    f"📌 저장해두고 상담 때 보여주세요{nl}"
                    f"📍 {salon_name}{designer_pipe}"
                )
            )
    else:
        ig_mid = nl.join([f"✔ {l}" for l in mid_lines[:2]]) if mid_lines else f"✔ {main_kw} 맞춤 정리"
        ig_base = (
            f"{hook_line}{nl}{nl}"
            f"{ig_mid}{nl}{nl}"
            f"💡 {care_comment}"
        )
        if not _has_custom_ig_footer(ig_base, salon_name=salon_name, designer_name=designer_name):
            ig_base = (
                f"{ig_base}{nl}{nl}"
                f"📌 저장해두고 상담 때 보여주세요{nl}"
                f"📍 {salon_name}{designer_pipe}"
            )
        ig_body = format_shortform_readable(normalize_branch_name(ig_base))

    _pin_region_suffix_re = re.compile(r'(?:미용실|헤어샵|헤어살롱|헤어)$')
    pin_style_cands = [k for k in style_kws if k and not _pin_region_suffix_re.search(k.strip())]
    pin_kr_styles = [k for k in pin_style_cands if re.search(r'[가-힣]', k)] or pin_style_cands
    pin_main_kw = ' '.join(pin_kr_styles[:2]) if pin_kr_styles else main_kw
    pin_suffix = f" | {salon_name}" if salon_name else ""
    pin_head_full = normalize_branch_name(f"{pin_main_kw}, {desc_phrase}")
    if len(pin_head_full) + len(pin_suffix) <= 52:
        pin_title = f"{pin_head_full}{pin_suffix}"
    elif pin_suffix and len(pin_suffix) < 36:
        pin_title = f"{smart_trim_text(pin_head_full, 52 - len(pin_suffix))}{pin_suffix}"
    else:
        pin_title = smart_trim_text(normalize_branch_name(f"{pin_head_full}{pin_suffix}"), 52)

    pin_sep = " " if hook_line.endswith((".", "!", "?")) else ". "
    pin_summary = normalize_branch_name(f"{hook_line}{pin_sep}{care_comment}".strip())
    pin_prefix = normalize_branch_name(f"[{salon_name}{designer_label}] ")
    pin_cta = "예약·상담은 아래 링크에서 확인해 주세요."
    pin_full = f"{pin_prefix}{pin_summary} {pin_cta}"
    if len(pin_full) <= 220:
        pin_body = pin_full
    else:
        pin_mid_budget = max(30, 220 - len(pin_prefix) - len(pin_cta) - 1)
        pin_mid_trimmed = smart_trim_text(pin_summary, pin_mid_budget)
        pin_body = smart_trim_text(f"{pin_prefix}{pin_mid_trimmed} {pin_cta}", 220)

    tags_5_str = format_clean_tags(tags_5)
    tags_10_str = format_clean_tags(tags_10)

    return {
        'naver_category': recommended_blog_cat,
        'youtube_title': yt_title, 'youtube_body': yt_body, 'youtube_tags': tags_10_str,
        'naver_title': naver_title, 'naver_body': naver_body, 'naver_tags': tags_10_str,
        'clip_title': clip_title, 'clip_body': clip_body, 'clip_tags': tags_5_str,
        'tiktok_title': tt_title, 'tiktok_body': tt_body, 'tiktok_tags': tags_5_str,
        'instagram_body': ig_body, 'instagram_tags': tags_5_str,
        'pin_title': pin_title, 'pin_body': pin_body, 'pin_tags': tags_5_str,
        'fb_body': ig_body, 'fb_tags': tags_5_str,
        'fb_page_body': ig_body, 'fb_page_tags': tags_5_str,
    }

# 기존 내부 호출 호환 별칭 (순수 파이썬 메인 변환 엔진 단일화)
fallback_convert = convert_master_content

def upload_to_youtube(title, body='', tags=None, video_path=None, privacy='public', thumbnail_path=None):
    migrate_legacy_auth_files()
    scopes = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
    token_path, secret_path = get_auth_file('youtube_token.json'), get_auth_file('client_secret.json')
    if not os.path.exists(secret_path) or os.path.getsize(secret_path) <= 10:
        raise RuntimeError("client_secret.json 파일이 없습니다. 초기 설정 가이드 1단계를 확인해 주세요.")
    creds = Credentials.from_authorized_user_file(token_path, scopes) if os.path.exists(token_path) else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            log(f"⚠️ [유튜브] 기존 토큰 갱신 실패({e}) → 구글 계정 재인증으로 자동 전환합니다.")
            creds = None
    if not (creds and creds.valid):
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, scopes)
        try:
            try:
                creds = flow.run_local_server(port=0, prompt='select_account', timeout_seconds=120)
            except TypeError:
                creds = flow.run_local_server(port=0, prompt='select_account')
        except Exception as auth_err:
            log("⚠️ [유튜브] 구글 계정 인증 시간이 초과(120초)되었거나 취소되었습니다.")
            raise RuntimeError(f"구글 계정 인증 시간 초과(120초) 또는 취소됨: {auth_err}")
        if not (creds and creds.valid):
            log("⚠️ [유튜브] 구글 계정 인증 시간이 초과(120초)되었거나 취소되었습니다.")
            raise RuntimeError("구글 계정 인증 시간 초과(120초) 또는 취소됨")
    with open(token_path, 'w', encoding='utf-8') as f:
        f.write(creds.to_json())
    yt = build('youtube', 'v3', credentials=creds)
    nl = chr(10)

    prof = load_profile()
    salon_name = get_salon_name(prof, fallback='미용실')
    salon_addr = get_salon_address(prof)
    designer_name = (prof.get('designer_name') or '').strip()
    designer_label = f" {designer_name}" if designer_name else ""
    booking_url = (prof.get('booking_url') or '').strip()
    booking_line = f"👉 예약·상담: {booking_url}{nl}" if booking_url else ""
    addr_line = f"📍 {salon_addr}{nl}" if salon_addr else ""

    clean_tags = [t.replace('#', '').strip() for t in (tags or []) if t.replace('#', '').strip()]
    yt_tags = list(dict.fromkeys(clean_tags))[:10]
    tail_hash = ' '.join(['#Shorts'] + [f'#{t}' for t in yt_tags[:3]]).strip()

    footer = f"{salon_name}{designer_label} (1:1 우선 예약제){nl}{booking_line}{addr_line}{tail_hash}"
    desc = (normalize_branch_name(body or '').strip() + nl + nl + footer).strip()

    req_body = {
        'snippet': {
            'title': normalize_branch_name(title or salon_name)[:100],
            'description': desc,
            'tags': yt_tags,
            'categoryId': '26',
            'defaultLanguage': 'ko',
            'defaultAudioLanguage': 'ko',
        },
        'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': False},
    }
    res = yt.videos().insert(part='snippet,status', body=req_body, media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)).execute()
    video_id = res['id']
    short_url = 'https://youtube.com/shorts/' + video_id
    if thumbnail_path and os.path.isfile(thumbnail_path):
        thumb_ext = os.path.splitext(thumbnail_path)[1].lower()
        if thumb_ext in ('.jpg', '.jpeg', '.png'):
            try:
                yt.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, resumable=True),
                ).execute()
                log(f"🖼️ [유튜브 쇼츠] 대표사진 기준 썸네일 적용 완료! ({os.path.basename(thumbnail_path)})")
            except Exception as thumb_err:
                log(f"⚠️ [유튜브 쇼츠] 커스텀 썸네일 적용 건너뜀({thumb_err}) — 쇼츠 영상 업로드는 정상 완료되었습니다.")
    return short_url

AUTH_CHECK_TARGETS = [
    # 1) 네이버 계열 (블로그·클립 쿠키 공유 → 동시 미로그인 시 1탭 통합)
    ('use_naver',   'naver',   'naver_cookies.json',     '네이버 블로그', 'https://blog.naver.com/GoBlogWrite.naver'),
    ('use_clip',    'clip',    'naver_cookies.json',     '네이버 클립',   'https://clipcreators.naver.com/web'),
    # 2) 개인 숏폼 SNS
    ('use_ig',      'ig',      'instagram_cookies.json', '인스타그램',    'https://www.instagram.com/'),
    ('use_tt',      'tt',      'tiktok_cookies.json',    '틱톡',          'https://www.tiktok.com/tiktokstudio'),
    # 3) 페이스북 계열 (개인·매장 쿠키 공유 → 선택·미로그인 시에만 열며 동시 요청 시 1탭 통합)
    ('use_fb',      'fb',      'facebook_cookies.json',  '페이스북(개인)', 'https://www.facebook.com/'),
    ('use_fb_page', 'fb_page', 'facebook_cookies.json',  '페이스북(매장)', 'https://business.facebook.com/'),
    # 4) 핀터레스트
    ('use_pin',     'pin',     'pinterest_cookies.json', '핀터레스트',    'https://kr.pinterest.com/pin-creation-tool/'),
]

_SHARED_COOKIE_MERGED_LABELS = {
    'naver_cookies.json': '네이버 (블로그·클립)',
    'facebook_cookies.json': '페이스북 (개인·매장)',
}


def resolve_auth_check_targets(payload, current_status=None, enabled_platforms=None):
    """통합 로그인 창에서 열 탭 목록(tabs_to_open)과 점검 대상 플랫폼 목록(requested_items)을 계산.
    - missing_only=True 시 사용자가 선택한 플랫폼(enabled_platforms) 중 미로그인('red')만 엄격 필터링
    - 쿠키 공유 계열(네이버 블로그+클립, 페이스북 개인+매장)은 단 1개 탭으로 중복 제거
    - 탭 순서는 항상 AUTH_CHECK_TARGETS 기준 유지"""
    req = payload if isinstance(payload, dict) else {}
    is_single = bool(req.get('single_platform'))
    missing_only = bool(req.get('missing_only')) and not is_single

    enabled_set = None
    if missing_only:
        raw_enabled = req.get('enabled_platforms') if enabled_platforms is None else enabled_platforms
        if isinstance(raw_enabled, (list, tuple, set)):
            enabled_set = {str(k).strip() for k in raw_enabled if str(k).strip()}
        else:
            prof = load_profile()
            prof_enabled = prof.get('enabled_platforms')
            if isinstance(prof_enabled, list):
                enabled_set = {str(k).strip() for k in prof_enabled if str(k).strip()}
        status_map = current_status if isinstance(current_status, dict) else get_setup_status(deep=False)
    else:
        status_map = {}

    requested_items = []
    for flag, key, cfile, label, url in AUTH_CHECK_TARGETS:
        if not req.get(flag):
            continue
        if missing_only:
            if enabled_set is not None and key not in enabled_set:
                continue
            if (status_map.get(key) or 'red') != 'red':
                continue
        requested_items.append((key, cfile, label, url))

    # 쿠키 공유 계열 중복 탭 제거 (네이버 블로그+클립 → 1탭, 페이스북 개인+매장 → 1탭)
    tabs_to_open = []
    seen_cfile_idx = {}
    for item in requested_items:
        key, cfile, label, url = item
        if cfile in seen_cfile_idx:
            prev_idx = seen_cfile_idx[cfile]
            prev_key, prev_cfile, prev_label, prev_url = tabs_to_open[prev_idx]
            merged_label = _SHARED_COOKIE_MERGED_LABELS.get(cfile, prev_label)
            tabs_to_open[prev_idx] = (prev_key, prev_cfile, merged_label, prev_url)
            continue
        seen_cfile_idx[cfile] = len(tabs_to_open)
        tabs_to_open.append(item)

    return tabs_to_open, requested_items


def worker_auth_check(payload):
    global ACTIVE_WORKER_THREAD
    with STATE_LOCK:
        STATE['busy'] = True
        STATE['stop_requested'] = False
        STATE['active_run_id'] += 1
        my_run_id = STATE['active_run_id']
        ACTIVE_WORKER_THREAD = threading.current_thread()

    selected, requested_items = resolve_auth_check_targets(payload)
    checked_cookie_files = {cfile for _, cfile, _, _ in requested_items}
    verified_platforms = set()
    active_login_page_cfiles = set()

    LOGIN_PAGE_REDIRECT_RULES = (
        ('naver_cookies.json', ('nid.naver.com/nidlogin', 'nidlogin.login'), ('naver.com',)),
        ('tiktok_cookies.json', ('tiktok.com/login',), ('tiktok.com',)),
        ('instagram_cookies.json', ('instagram.com/accounts/login',), ('instagram.com',)),
        ('pinterest_cookies.json', ('pinterest.com/login', '/login/'), ('pinterest.com',)),
        ('facebook_cookies.json', ('facebook.com/login', 'login.php', 'checkpoint', '/recover'), ('facebook.com',)),
    )

    def _inspect_live_tabs(ctx):
        """열려 있는 브라우저 탭을 정밀 점검:
        1) 페이스북 실제 대시보드 진입 여부 확인(단순 열었다 닫기 시 절대 초록불 불가)
        2) 각 플랫폼 탭이 로그인 페이지로 리다이렉트된 채 머물러 있는지 감지해 잔여 쿠키 가짜 초록불 차단"""
        if ctx is None:
            return
        try:
            from uploaders.facebook_uploader import is_facebook_logged_in
            pages = list(ctx.pages)
        except Exception:
            return
        seen_login_page = set()
        seen_non_login_page = set()
        fb_logged_in_any = False
        fb_seen_any = False
        fb_explicit_login_seen = False
        fb_has_auth_cookies = has_valid_facebook_auth_cookies(ctx)
        for pg in pages:
            try:
                if pg.is_closed():
                    continue
                cur_u = (pg.url or '').lower()
                if not cur_u or cur_u == 'about:blank':
                    continue
                if 'facebook.com' in cur_u:
                    fb_seen_any = True
                    is_explicit_fb_login = any(
                        mk in cur_u for mk in ('facebook.com/login', 'login.php', 'checkpoint', '/recover', 'two_step_verification')
                    )
                    if is_explicit_fb_login:
                        fb_explicit_login_seen = True
                    elif is_facebook_logged_in(pg) or fb_has_auth_cookies:
                        fb_logged_in_any = True
                    continue
                for cfile, login_markers, dom_markers in LOGIN_PAGE_REDIRECT_RULES:
                    if any(dm in cur_u for dm in dom_markers):
                        if any(mk in cur_u for mk in login_markers):
                            seen_login_page.add(cfile)
                        else:
                            seen_non_login_page.add(cfile)
                            if cfile == 'instagram_cookies.json':
                                new_ig = auto_extract_and_save_profile_id(pg, 'ig')
                                if new_ig:
                                    log(f'🔗 [프로필 자동 연동] 인스타그램 아이디(@{new_ig})를 자동 추출해 바로가기에 연결했습니다.')
            except Exception:
                pass
        if fb_has_auth_cookies and not fb_explicit_login_seen:
            fb_logged_in_any = True
        if fb_seen_any or fb_logged_in_any:
            if fb_logged_in_any:
                verified_platforms.add('facebook_cookies.json')
                seen_non_login_page.add('facebook_cookies.json')
            else:
                verified_platforms.discard('facebook_cookies.json')
                if fb_explicit_login_seen:
                    seen_login_page.add('facebook_cookies.json')
        active_login_page_cfiles.clear()
        for cf in seen_login_page:
            if cf not in seen_non_login_page:
                active_login_page_cfiles.add(cf)

    def _sync_and_enforce_checked(ctx):
        _inspect_live_tabs(ctx)
        sync_cookies_from_persistent_context(
            ctx,
            verified_platforms=verified_platforms,
            checked_filenames=checked_cookie_files,
        )
        # 현재 열린 탭이 로그인 화면으로 리다이렉트된 플랫폼은 잔여 쿠키 파일이 남지 않도록 즉시 제거
        for cf in list(active_login_page_cfiles):
            if cf in checked_cookie_files and cf not in verified_platforms:
                fp = get_auth_file(cf)
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

    if not selected:
        log('ℹ️ [Step 1] 로그인 점검할 플랫폼이 선택되지 않았습니다.')
        with STATE_LOCK:
            if STATE['active_run_id'] == my_run_id:
                STATE['busy'] = False
                STATE['stop_requested'] = False
            if ACTIVE_WORKER_THREAD is threading.current_thread():
                ACTIVE_WORKER_THREAD = None
        return

    context = None
    login_start_dt = datetime.datetime.now()
    login_exc = None
    login_tb = ''
    tab_diag_snapshots = {}

    def _get_open_pages(ctx):
        if ctx is None:
            return []
        try:
            return [pg for pg in list(ctx.pages) if not pg.is_closed()]
        except Exception:
            return []

    def _capture_open_tabs_diag(ctx):
        for pg in _get_open_pages(ctx):
            try:
                cur_u = (pg.url or '').strip()
                if not cur_u or cur_u == 'about:blank':
                    continue
                low_u = cur_u.lower()
                for cfile, _, dom_markers in LOGIN_PAGE_REDIRECT_RULES:
                    if any(dm in low_u for dm in dom_markers):
                        btns = []
                        try:
                            raw_btns = pg.evaluate(
                                """() => Array.from(document.querySelectorAll('button, [role="button"], a[href], input[type="submit"]'))
                                    .filter(el => el.offsetParent !== null)
                                    .map(el => (el.innerText || el.getAttribute('aria-label') || el.value || '').replace(/\\s+/g, ' ').trim())
                                    .filter(Boolean).slice(0, 30)"""
                            )
                            if isinstance(raw_btns, list):
                                btns = [str(b).strip() for b in raw_btns if str(b or '').strip()][:30]
                        except Exception:
                            btns = []
                        tab_diag_snapshots[cfile] = {
                            'url': cur_u,
                            'buttons': btns,
                            'captured_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        }
                        break
            except Exception:
                pass

    def _close_login_context_cleanly(ctx):
        """로그인 전용 Playwright 브라우저만 완전 종료 (macOS 빨간 X 닫힘 후 프로세스 잔류 방지 · 대시보드 브라우저는 무영향)"""
        if ctx is None:
            return
        done_evt = threading.Event()

        def _watchdog():
            if not done_evt.wait(3.5):
                _kill_automation_chrome()

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()
        try:
            ctx.close()
        except Exception:
            pass
        finally:
            done_evt.set()
        if sys.platform == 'darwin':
            # macOS에서 창 X 버튼으로 탭 0개가 된 뒤에도 Dock에 잔류할 수 있는 로그인 전용 크로미움 프로세스만 정리
            _kill_automation_chrome()

    try:
        from playwright.sync_api import sync_playwright

        _cleanup_paused_stop_browser_if_needed()
        names_str = ', '.join(label for _, _, label, _ in selected)
        log(f'⏳ [통합 로그인] 브라우저 창에 {len(selected)}개 플랫폼 탭을 여는 중... ({names_str})')

        with sync_playwright() as p:
            # 로그인 점검 시 비밀번호 노출 방지를 위해 record_video_dir 없이 영구 크롬 프로필로 실행
            context = launch_persistent_chromium(p, headless=False, viewport=None, record_video_dir=None)
            context_closed = [False]
            all_tabs_closed = [False]

            def _check_zero_tabs(*_):
                if not _get_open_pages(context):
                    all_tabs_closed[0] = True

            def _watch_page_close(pg):
                try:
                    pg.on('close', _check_zero_tabs)
                except Exception:
                    pass

            try:
                context.on('close', lambda *_: context_closed.__setitem__(0, True))
                context.on('page', _watch_page_close)
                for init_pg in list(context.pages):
                    _watch_page_close(init_pg)
            except Exception:
                pass

            first_page = None
            for idx, (key, cfile, label, url) in enumerate(selected):
                if _is_stop_requested(my_run_id) or context_closed[0] or (idx > 0 and all_tabs_closed[0]):
                    break
                try:
                    if idx == 0 and context.pages:
                        pg = context.pages[0]
                    else:
                        pg = context.new_page()
                    _watch_page_close(pg)
                    if first_page is None:
                        first_page = pg
                    pg.goto(url, wait_until='domcontentloaded', timeout=25000)
                except Exception as nav_err:
                    if not _is_stop_requested(my_run_id):
                        log(f'ℹ️ [{label}] 탭 로드 대기 중 ({nav_err}) — 열린 탭에서 계속 진행하실 수 있습니다.')

            if first_page is not None and not first_page.is_closed():
                try:
                    first_page.bring_to_front()
                except Exception:
                    pass

            if not _is_stop_requested(my_run_id) and not context_closed[0] and not all_tabs_closed[0]:
                log('🔑 [통합 로그인] 열린 브라우저 창에서 로그인을 완료한 뒤 창 [X]로 닫아주세요. 닫는 즉시 로그인 상태를 정밀 확인합니다!')
                if any(k in ('naver', 'clip') for k, _, _, _ in requested_items):
                    log("💡 [네이버 로그인 안내] 네이버 로그인 시 반드시 '로그인 상태 유지'를 체크해 주세요!")
                if any(k in ('fb', 'fb_page') for k, _, _, _ in requested_items):
                    log("💡 [페이스북 로그인 안내] 로그인 완료 후 페이스북 메인 피드 화면(facebook.com)을 확인한 뒤 창을 닫아 주세요!")

            fb_ac_hint_logged = False
            last_sync_ts = 0.0
            while not context_closed[0] and not all_tabs_closed[0]:
                if _is_stop_requested(my_run_id):
                    log('🛑 [작업 중지됨] 사용자 요청으로 통합 로그인 창을 종료합니다.')
                    break

                open_pages = _get_open_pages(context)
                if len(open_pages) == 0:
                    all_tabs_closed[0] = True
                    log('🔒 [통합 로그인] 로그인 창 닫힘 감지(열린 탭 0개) — 로그인 전용 브라우저를 종료하고 신호등을 정밀 확인합니다.')
                    break

                now_ts = time.time()
                if now_ts - last_sync_ts >= 2.0:
                    try:
                        _capture_open_tabs_diag(context)
                        _sync_and_enforce_checked(context)
                        if (not fb_ac_hint_logged) and ('facebook_cookies.json' in checked_cookie_files):
                            if any('accountscenter.facebook.com' in ((pg.url or '').lower()) for pg in open_pages):
                                if not has_valid_facebook_auth_cookies(context):
                                    fb_ac_hint_logged = True
                                    log("💡 [페이스북 로그인 안내] 현재 계정 센터(accountscenter.facebook.com) 화면에 머물러 있습니다. 페이스북 메인 피드(facebook.com) 화면을 확인한 뒤 창을 닫아 주세요!")
                    except Exception:
                        pass
                    last_sync_ts = now_ts

                # 250ms 간격으로 빠르게 이벤트 펌핑 (macOS 창 X 닫힘 즉시 감지)
                try:
                    open_pages[0].wait_for_timeout(250)
                except Exception:
                    if len(_get_open_pages(context)) == 0:
                        all_tabs_closed[0] = True
                        break
                    time.sleep(0.25)

            try:
                _capture_open_tabs_diag(context)
                _sync_and_enforce_checked(context)
            except Exception:
                pass
            _close_login_context_cleanly(context)
            context = None

        if not _is_stop_requested(my_run_id):
            st_after = get_setup_status(deep=True)
            summary = ', '.join(
                f"{label} {'🟢' if st_after.get(key) in ('green', 'yellow') else '🔴'}"
                for key, _, label, _ in requested_items
            )
            log(f'✅ [로그인 상태 정밀 확인 완료] {summary}')
            # 미로그인(🔴)이 남아 있는 경우 정밀 진단 리포트(blackbox/report_login.md) 자동 덮어쓰기 기록
            login_report_items = []
            has_unlogged = False
            for key, cfile, label, url in requested_items:
                is_ok = st_after.get(key) in ('green', 'yellow')
                snap = tab_diag_snapshots.get(cfile) or {}
                if not is_ok:
                    has_unlogged = True
                login_report_items.append({
                    'key': key,
                    'label': label,
                    'status': '성공' if is_ok else '실패',
                    'msg': '로그인 확인 완료' if is_ok else '미로그인 상태 (창 종료 후 인증 쿠키 미감지)',
                    'url': snap.get('url') or url,
                    'error': '' if is_ok else f'{label} 로그인 미완료 또는 세션 인증 실패',
                    'traceback': '' if is_ok else f'Login verification returned red for {key} ({cfile})',
                    'buttons': [] if is_ok else (snap.get('buttons') or []),
                    'captured_at': snap.get('captured_at') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
            if has_unlogged:
                rp = write_run_report(
                    login_report_items,
                    mode_label='통합 로그인 점검',
                    video_name='',
                    start_dt=login_start_dt,
                    end_dt=datetime.datetime.now(),
                    stopped_by_user=False,
                    report_kind='login',
                )
                if rp:
                    log(f'📝 [로그인 진단 리포트 저장] blackbox/{os.path.basename(rp)}')
    except Exception as e:
        login_exc = e
        login_tb = traceback.format_exc()
        if not _is_stop_requested(my_run_id):
            log(f'⚠️ 통합 로그인 중 오류: {e}')
            rp = write_run_report(
                [{
                    'key': 'login',
                    'label': '통합 로그인',
                    'status': '실패',
                    'msg': str(e)[:80],
                    'url': '',
                    'error': f'{type(e).__name__}: {e}',
                    'traceback': login_tb,
                    'buttons': [],
                    'captured_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                }],
                mode_label='통합 로그인 점검',
                video_name='',
                start_dt=login_start_dt,
                end_dt=datetime.datetime.now(),
                stopped_by_user=False,
                report_kind='login',
            )
            if rp:
                log(f'📝 [로그인 오류 리포트 저장] blackbox/{os.path.basename(rp)}')
    finally:
        if context is not None:
            try:
                _sync_and_enforce_checked(context)
            except Exception:
                pass
            _close_login_context_cleanly(context)
        cleanup_chrome_profile_locks()
        invalidate_deep_status_cache()
        with STATE_LOCK:
            STATE['auth_check_seq'] = int(STATE.get('auth_check_seq', 0)) + 1
            if STATE['active_run_id'] == my_run_id:
                STATE['busy'] = False
                STATE['stop_requested'] = False
            elif not STATE['busy']:
                STATE['stop_requested'] = False
            if ACTIVE_WORKER_THREAD is threading.current_thread():
                ACTIVE_WORKER_THREAD = None
        clean_and_unify_blackbox_files()

def split_tags(raw_str):
    cleaned = []
    seen = set()
    for part in re.split(r'[,\n]+', str(raw_str or '')):
        tok = _sanitize_tag_token(part)
        if tok and tok.lower() not in seen:
            seen.add(tok.lower())
            cleaned.append(tok)
    return cleaned

def _is_stop_requested(run_id=None):
    with STATE_LOCK:
        if STATE.get('stop_requested'):
            return True
        if run_id is not None and STATE.get('active_run_id') != run_id:
            return True
        return False


def _was_platform_skipped(run_id, platform_key):
    with STATE_LOCK:
        if run_id is not None and STATE.get('active_run_id') != run_id:
            return False
        return str(STATE.get('skip_platform_key') or '') == str(platform_key or '')


def _is_stop_or_skip_requested(run_id, platform_key):
    return _is_stop_requested(run_id) or _was_platform_skipped(run_id, platform_key)


def _set_last_result(key, ok, msg, run_id=None, history_id='', history_rec=None, test_mode=False):
    with STATE_LOCK:
        if run_id is not None and STATE.get('active_run_id') != run_id:
            return
        STATE['last_results'][key] = {'ok': bool(ok), 'msg': msg}
    if ok and not test_mode:
        updated = record_history_uploaded_platform(history_id, key, fallback_rec=history_rec)
        with STATE_LOCK:
            if run_id is None or STATE.get('active_run_id') == run_id:
                if updated is not None:
                    STATE['active_uploaded_platforms'] = list(updated)
                else:
                    cur = list(STATE.get('active_uploaded_platforms') or [])
                    norm_k = _normalize_platform_key(key)
                    if norm_k and norm_k not in cur:
                        cur.append(norm_k)
                    STATE['active_uploaded_platforms'] = cur

def _create_test_sample_video(run_id=0):
    """[테스트 모드] 영상 미첨부 시 내장 FFmpeg(imageio-ffmpeg 포함)로 1초 초경량 세로(9:16, 720x1280)
    단색 샘플 영상(test_sample.mp4)을 임시 폴더에 자동 생성해 (sample_dir, sample_path) 반환."""
    os.makedirs(THUMB_DIR, exist_ok=True)
    sample_dir = os.path.join(THUMB_DIR, f'_test_sample_{run_id}_{int(time.time() * 1000)}')
    os.makedirs(sample_dir, exist_ok=True)
    sample_path = os.path.join(sample_dir, 'test_sample.mp4')
    ffmpeg_bin = get_ffmpeg_bin()
    cmd = [
        ffmpeg_bin, '-y',
        '-f', 'lavfi', '-i', 'color=c=#1e293b:s=720x1280:d=1:r=24',
        '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
        '-t', '1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast',
        '-c:a', 'aac', '-shortest', '-movflags', '+faststart',
        sample_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=25,
            **_win_no_window_kwargs(),
        )
        if proc.returncode == 0 and os.path.isfile(sample_path) and os.path.getsize(sample_path) > 512:
            return sample_dir, sample_path
    except Exception:
        pass

    # 무음 트랙 포함 인코딩이 실패한 환경용 비디오 단독 경량 폴백
    fallback_cmd = [
        ffmpeg_bin, '-y',
        '-f', 'lavfi', '-i', 'color=c=#1e293b:s=720x1280:d=1:r=24',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast',
        '-movflags', '+faststart',
        sample_path,
    ]
    try:
        proc2 = subprocess.run(
            fallback_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=25,
            **_win_no_window_kwargs(),
        )
        if proc2.returncode == 0 and os.path.isfile(sample_path) and os.path.getsize(sample_path) > 512:
            return sample_dir, sample_path
    except Exception:
        pass

    _force_rmtree(sample_dir)
    return '', ''


def worker_publish(payload):
    global ACTIVE_WORKER_THREAD
    test_mode = bool(payload.get('test_mode', False))
    save_history_flag = (not test_mode) and bool(payload.get('_save_history', payload.get('save_history', False)))
    history_id = '' if (test_mode or not save_history_flag) else str(payload.get('_history_id') or payload.get('history_id') or '').strip()
    history_rec = None if (test_mode or not save_history_flag) else (payload.get('_history_rec') if isinstance(payload.get('_history_rec'), dict) else None)
    existing_uploaded = [] if (test_mode or not save_history_flag) else get_history_uploaded_platforms(history_id, fallback_rec=history_rec)
    run_start_dt = datetime.datetime.now()
    bb_session_dir = ''

    # 직전 강제 중지로 열어둔 자동화 브라우저가 있으면 프로필 잠금 충돌 방지를 위해 먼저 안전 정리
    _cleanup_paused_stop_browser_if_needed()

    with STATE_LOCK:
        STATE['busy'] = True
        STATE['stop_requested'] = False
        STATE['skip_platform_key'] = ''
        STATE['current_platform_key'] = ''
        STATE['current_platform_label'] = ''
        STATE['last_results'] = {}
        STATE['active_history_id'] = history_id
        STATE['active_uploaded_platforms'] = list(existing_uploaded)
        STATE['active_run_id'] += 1
        my_run_id = STATE['active_run_id']
        STATE['is_test_mode'] = test_mode
        STATE['test_merged_done'] = False
        STATE['force_stop_merging'] = False
        if test_mode:
            bb_session_dir = os.path.join(BLACKBOX_TMP_DIR, f'_test_session_{my_run_id}_{int(time.time())}')
        else:
            bb_session_dir = os.path.join(BLACKBOX_TMP_DIR, f'_upload_session_{my_run_id}_{int(time.time())}')
        STATE['test_session_dir'] = bb_session_dir
        STATE['test_start_dt'] = run_start_dt
        ACTIVE_WORKER_THREAD = threading.current_thread()
        state_vn = STATE['video_name']
        state_vp = STATE['video_path']

    if test_mode:
        set_blackbox_session_dir(bb_session_dir, session_kind='test')
        log('🧪 [테스트 모드 시작] 각 플랫폼 게시 직전까지 시뮬레이션하며 블랙박스 영상을 수집합니다.')
    else:
        set_blackbox_session_dir(bb_session_dir, session_kind='upload')

    clear_platform_diagnostics(None)
    naver_completed = False
    prof = load_profile()
    vn = payload.get('video_name') or state_vn
    video_path = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
    auto_sample_dir = ''
    if test_mode and (not video_path or not os.path.isfile(video_path)):
        auto_sample_dir, sample_vp = _create_test_sample_video(run_id=my_run_id)
        if sample_vp and os.path.isfile(sample_vp):
            video_path = sample_vp
            vn = os.path.basename(sample_vp)
            log('🎬 [테스트 모드] 영상 미첨부로 1초 테스트용 샘플 영상을 자동 생성했습니다. (test_sample.mp4 · 720x1280)')
        else:
            log('⚠️ [테스트 모드] 1초 테스트용 샘플 영상 자동 생성에 실패했습니다. (FFmpeg/imageio-ffmpeg 설치 확인 필요)')
    default_place = get_place_search_keyword(prof)
    if test_mode and not default_place:
        default_place = DEMO_FALLBACK_PROFILE['salon_name']
    mode_label = '테스트 모드' if test_mode else '실업로드'

    def _resolve_valid_cover_image(*candidates):
        for raw_p in candidates:
            p = str(raw_p or '').strip()
            if not p or not os.path.isfile(p):
                continue
            if os.path.splitext(p)[1].lower() in ('.jpg', '.jpeg', '.png'):
                return p
        return None

    shared_cover_image = _resolve_valid_cover_image(
        payload.get('cover_image'),
        payload.get('thumbnail_path'),
        payload.get('tt_cover_image'),
        payload.get('naver_rep_image'),
    )

    # 표준 실행 순서 (AI_RULES.md 기준 · 테스트 모드/실업로드 모두 강제 고정)
    platform_order = [
        ('yt', '유튜브 쇼츠', '유튜브', 'use_yt'),
        ('naver', '네이버 블로그', '네이버블로그', 'use_naver'),
        ('clip', '네이버 클립', '네이버클립', 'use_clip'),
        ('ig', '인스타그램', '인스타그램', 'use_ig'),
        ('tt', '틱톡', '틱톡', 'use_tt'),
        ('fb', '페이스북(개인)', '페이스북개인', 'use_fb'),
        ('fb_page', '페이스북(매장)', '페이스북매장', 'use_fb_page'),
        ('pin', '핀터레스트', '핀터레스트', 'use_pin'),
    ]
    selected_platforms = [(k, lbl, bbn) for k, lbl, bbn, flag in platform_order if payload.get(flag)]
    ordered_platform_keys = [k for k, _, _ in selected_platforms]
    if selected_platforms:
        log('📋 [표준 실행 순서] ' + ' → '.join(lbl for _, lbl, _ in selected_platforms))
    platform_records = {}
    platform_last_msgs = {}
    top_level_exc = None
    top_level_tb = ''
    run_ctx = {
        'run_id': my_run_id,
        'mode_label': mode_label,
        'report_kind': ('test' if test_mode else 'upload'),
        'video_name': vn,
        'start_dt': run_start_dt,
        'selected_platforms': selected_platforms,
        'platform_records': platform_records,
    }
    with STATE_LOCK:
        if STATE['active_run_id'] == my_run_id:
            STATE['active_run_ctx'] = run_ctx

    def mark_res(k, ok, msg):
        platform_last_msgs[k] = msg
        _set_last_result(
            k, ok, msg, my_run_id, history_id=history_id, history_rec=history_rec, test_mode=test_mode
        )

    def _begin_platform(pkey, label, bb_name):
        with STATE_LOCK:
            if STATE['active_run_id'] == my_run_id:
                STATE['current_platform_key'] = pkey
                STATE['current_platform_label'] = label
                STATE['skip_platform_key'] = ''
        clear_platform_diagnostics(bb_name)
        set_active_diagnostics_platform(bb_name)
        act_word = '테스트' if test_mode else '업로드'
        log(f'현재 {label} {act_word}를 시작합니다...')

    def _finish_platform(pkey, label, bb_name, ok, exc_obj=None, err_tb=''):
        skipped = _was_platform_skipped(my_run_id, pkey)
        stopped_all = _is_stop_requested(my_run_id)
        with STATE_LOCK:
            if STATE['active_run_id'] == my_run_id:
                STATE['current_platform_key'] = ''
                STATE['current_platform_label'] = ''
                if skipped:
                    STATE['skip_platform_key'] = ''
        set_active_diagnostics_platform('')

        diag = get_platform_diagnostics(bb_name)
        now_cap = diag.get('captured_at') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        reached_url = diag.get('url', '') or ('(YouTube Data API)' if pkey == 'yt' else '')
        final_msg = str(platform_last_msgs.get(pkey) or '').strip()

        if skipped:
            mark_res(pkey, False, '건너뜀')
            log(f'⏭️ [{label}] 사용자 요청으로 건너뛰고 다음 플랫폼으로 이동합니다.')
            platform_records[pkey] = {
                'key': pkey,
                'label': label,
                'status': '건너뜀',
                'msg': '건너뜀',
                'url': reached_url,
                'error': diag.get('error') or '사용자가 [이 플랫폼만 건너뛰기]를 클릭해 건너뜀',
                'traceback': diag.get('traceback') or err_tb or 'Skipped by user request',
                'buttons': diag.get('buttons') or [],
                'captured_at': now_cap,
            }
            cleanup_chrome_profile_locks()
            return

        if ok:
            aim_msg = str(diag.get('aim_result') or '').strip() if test_mode else ''
            if aim_msg:
                final_msg = aim_msg
                mark_res(pkey, True, aim_msg)
            platform_records[pkey] = {
                'key': pkey,
                'label': label,
                'status': '성공',
                'msg': final_msg or ('테스트 통과' if test_mode else '완료'),
                'url': reached_url,
                'error': '',
                'traceback': '',
                'buttons': [],
                'captured_at': now_cap,
            }
            return

        if stopped_all:
            err_summary = diag.get('error') or (f'{type(exc_obj).__name__}: {exc_obj}' if exc_obj else '사용자 강제 중지 요청으로 중단됨 (브라우저 화면 유지)')
            tb_final = diag.get('traceback') or err_tb or err_summary
            platform_records[pkey] = {
                'key': pkey,
                'label': label,
                'status': '강제 중지',
                'msg': '강제 중지 (브라우저 화면 유지)',
                'url': reached_url,
                'error': err_summary,
                'traceback': tb_final,
                'buttons': diag.get('buttons') or [],
                'captured_at': now_cap,
            }
            return

        err_summary = diag.get('error') or (f'{type(exc_obj).__name__}: {exc_obj}' if exc_obj else f'{label} 업로드/게시 실패')
        tb_final = diag.get('traceback') or err_tb or err_summary
        platform_records[pkey] = {
            'key': pkey,
            'label': label,
            'status': '실패',
            'msg': final_msg or err_summary[:80],
            'url': reached_url,
            'error': err_summary,
            'traceback': tb_final,
            'buttons': diag.get('buttons') or [],
            'captured_at': now_cap,
        }

    try:
        for _ in (0,):
            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_yt'):
                _begin_platform('yt', '유튜브 쇼츠', '유튜브')
                yt_ok = False
                yt_exc = None
                yt_tb = ''
                try:
                    if test_mode:
                        yt_check = validate_youtube_oauth_token(attempt_refresh=True)
                        if yt_check['ok']:
                            yt_pass_msg = f"API 테스트 통과 ({yt_check['reason']} · 버튼 조준 대상 아님)"
                            log(f'🧪 [테스트 모드 - 유튜브 쇼츠] {yt_pass_msg}')
                            yt_ok = True
                            mark_res('yt', True, yt_pass_msg)
                        else:
                            yt_fail_msg = f"미로그인/토큰 검증 실패 — {yt_check['reason']}"
                            log(f'❌ [테스트 모드 - 유튜브 쇼츠] {yt_fail_msg}')
                            raise RuntimeError(yt_fail_msg)
                    else:
                        log('🔴 [유튜브 쇼츠] 업로드 시작...')
                        yt_url = upload_to_youtube(
                            normalize_branch_name(payload.get('yt_title', '')),
                            normalize_branch_name(payload.get('yt_body', '')),
                            split_tags(payload.get('yt_tags')),
                            video_path,
                            'public',
                            thumbnail_path=shared_cover_image,
                        )
                        if yt_url:
                            yt_ok = True
                            record_daily_usage('yt')
                            mark_res('yt', True, '완료')
                            log('✅ [유튜브 쇼츠] 완료!')
                        else:
                            mark_res('yt', False, '업로드 실패')
                except Exception as e:
                    yt_exc = e
                    yt_tb = traceback.format_exc()
                    mark_res('yt', False, str(e)[:80])
                    if not _is_stop_or_skip_requested(my_run_id, 'yt'):
                        log(f'⚠️ [유튜브 쇼츠] 오류: {e}')
                _finish_platform('yt', '유튜브 쇼츠', '유튜브', yt_ok, exc_obj=yt_exc, err_tb=yt_tb)

            if _is_stop_requested(my_run_id):
                break

            def _is_shared_ctx_alive(ctx_obj):
                if ctx_obj is None:
                    return False
                try:
                    pages = list(ctx_obj.pages)
                    return any(not pg.is_closed() for pg in pages) if pages else True
                except Exception:
                    return False

            def _run_naver_blog_step(shared_ctx=None, keep_open=False, shared_rec_dir=None):
                nonlocal naver_completed
                _begin_platform('naver', '네이버 블로그', '네이버블로그')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'naver')
                b_ok = False
                b_exc = None
                b_tb = ''
                try:
                    cat = (payload.get('naver_category') or '').strip()
                    cat_log = cat or '기본 게시판'
                    n_tags = split_tags(payload.get('naver_tags'))
                    rep_img = (payload.get('naver_rep_image') or '').strip()
                    sel_imgs = [p for p in (payload.get('naver_selected_images') or []) if p]
                    gif_cnt = sum(1 for p in sel_imgs if p.lower().endswith('.gif'))
                    img_cnt = len(sel_imgs) - gif_cnt
                    rep_desc = '⭐ 대표 1 · ' if (rep_img and os.path.exists(rep_img)) else ''
                    media_desc = f"{rep_desc}사진 {img_cnt}장 · GIF {gif_cnt}개" if gif_cnt else f"{rep_desc}사진 {img_cnt}장"
                    log(f'🟢 [네이버 블로그 - {cat_log}] 발행 시작 (선택 미디어 {media_desc})...')
                    log(f"🏷️ [네이버 블로그] 등록할 태그({min(len(n_tags), 10)}개): " + ", ".join([f"#{t.replace('#', '')}" for t in n_tags[:10]]))
                    b_ok = bool(upload_to_naver_blog(
                        normalize_branch_name(payload.get('naver_title', '')),
                        normalize_branch_name(payload.get('naver_body', '')),
                        n_tags,
                        video_path,
                        cat,
                        normalize_branch_name(payload.get('naver_place') or default_place),
                        selected_images=payload.get('naver_selected_images') or [],
                        rep_image=rep_img or None,
                        stop_checker=p_stop,
                        test_mode=test_mode,
                        shared_context=shared_ctx,
                        keep_context_open=keep_open,
                        shared_record_dir=shared_rec_dir,
                    ))
                    if b_ok:
                        if not test_mode:
                            naver_completed = True
                            record_daily_usage('naver')
                        mark_res('naver', True, '테스트 통과' if test_mode else '발행 완료')
                    elif not _was_platform_skipped(my_run_id, 'naver'):
                        mark_res('naver', False, '발행 중단/실패')
                    if not p_stop():
                        log(f'✅ [네이버 블로그 - {cat_log}] {"테스트 완료!" if test_mode else "발행 완료!"}' if b_ok else f'⚠️ [네이버 블로그 - {cat_log}] 발행 중단/실패')
                except Exception as e:
                    b_exc = e
                    b_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'naver'):
                        mark_res('naver', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [네이버 블로그] 오류: {e}')
                _finish_platform('naver', '네이버 블로그', '네이버블로그', b_ok, exc_obj=b_exc, err_tb=b_tb)

            def _run_naver_clip_step(shared_ctx=None, shared_rec_dir=None):
                _begin_platform('clip', '네이버 클립', '네이버클립')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'clip')
                c_ok = False
                c_exc = None
                c_tb = ''
                try:
                    from uploaders.naver_clip_uploader import upload_to_naver_clip
                    log('🟢 [네이버 클립] 게시 시작...')
                    c_ok = bool(upload_to_naver_clip(
                        normalize_branch_name(payload.get('clip_title', '')),
                        normalize_branch_name(payload.get('clip_body', '')),
                        split_tags(payload.get('clip_tags')),
                        video_path,
                        normalize_branch_name(payload.get('clip_place') or default_place),
                        stop_checker=p_stop,
                        test_mode=test_mode,
                        shared_context=shared_ctx,
                        shared_record_dir=shared_rec_dir,
                    ))
                    if c_ok:
                        if not test_mode:
                            record_daily_usage('clip')
                        mark_res('clip', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'clip'):
                        mark_res('clip', False, '게시 실패')
                    if not p_stop():
                        log('✅ [네이버 클립] ' + ('테스트 완료!' if test_mode else '게시 완료!') if c_ok else '⚠️ [네이버 클립] 게시 실패')
                except Exception as e:
                    c_exc = e
                    c_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'clip'):
                        mark_res('clip', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [네이버 클립] 오류: {e}')
                _finish_platform('clip', '네이버 클립', '네이버클립', c_ok, exc_obj=c_exc, err_tb=c_tb)

            # 네이버 블로그 + 네이버 클립 연속 실행 시 표준 순서(naver → clip)로 동일 크롬 세션(새 탭 전환) 재사용
            if 'naver' in ordered_platform_keys and 'clip' in ordered_platform_keys:
                shared_naver_rec_dir = create_blackbox_record_dir('네이버블로그클립', mode='upload')
                shared_naver_ctx = None
                with open_playwright_session() as naver_pw:
                    try:
                        log('🔗 [네이버 세션 공유] 표준 순서(네이버 블로그 → 네이버 클립)로 단일 브라우저 세션을 통합 진행합니다.')
                        shared_naver_ctx = launch_persistent_chromium(
                            naver_pw,
                            headless=False,
                            viewport={'width': 1360, 'height': 920},
                            record_video_dir=shared_naver_rec_dir,
                            record_video_size={'width': 1360, 'height': 920},
                        )
                        _run_naver_blog_step(
                            shared_ctx=shared_naver_ctx,
                            keep_open=True,
                            shared_rec_dir=shared_naver_rec_dir,
                        )
                        if not _is_stop_requested(my_run_id):
                            time.sleep(1.2 if sys.platform == 'win32' else 0.8)
                            if _is_shared_ctx_alive(shared_naver_ctx):
                                _run_naver_clip_step(
                                    shared_ctx=shared_naver_ctx,
                                    shared_rec_dir=shared_naver_rec_dir,
                                )
                            else:
                                # 블로그 단계 건너뛰기/창 닫힘 등으로 공유 컨텍스트가 닫힌 경우 클립 단독 세션으로 안전 폴백
                                log('ℹ️ [네이버 세션 공유] 공유 브라우저가 닫혀 네이버 클립을 독립 세션으로 안전 전환합니다.')
                                _run_naver_clip_step(shared_ctx=None, shared_rec_dir=None)
                    finally:
                        last_pg = None
                        try:
                            if _is_shared_ctx_alive(shared_naver_ctx) and shared_naver_ctx.pages:
                                open_pgs = [pg for pg in list(shared_naver_ctx.pages) if not pg.is_closed()]
                                last_pg = open_pgs[-1] if open_pgs else shared_naver_ctx.pages[-1]
                        except Exception:
                            last_pg = None
                        finalize_playwright_session(
                            '네이버블로그클립',
                            mode='upload',
                            page=last_pg,
                            context=shared_naver_ctx,
                            record_dir=shared_naver_rec_dir,
                            platform_label='네이버 블로그·클립',
                        )
            else:
                if 'naver' in ordered_platform_keys:
                    _run_naver_blog_step(shared_ctx=None, keep_open=False, shared_rec_dir=None)
                if _is_stop_requested(my_run_id):
                    break
                if 'clip' in ordered_platform_keys:
                    _run_naver_clip_step(shared_ctx=None, shared_rec_dir=None)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_ig'):
                _begin_platform('ig', '인스타그램', '인스타그램')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'ig')
                ig_ok = False
                ig_exc = None
                ig_tb = ''
                try:
                    log('🟣 [인스타그램 릴스] 게시 시작...')
                    ig_place = normalize_branch_name(payload.get('ig_location') or default_place)
                    ig_ok = bool(upload_to_instagram(normalize_branch_name(payload.get('ig_body', '')), split_tags(payload.get('ig_tags')), video_path, is_draft=False, place_name=ig_place, stop_checker=p_stop, test_mode=test_mode))
                    if ig_ok:
                        if not test_mode:
                            record_daily_usage('ig')
                        mark_res('ig', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'ig'):
                        mark_res('ig', False, '게시 실패')
                    if not p_stop():
                        log('✅ [인스타그램 릴스] ' + ('테스트 완료!' if test_mode else '게시 완료!') if ig_ok else '⚠️ [인스타그램 릴스] 게시 실패')
                except Exception as e:
                    ig_exc = e
                    ig_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'ig'):
                        mark_res('ig', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [인스타그램] 오류: {e}')
                _finish_platform('ig', '인스타그램', '인스타그램', ig_ok, exc_obj=ig_exc, err_tb=ig_tb)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_tt'):
                _begin_platform('tt', '틱톡', '틱톡')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'tt')
                t_ok = False
                t_exc = None
                t_tb = ''
                try:
                    log('⚫ [틱톡] 게시 시작...')
                    tt_place = normalize_branch_name(payload.get('tt_location') or default_place)
                    tt_cover_img = _resolve_valid_cover_image(payload.get('tt_cover_image'), shared_cover_image)
                    t_ok = bool(upload_to_tiktok(normalize_branch_name(payload.get('tt_title', '')), normalize_branch_name(payload.get('tt_body', '')), split_tags(payload.get('tt_tags')), video_path, place_name=tt_place, cover_image=tt_cover_img, draft=False, stop_checker=p_stop, test_mode=test_mode))
                    if t_ok:
                        if not test_mode:
                            record_daily_usage('tt')
                        mark_res('tt', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'tt'):
                        mark_res('tt', False, '취소/실패')
                    if not p_stop():
                        log('✅ [틱톡] ' + ('테스트 완료!' if test_mode else '게시 완료!') if t_ok else '⚠️ [틱톡] 취소됨')
                except Exception as e:
                    t_exc = e
                    t_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'tt'):
                        mark_res('tt', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [틱톡] 오류: {e}')
                _finish_platform('tt', '틱톡', '틱톡', t_ok, exc_obj=t_exc, err_tb=t_tb)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_fb'):
                _begin_platform('fb', '페이스북(개인)', '페이스북개인')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'fb')
                fb_ok = False
                fb_exc = None
                fb_tb = ''
                try:
                    from uploaders.facebook_uploader import upload_to_facebook_personal
                    log('📘 [페이스북(개인)] 릴스 게시 시작...')
                    fb_ok = bool(upload_to_facebook_personal(body=normalize_branch_name(payload.get('fb_body', '')), tags=split_tags(payload.get('fb_tags')), video_path=video_path, stop_checker=p_stop, test_mode=test_mode))
                    if fb_ok:
                        if not test_mode:
                            record_daily_usage('fb')
                        mark_res('fb', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'fb'):
                        mark_res('fb', False, '게시 실패')
                    if not p_stop():
                        log('✅ [페이스북(개인)] ' + ('테스트 완료!' if test_mode else '게시 완료!') if fb_ok else '⚠️ [페이스북(개인)] 게시 실패')
                except Exception as e:
                    fb_exc = e
                    fb_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'fb'):
                        mark_res('fb', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [페이스북(개인)] 오류: {e}')
                _finish_platform('fb', '페이스북(개인)', '페이스북개인', fb_ok, exc_obj=fb_exc, err_tb=fb_tb)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_fb_page'):
                _begin_platform('fb_page', '페이스북(매장)', '페이스북매장')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'fb_page')
                fbp_ok = False
                fbp_exc = None
                fbp_tb = ''
                try:
                    from uploaders.facebook_uploader import upload_to_facebook_page
                    fb_page_name = normalize_branch_name(payload.get('fb_page_name') or get_salon_name(prof, fallback=''))
                    log(f'🏪 [페이스북(매장)] 메타 비즈니스 스위트 릴스 게시 시작 ({fb_page_name or "기본 페이지"})...')
                    fbp_ok = bool(upload_to_facebook_page(body=normalize_branch_name(payload.get('fb_page_body', '')), tags=split_tags(payload.get('fb_page_tags')), video_path=video_path, page_name=fb_page_name, stop_checker=p_stop, test_mode=test_mode))
                    if fbp_ok:
                        if not test_mode:
                            record_daily_usage('fb_page')
                        mark_res('fb_page', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'fb_page'):
                        mark_res('fb_page', False, '게시 실패')
                    if not p_stop():
                        log('✅ [페이스북(매장)] ' + ('테스트 완료!' if test_mode else '게시 완료!') if fbp_ok else '⚠️ [페이스북(매장)] 게시 실패')
                except Exception as e:
                    fbp_exc = e
                    fbp_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'fb_page'):
                        mark_res('fb_page', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [페이스북(매장)] 오류: {e}')
                _finish_platform('fb_page', '페이스북(매장)', '페이스북매장', fbp_ok, exc_obj=fbp_exc, err_tb=fbp_tb)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_pin'):
                _begin_platform('pin', '핀터레스트', '핀터레스트')
                p_stop = lambda: _is_stop_or_skip_requested(my_run_id, 'pin')
                pin_ok = False
                pin_exc = None
                pin_tb = ''
                try:
                    from uploaders.pinterest_uploader import upload_to_pinterest
                    log('🔴 [핀터레스트] 게시 시작...')
                    pin_link = payload.get('pin_dest_url') if payload.get('pin_dest_url') is not None else prof.get('booking_url', '')
                    pin_board = payload.get('pin_board') if payload.get('pin_board') is not None else prof.get('pinterest_board', '')
                    pin_ok = bool(upload_to_pinterest(title=normalize_branch_name(payload.get('pin_title', '')), description=normalize_branch_name(payload.get('pin_body', '')), tags=split_tags(payload.get('pin_tags')), video_path=video_path, link=pin_link, board_name=pin_board, stop_checker=p_stop, test_mode=test_mode))
                    if pin_ok:
                        if not test_mode:
                            record_daily_usage('pin')
                        mark_res('pin', True, '테스트 통과' if test_mode else '게시 완료')
                    elif not _was_platform_skipped(my_run_id, 'pin'):
                        mark_res('pin', False, '중단/실패')
                    if not p_stop():
                        log('✅ [핀터레스트] ' + ('테스트 완료!' if test_mode else '게시 완료!') if pin_ok else '⚠️ [핀터레스트] 중단됨')
                except Exception as e:
                    pin_exc = e
                    pin_tb = traceback.format_exc()
                    if not _was_platform_skipped(my_run_id, 'pin'):
                        mark_res('pin', False, str(e)[:80])
                    if not p_stop():
                        log(f'⚠️ [핀터레스트] 오류: {e}')
                _finish_platform('pin', '핀터레스트', '핀터레스트', pin_ok, exc_obj=pin_exc, err_tb=pin_tb)

        if _is_stop_requested(my_run_id):
            log('🛑 [작업 중지 완료] 사용자 요청으로 남은 업로드를 모두 취소했습니다.')
        else:
            if save_history_flag and video_path and os.path.exists(video_path):
                shutil.copy2(video_path, os.path.join(DONE_DIR, os.path.basename(video_path)))
                log('📁 완료된 영상 done 폴더 백업 완료!')
            with STATE_LOCK:
                run_results = dict(STATE.get('last_results', {})) if STATE['active_run_id'] == my_run_id else {}
            had_failed = any(not bool(v.get('ok')) for v in run_results.values())
            if had_failed:
                log('⚠️ 일부 플랫폼 작업에서 오류 또는 건너뜀이 발생했습니다.')
                log('💡 플랫폼 화면이 바뀌었나요? 상단 [🔄 업데이트 확인] 또는 하단 [💬 질문·소통방]을 확인하세요.')
            else:
                log('🎉 선택한 플랫폼 테스트가 모두 완료되었습니다!' if test_mode else '🎉 선택한 플랫폼 작업이 모두 완료되었습니다!')
    except Exception as e:
        top_level_exc = e
        top_level_tb = traceback.format_exc()
        if not _is_stop_requested(my_run_id):
            log(f'⚠️ 업로드 중 오류 발생: {e}')
            log('💡 플랫폼 화면이 바뀌었나요? 상단 [🔄 업데이트 확인] 또는 하단 [💬 질문·소통방]을 확인하세요.')
    finally:
        was_stopped = _is_stop_requested(my_run_id)
        set_active_diagnostics_platform('')
        with STATE_LOCK:
            force_stop_merging = bool(STATE.get('force_stop_merging'))
            is_still_latest_ctx = (STATE.get('active_run_ctx') is run_ctx)
            cur_active_pkey = str(STATE.get('current_platform_key') or '').strip()
            if STATE['active_run_id'] == my_run_id:
                STATE['busy'] = False
                STATE['stop_requested'] = False
                STATE['current_platform_key'] = ''
                STATE['current_platform_label'] = ''
                STATE['skip_platform_key'] = ''
            elif not STATE['busy']:
                STATE['stop_requested'] = False
            if ACTIVE_WORKER_THREAD is threading.current_thread():
                ACTIVE_WORKER_THREAD = None

        # 매 실행 종료 시(성공·실패·건너뜀·강제 중지·테스트 모드 포함) 항상 blackbox/report_test.md 또는 blackbox/report_upload.md에 덮어쓰기 저장
        # (이미 새 업로드가 시작되어 다른 run_ctx로 교체된 늦은 스레드는 새 리포트를 덮어쓰지 않음)
        if is_still_latest_ctx:
            saved_report_path = _build_and_write_run_report_snapshot(
                run_ctx,
                stopped_by_user=was_stopped,
                active_platform_key=cur_active_pkey,
                top_level_exc=top_level_exc,
                top_level_tb=top_level_tb,
            )
            if saved_report_path:
                log(f'📝 [실행 리포트 저장] blackbox/{os.path.basename(saved_report_path)}에 이번 작업 결과를 기록했습니다.')

        # 강제 중지 경로가 아닌 일반 완료 시 여기서 FFmpeg 단일 MP4 합본 저장
        # (강제 중지 시에는 force_stop_active_task가 완료 로그 직후 합본 저장)
        if not force_stop_merging and is_still_latest_ctx:
            _finalize_blackbox_session_merge_once()
            clean_and_unify_blackbox_files(clear_thumbs=(False if test_mode else naver_completed))

        # 테스트 모드용 임시 1초 샘플 영상 폴더 정리
        if auto_sample_dir and os.path.isdir(auto_sample_dir):
            _force_rmtree(auto_sample_dir)

def open_blackbox_folder():
    """blackbox/ 폴더가 없으면 생성한 뒤 OS 기본 탐색기(macOS open, Windows explorer)로 열기"""
    os.makedirs(BLACKBOX_DIR, exist_ok=True)
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', BLACKBOX_DIR])
        elif sys.platform == 'win32':
            try:
                os.startfile(BLACKBOX_DIR)
            except Exception:
                subprocess.Popen(['explorer', BLACKBOX_DIR])
        else:
            subprocess.Popen(['xdg-open', BLACKBOX_DIR])
    except Exception as e:
        return {'ok': False, 'msg': f'오류 폴더를 열지 못했습니다: {e}'}
    log('📂 [오류 폴더 열기] 프로그램 폴더 내 blackbox 폴더의 report_upload.md / report_test.md와 영상(upload.mp4 / test.mp4)을 소통방에 첨부해 문의해 주세요.')
    return {
        'ok': True,
        'path': BLACKBOX_DIR,
        'msg': '프로그램 폴더 내 blackbox 폴더의 report_upload.md / report_test.md와 영상을 소통방에 첨부해 문의해 주세요.',
    }


def get_done_storage_info():
    """done/ 폴더(history.json 제외), temp_thumbs/, blackbox/ 폴더(AI_RULES.md·실행 도구 .command 제외)의 합산 용량 및 파일 개수 계산"""
    total_bytes = 0
    file_count = 0
    for target_dir, exclude_names in ((DONE_DIR, {'history.json'}), (THUMB_DIR, set()), (BLACKBOX_DIR, set())):
        if not os.path.isdir(target_dir):
            continue
        for root, _, files in os.walk(target_dir):
            for fname in files:
                if root == target_dir and fname in exclude_names:
                    continue
                if root == BLACKBOX_DIR and is_blackbox_protected_file(fname):
                    continue
                fp = os.path.join(root, fname)
                try:
                    if os.path.isfile(fp):
                        total_bytes += os.path.getsize(fp)
                        file_count += 1
                except Exception:
                    pass
    total_mb = round(total_bytes / (1024.0 * 1024.0), 1)
    over_1gb = total_bytes >= (1024 * 1024 * 1024)
    return {
        'ok': True,
        'total_bytes': total_bytes,
        'total_mb': total_mb,
        'file_count': file_count,
        'over_1gb': over_1gb,
    }


def clear_done_and_thumbs():
    """done/ 폴더(history.json 보존), temp_thumbs/, blackbox/ 폴더(AI_RULES.md·실행 도구 .command 보존) 내부 파일 수동 비우기 (auth_data/·upload/ 절대 보호)"""
    with STATE_LOCK:
        is_busy = STATE.get('busy')
    if is_busy:
        return {'ok': False, 'msg': BUSY_TASK_MSG}

    before = get_done_storage_info()
    removed_count = 0
    for target_dir, keep_names in ((DONE_DIR, {'history.json'}), (THUMB_DIR, set()), (BLACKBOX_DIR, set())):
        if not os.path.isdir(target_dir):
            continue
        for entry in list(os.listdir(target_dir)):
            if entry in keep_names:
                continue
            if target_dir == BLACKBOX_DIR and is_blackbox_protected_file(entry):
                continue
            ep = os.path.join(target_dir, entry)
            try:
                if os.path.isdir(ep):
                    for _, _, sub_files in os.walk(ep):
                        removed_count += len(sub_files)
                    shutil.rmtree(ep, ignore_errors=True)
                elif os.path.isfile(ep) or os.path.islink(ep):
                    os.remove(ep)
                    removed_count += 1
            except Exception:
                pass

    after = get_done_storage_info()
    media_freed_mb = round(max(0.0, (before['total_bytes'] - after['total_bytes']) / (1024.0 * 1024.0)), 1)
    cache_res = cleanup_chrome_profile_caches()
    cache_freed_mb = cache_res.get('freed_mb', 0.0)
    total_freed_mb = round(media_freed_mb + cache_freed_mb, 1)
    if cache_res.get('freed_bytes', 0) > 0 or cache_res.get('removed_dirs', 0) > 0:
        log(f"🧹 크롬 캐시 정리 완료: {cache_freed_mb} MB 확보 (로그인 세션 유지)")
    log(f"🗑️ [완료 파일 비우기] done/temp_thumbs/blackbox 파일 {removed_count}개 삭제 ({media_freed_mb}MB) + 크롬 캐시 {cache_freed_mb}MB 확보 — 로그인 세션·대기 파일 안전 보호됨")
    return {
        'ok': True,
        'removed_count': removed_count,
        'freed_mb': total_freed_mb,
        'cache_freed_mb': cache_freed_mb,
        'storage': after,
        'msg': f"완료·블랙박스 파일 {removed_count}개({media_freed_mb}MB) 및 크롬 캐시 {cache_freed_mb}MB를 비웠습니다! (총 {total_freed_mb}MB 확보 · 로그인 세션 안전 유지)",
    }


HISTORY_FIELDS = (
    'source_text',
    'ai_video_type',
    'ai_memo_concern', 'ai_memo_technique', 'ai_memo_reaction',
    'naver_title', 'naver_body', 'naver_tags', 'naver_category',
    'clip_title', 'clip_body', 'clip_tags',
    'yt_title', 'yt_body', 'yt_tags',
    'tt_title', 'tt_body', 'tt_tags',
    'ig_body', 'ig_tags',
    'pin_title', 'pin_body', 'pin_tags',
    'fb_body', 'fb_tags',
    'fb_page_body', 'fb_page_tags',
)


def _norm_history_text(val):
    """기록 중복 판별용 문자열 정규화 (줄바꿈 통일 + 앞뒤 공백 제거)"""
    return str(val or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def _history_dedup_key(rec):
    """지난 기록 항목의 (대표 제목, 대표 본문) 앞뒤 공백 제거 기준 중복 판별 키 반환"""
    if not isinstance(rec, dict):
        return ('', '')
    title = _norm_history_text(
        rec.get('naver_title')
        or rec.get('yt_title')
        or rec.get('youtube_title')
        or rec.get('clip_title')
        or ''
    )
    body = _norm_history_text(
        rec.get('naver_body')
        or rec.get('yt_body')
        or rec.get('youtube_body')
        or rec.get('ig_body')
        or rec.get('instagram_body')
        or rec.get('source_text')
        or ''
    )
    return (title, body)


HISTORY_PLATFORM_ORDER = ('yt', 'naver', 'clip', 'ig', 'tt', 'fb', 'fb_page', 'pin')
HISTORY_PLATFORM_ALIASES = {
    'yt': 'yt', 'youtube': 'yt', 'youtube_shorts': 'yt',
    'naver': 'naver', 'naver_blog': 'naver', 'blog': 'naver',
    'clip': 'clip', 'naver_clip': 'clip',
    'tt': 'tt', 'tiktok': 'tt',
    'ig': 'ig', 'instagram': 'ig', 'reels': 'ig',
    'pin': 'pin', 'pinterest': 'pin',
    'fb': 'fb', 'facebook': 'fb', 'fb_personal': 'fb',
    'fb_page': 'fb_page', 'facebook_page': 'fb_page',
}


def _normalize_platform_key(raw_key):
    s = str(raw_key or '').strip().lower()
    return HISTORY_PLATFORM_ALIASES.get(s, '')


def _normalize_uploaded_platforms(raw_list):
    """기록의 uploaded_platforms 배열을 표준 8개 플랫폼 키 순서로 중복 제거·정규화 (없는 경우 빈 배열 반환)"""
    if not isinstance(raw_list, (list, tuple, set)):
        return []
    seen = set()
    for item in raw_list:
        k = _normalize_platform_key(item)
        if k:
            seen.add(k)
    return [k for k in HISTORY_PLATFORM_ORDER if k in seen]


def _get_done_gifs_dir():
    """생성한 GIF 영구 보관 폴더(done/gifs/) 경로 반환"""
    return os.path.join(DONE_DIR, 'gifs')


def _get_done_media_dir():
    """지난 기록 6칸 슬롯 미디어(대표사진·커스텀 캡처 JPG·GIF) 영구 보관 폴더(done/media/) 경로 반환"""
    return os.path.join(DONE_DIR, 'media')


def _normalize_saved_slots(raw_slots):
    """done/history.json의 saved_slots 목록을 표준 구조[{slot_idx, filename, kind, active, label}]로 정규화"""
    if not isinstance(raw_slots, list):
        return []
    out = []
    seen_slots = set()
    allowed_exts = ('.jpg', '.jpeg', '.png', '.gif')
    for item in raw_slots:
        if not isinstance(item, dict):
            continue
        try:
            slot_idx = int(item.get('slot_idx'))
        except Exception:
            continue
        if slot_idx < 0 or slot_idx > 5 or slot_idx in seen_slots:
            continue
        fname = os.path.basename(str(item.get('filename') or '').strip())
        if fname and not fname.lower().endswith(allowed_exts):
            fname = ''
        raw_kind = str(item.get('kind') or '').strip().lower()
        kind = 'gif' if (raw_kind == 'gif' or fname.lower().endswith('.gif')) else 'image'
        active = bool(item.get('active', True))
        default_lbl = '대표' if slot_idx == 0 else str(slot_idx)
        label = str(item.get('label') or default_lbl).strip()[:40] or default_lbl
        seen_slots.add(slot_idx)
        out.append({
            'slot_idx': slot_idx,
            'filename': fname,
            'kind': kind,
            'active': active,
            'label': label,
        })
    out.sort(key=lambda x: x['slot_idx'])
    return out


def _collect_referenced_media_filenames(records):
    refs = set()
    for it in (records or []):
        if not isinstance(it, dict):
            continue
        for s in _normalize_saved_slots(it.get('saved_slots')):
            if s.get('filename'):
                refs.add(s['filename'])
    return refs


def _cleanup_unreferenced_done_media(candidate_names, remaining_records):
    """다른 기록에서 더 이상 참조하지 않는 done/media/ 내 고아 미디어 파일만 안전하게 삭제"""
    if not candidate_names:
        return 0
    still_used = _collect_referenced_media_filenames(remaining_records)
    media_dir = _get_done_media_dir()
    if not os.path.isdir(media_dir):
        return 0
    removed = 0
    for fname in set(candidate_names):
        safe_fn = os.path.basename(str(fname or '').strip())
        if not safe_fn or safe_fn in still_used:
            continue
        fp = os.path.join(media_dir, safe_fn)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                removed += 1
            except Exception:
                pass
    return removed


def backup_slots_to_done_media(slots_input, history_id=''):
    """6칸 슬롯(대표사진 0번 + 본문 1~5번)의 실존 이미지(.jpg/.jpeg/.png) 및 .gif 파일을
    done/media/에 고유 파일명으로 영구 복사하고 saved_slots 메타 목록을 반환"""
    if not isinstance(slots_input, list):
        return []
    safe_hid = re.sub(r'[^A-Za-z0-9_-]+', '', str(history_id or '').strip()) or datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    media_dir = _get_done_media_dir()
    allowed_exts = ('.jpg', '.jpeg', '.png', '.gif')
    ts_ms = int(time.time() * 1000)
    saved = []

    for item in slots_input[:6]:
        if not isinstance(item, dict):
            continue
        try:
            slot_idx = int(item.get('slot_idx'))
        except Exception:
            continue
        if slot_idx < 0 or slot_idx > 5:
            continue
        active = bool(item.get('active', True))
        default_lbl = '대표' if slot_idx == 0 else str(slot_idx)
        label = str(item.get('label') or default_lbl).strip()[:40] or default_lbl
        raw_kind = str(item.get('kind') or '').strip().lower()
        p = str(item.get('path') or '').strip()
        prev_fn = os.path.basename(str(item.get('saved_filename') or '').strip())

        if not p or not os.path.isfile(p):
            # 파일이 없는 슬롯이라도 기존 보관 파일명이 있으면 재사용, 없으면 빈 슬롯 상태 보존
            if prev_fn and os.path.isfile(os.path.join(media_dir, prev_fn)):
                kind = 'gif' if (raw_kind == 'gif' or prev_fn.lower().endswith('.gif')) else 'image'
                saved.append({
                    'slot_idx': slot_idx,
                    'filename': prev_fn,
                    'kind': kind,
                    'active': active,
                    'label': label,
                })
            else:
                saved.append({
                    'slot_idx': slot_idx,
                    'filename': '',
                    'kind': 'gif' if raw_kind == 'gif' else 'image',
                    'active': False,
                    'label': label,
                })
            continue

        ext = os.path.splitext(p)[1].lower()
        if ext not in allowed_exts:
            ext = '.gif' if raw_kind == 'gif' else '.jpg'
        kind = 'gif' if (raw_kind == 'gif' or ext == '.gif') else 'image'
        try:
            src_size = os.path.getsize(p)
        except Exception:
            src_size = 0
        if src_size <= 0:
            continue

        os.makedirs(media_dir, exist_ok=True)
        # 기존에 이미 done/media/에 동일 크기 파일이 보관되어 있으면 재사용
        if prev_fn and prev_fn.lower().endswith(allowed_exts):
            prev_fp = os.path.join(media_dir, prev_fn)
            try:
                if os.path.isfile(prev_fp) and os.path.getsize(prev_fp) == src_size:
                    saved.append({
                        'slot_idx': slot_idx,
                        'filename': prev_fn,
                        'kind': kind,
                        'active': active,
                        'label': label,
                    })
                    continue
            except Exception:
                pass

        dst_name = f"slot_{safe_hid}_s{slot_idx}_{ts_ms + slot_idx}{ext}"
        dst_path = os.path.join(media_dir, dst_name)
        try:
            shutil.copy2(p, dst_path)
            if os.path.isfile(dst_path) and os.path.getsize(dst_path) > 0:
                saved.append({
                    'slot_idx': slot_idx,
                    'filename': dst_name,
                    'kind': kind,
                    'active': active,
                    'label': label,
                })
        except Exception:
            pass
    return _normalize_saved_slots(saved)


def restore_saved_slots_to_thumbs(saved_slots):
    """기록의 saved_slots 중 done/media/(또는 done/gifs/)에 실존하는 파일을 temp_thumbs/로 복사해
    6칸 슬롯 복원 목록(파일 경로·URL·kind·active·label)을 반환"""
    media_dir = _get_done_media_dir()
    gifs_dir = _get_done_gifs_dir()
    restored = []
    ts_ms = int(time.time() * 1000)
    for s in _normalize_saved_slots(saved_slots):
        fname = s['filename']
        src_fp = ''
        if fname:
            cand_media = os.path.join(media_dir, fname)
            cand_gif = os.path.join(gifs_dir, fname)
            if os.path.isfile(cand_media):
                src_fp = cand_media
            elif os.path.isfile(cand_gif):
                src_fp = cand_gif
        if src_fp:
            try:
                if os.path.getsize(src_fp) > 0:
                    os.makedirs(THUMB_DIR, exist_ok=True)
                    dst_name = f"restored_{fname}"
                    dst_fp = os.path.join(THUMB_DIR, dst_name)
                    shutil.copy2(src_fp, dst_fp)
                    if os.path.isfile(dst_fp) and os.path.getsize(dst_fp) > 0:
                        restored.append({
                            'slot_idx': s['slot_idx'],
                            'filename': fname,
                            'path': dst_fp,
                            'url': f"/thumbs/{quote(dst_name)}?t={ts_ms}",
                            'kind': s['kind'],
                            'active': s['active'],
                            'label': s['label'],
                        })
                        continue
            except Exception:
                pass
        restored.append({
            'slot_idx': s['slot_idx'],
            'filename': '',
            'path': '',
            'url': '',
            'kind': s['kind'],
            'active': False,
            'label': s['label'],
        })
    return restored


def _normalize_saved_gifs(raw_gifs):
    """done/history.json의 saved_gifs 목록을 표준 구조[{slot_idx, filename, label}]로 정규화"""
    if not isinstance(raw_gifs, list):
        return []
    out = []
    seen_slots = set()
    for item in raw_gifs:
        if not isinstance(item, dict):
            continue
        try:
            slot_idx = int(item.get('slot_idx'))
        except Exception:
            continue
        if slot_idx < 0 or slot_idx > 9 or slot_idx in seen_slots:
            continue
        fname = os.path.basename(str(item.get('filename') or '').strip())
        if not fname or not fname.lower().endswith('.gif'):
            continue
        label = str(item.get('label') or 'GIF').strip()[:40] or 'GIF'
        seen_slots.add(slot_idx)
        out.append({'slot_idx': slot_idx, 'filename': fname, 'label': label})
    out.sort(key=lambda x: x['slot_idx'])
    return out


def _collect_referenced_gif_filenames(records):
    refs = set()
    for it in (records or []):
        if not isinstance(it, dict):
            continue
        for g in _normalize_saved_gifs(it.get('saved_gifs')):
            refs.add(g['filename'])
    return refs


def _collect_referenced_video_filenames(records):
    refs = set()
    for it in (records or []):
        if not isinstance(it, dict):
            continue
        vn = os.path.basename(str(it.get('video_name') or '').strip())
        if vn:
            refs.add(vn)
    return refs


def _cleanup_unreferenced_done_gifs(candidate_names, remaining_records):
    """다른 기록에서 더 이상 참조하지 않는 done/gifs/ 내 고아 GIF 파일만 안전하게 삭제"""
    if not candidate_names:
        return 0
    still_used = _collect_referenced_gif_filenames(remaining_records)
    gifs_dir = _get_done_gifs_dir()
    if not os.path.isdir(gifs_dir):
        return 0
    removed = 0
    for fname in set(candidate_names):
        safe_fn = os.path.basename(str(fname or '').strip())
        if not safe_fn or safe_fn in still_used:
            continue
        fp = os.path.join(gifs_dir, safe_fn)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                removed += 1
            except Exception:
                pass
    return removed


def _cleanup_unreferenced_done_videos(candidate_names, remaining_records):
    """다른 기록에서 더 이상 참조하지 않는 done/ 내 백업 영상 파일만 안전하게 삭제 (history.json 절대 보호)"""
    if not candidate_names or not os.path.isdir(DONE_DIR):
        return 0
    still_used = _collect_referenced_video_filenames(remaining_records)
    removed = 0
    for fname in set(candidate_names):
        safe_fn = os.path.basename(str(fname or '').strip())
        if not safe_fn or safe_fn == 'history.json' or safe_fn in still_used:
            continue
        fp = os.path.join(DONE_DIR, safe_fn)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                removed += 1
            except Exception:
                pass
    return removed


def _cleanup_all_unreferenced_done_media():
    """done/history.json에서 참조하지 않는 모든 done/ 백업 영상 및 done/gifs/, done/media/ 미참조 파일을 안전 정리.
    history.json과 기록에 연결된 영상·사진·GIF는 100% 보존."""
    hf = os.path.join(DONE_DIR, 'history.json')
    removed_videos = 0
    removed_gifs = 0
    removed_media = 0
    freed_bytes = 0
    with HISTORY_LOCK:
        hist = []
        if os.path.exists(hf):
            try:
                with open(hf, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    hist = loaded
            except Exception:
                hist = []
        ref_videos = _collect_referenced_video_filenames(hist)
        ref_gifs = _collect_referenced_gif_filenames(hist)
        ref_media = _collect_referenced_media_filenames(hist)

        if os.path.isdir(DONE_DIR):
            for entry in list(os.listdir(DONE_DIR)):
                if entry in ('history.json', 'gifs', 'media'):
                    continue
                if entry in ref_videos:
                    continue
                fp = os.path.join(DONE_DIR, entry)
                if os.path.isfile(fp):
                    try:
                        fsize = os.path.getsize(fp)
                        os.remove(fp)
                        removed_videos += 1
                        freed_bytes += max(0, fsize)
                    except Exception:
                        pass

        gifs_dir = _get_done_gifs_dir()
        if os.path.isdir(gifs_dir):
            for entry in list(os.listdir(gifs_dir)):
                if entry in ref_gifs:
                    continue
                fp = os.path.join(gifs_dir, entry)
                if os.path.isfile(fp):
                    try:
                        fsize = os.path.getsize(fp)
                        os.remove(fp)
                        removed_gifs += 1
                        freed_bytes += max(0, fsize)
                    except Exception:
                        pass

        media_dir = _get_done_media_dir()
        if os.path.isdir(media_dir):
            for entry in list(os.listdir(media_dir)):
                if entry in ref_media:
                    continue
                fp = os.path.join(media_dir, entry)
                if os.path.isfile(fp):
                    try:
                        fsize = os.path.getsize(fp)
                        os.remove(fp)
                        removed_media += 1
                        freed_bytes += max(0, fsize)
                    except Exception:
                        pass

    return {
        'removed_videos': removed_videos,
        'removed_gifs': removed_gifs + removed_media,
        'removed_media': removed_media,
        'freed_bytes': freed_bytes,
        'freed_mb': round(freed_bytes / (1024.0 * 1024.0), 1),
    }


def backup_selected_gifs_to_done(selected_images, history_id='', gif_meta=None):
    """실업로드 시 naver_selected_images 배열에 포함된 실존 .gif 파일을 done/gifs/로 복사 보관하고
    saved_gifs 메타 목록([{slot_idx, filename, label}])을 반환"""
    if not isinstance(selected_images, list):
        return []
    meta_by_slot = {}
    if isinstance(gif_meta, list):
        for m in gif_meta:
            if not isinstance(m, dict):
                continue
            try:
                s_idx = int(m.get('slot_idx'))
            except Exception:
                continue
            if 0 <= s_idx <= 9:
                meta_by_slot[s_idx] = m

    safe_hid = re.sub(r'[^A-Za-z0-9_-]+', '', str(history_id or '').strip()) or datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    gifs_dir = _get_done_gifs_dir()
    saved = []
    ts_ms = int(time.time() * 1000)

    for slot_idx, raw_path in enumerate(selected_images[:10]):
        p = str(raw_path or '').strip()
        if not p or not p.lower().endswith('.gif') or not os.path.isfile(p):
            continue
        try:
            src_size = os.path.getsize(p)
        except Exception:
            src_size = 0
        if src_size <= 0:
            continue

        m_info = meta_by_slot.get(slot_idx) or {}
        label = str(m_info.get('label') or 'GIF').strip()[:40] or 'GIF'
        prev_fn = os.path.basename(str(m_info.get('saved_filename') or '').strip())

        os.makedirs(gifs_dir, exist_ok=True)
        # 복원된 기존 GIF를 그대로 재업로드하는 경우 동일 보관 파일 재사용
        if prev_fn and prev_fn.lower().endswith('.gif'):
            prev_fp = os.path.join(gifs_dir, prev_fn)
            try:
                if os.path.isfile(prev_fp) and os.path.getsize(prev_fp) == src_size:
                    saved.append({'slot_idx': slot_idx, 'filename': prev_fn, 'label': label})
                    continue
            except Exception:
                pass

        dst_name = f"gif_{safe_hid}_s{slot_idx}_{ts_ms + slot_idx}.gif"
        dst_path = os.path.join(gifs_dir, dst_name)
        try:
            shutil.copy2(p, dst_path)
            if os.path.isfile(dst_path) and os.path.getsize(dst_path) > 0:
                saved.append({'slot_idx': slot_idx, 'filename': dst_name, 'label': label})
        except Exception:
            pass
    return _normalize_saved_gifs(saved)


def restore_saved_gifs_to_thumbs(saved_gifs):
    """기록의 saved_gifs 중 done/gifs/에 실존하는 파일을 temp_thumbs/로 복사해 프론트 복원 목록 반환"""
    gifs_dir = _get_done_gifs_dir()
    restored = []
    ts_ms = int(time.time() * 1000)
    for g in _normalize_saved_gifs(saved_gifs):
        fname = g['filename']
        src_fp = os.path.join(gifs_dir, fname)
        if not os.path.isfile(src_fp):
            continue
        try:
            if os.path.getsize(src_fp) <= 0:
                continue
            os.makedirs(THUMB_DIR, exist_ok=True)
            dst_name = f"restored_{fname}"
            dst_fp = os.path.join(THUMB_DIR, dst_name)
            shutil.copy2(src_fp, dst_fp)
            if os.path.isfile(dst_fp) and os.path.getsize(dst_fp) > 0:
                restored.append({
                    'slot_idx': g['slot_idx'],
                    'filename': fname,
                    'path': dst_fp,
                    'url': f"/thumbs/{quote(dst_name)}?t={ts_ms}",
                    'label': g['label'],
                    'kind': 'gif',
                })
        except Exception:
            pass
    return restored


def build_history_record(raw_data, video_name='', source_text=None):
    """미리보기 변환 결과 또는 업로드 요청 페이로드를 done/history.json 표준 레코드 형식으로 정규화"""
    src = raw_data if isinstance(raw_data, dict) else {}
    alias_map = {
        'ai_memo_concern': ('ai_memo_concern', 'memo_concern'),
        'ai_memo_technique': ('ai_memo_technique', 'memo_technique'),
        'ai_memo_reaction': ('ai_memo_reaction', 'memo_reaction'),
        'yt_title': ('yt_title', 'youtube_title'),
        'yt_body': ('yt_body', 'youtube_body'),
        'yt_tags': ('yt_tags', 'youtube_tags'),
        'tt_title': ('tt_title', 'tiktok_title'),
        'tt_body': ('tt_body', 'tiktok_body'),
        'tt_tags': ('tt_tags', 'tiktok_tags'),
        'ig_body': ('ig_body', 'instagram_body'),
        'ig_tags': ('ig_tags', 'instagram_tags'),
    }
    now = datetime.datetime.now()
    rec = {
        'id': now.strftime('%Y%m%d_%H%M%S_%f'),
        'date': now.strftime('%Y-%m-%d %H:%M'),
        'video_name': os.path.basename(video_name) if video_name else '',
        'uploaded_platforms': _normalize_uploaded_platforms(src.get('uploaded_platforms')),
    }
    if 'selected_platforms' in src and isinstance(src.get('selected_platforms'), list):
        rec['selected_platforms'] = _normalize_uploaded_platforms(src.get('selected_platforms'))
    if 'saved_gifs' in src:
        rec['saved_gifs'] = _normalize_saved_gifs(src.get('saved_gifs'))
    if 'saved_slots' in src:
        rec['saved_slots'] = _normalize_saved_slots(src.get('saved_slots'))
    for k in HISTORY_FIELDS:
        if k == 'source_text' and source_text is not None:
            val = source_text
        elif k in alias_map:
            val = ''
            for ak in alias_map[k]:
                if src.get(ak) not in (None, ''):
                    val = src.get(ak)
                    break
        else:
            val = src.get(k, '')
        rec[k] = normalize_branch_name(val) if isinstance(val, str) else val
    return rec


def save_history_record(rec, update_id=''):
    """done/history.json에 기록 저장 또는 기존 기록 덮어쓰기 갱신.
    - update_id가 전달되고 해당 id 기록이 존재하면 새 항목을 추가하지 않고 기존 기록을 최종 수정본으로 덮어쓰기
      (기존 uploaded_platforms 유지, 최신 date 반영 후 목록 최상단 이동)
    - update_id가 없거나 일치 항목이 없으면 (제목, 본문) 중복 검사 후 신규 추가"""
    new_key = _history_dedup_key(rec)
    if new_key == ('', ''):
        return {'saved': False, 'updated': False, 'duplicate': False, 'empty': True, 'id': ''}

    uid = str(update_id or '').strip()
    has_selected_platforms_key = isinstance(rec, dict) and ('selected_platforms' in rec) and isinstance(rec.get('selected_platforms'), list)
    incoming_selected_plats = _normalize_uploaded_platforms(rec.get('selected_platforms')) if has_selected_platforms_key else None
    has_saved_gifs_key = isinstance(rec, dict) and ('saved_gifs' in rec)
    incoming_gifs = _normalize_saved_gifs(rec.get('saved_gifs')) if has_saved_gifs_key else None
    has_saved_slots_key = isinstance(rec, dict) and ('saved_slots' in rec)
    incoming_slots = _normalize_saved_slots(rec.get('saved_slots')) if has_saved_slots_key else None
    hf = os.path.join(DONE_DIR, 'history.json')

    with HISTORY_LOCK:
        hl = []
        if os.path.exists(hf):
            try:
                with open(hf, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    hl = loaded
            except Exception:
                hl = []

        # 1) update_id 일치 시 기존 기록 덮어쓰기 갱신 (미리보기 저장 → 본문 수정/GIF 추가 → 업로드 중복 방지)
        if uid:
            match_idx = next(
                (i for i, it in enumerate(hl) if isinstance(it, dict) and str(it.get('id', '')) == uid),
                None,
            )
            if match_idx is not None:
                existing = hl.pop(match_idx)
                old_gif_names = {g['filename'] for g in _normalize_saved_gifs(existing.get('saved_gifs'))}
                old_media_names = {s['filename'] for s in _normalize_saved_slots(existing.get('saved_slots')) if s.get('filename')}
                merged_plats = _normalize_uploaded_platforms(
                    _normalize_uploaded_platforms(existing.get('uploaded_platforms'))
                    + _normalize_uploaded_platforms(rec.get('uploaded_platforms'))
                )
                updated = dict(existing)
                updated['id'] = uid
                updated['date'] = rec.get('date') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                if rec.get('video_name'):
                    updated['video_name'] = os.path.basename(str(rec.get('video_name')))
                elif not updated.get('video_name'):
                    updated['video_name'] = ''
                updated['uploaded_platforms'] = merged_plats
                if has_selected_platforms_key:
                    updated['selected_platforms'] = incoming_selected_plats
                elif isinstance(existing.get('selected_platforms'), list):
                    updated['selected_platforms'] = _normalize_uploaded_platforms(existing.get('selected_platforms'))
                for k in HISTORY_FIELDS:
                    if k in rec:
                        updated[k] = rec[k]
                if has_saved_gifs_key:
                    updated['saved_gifs'] = incoming_gifs
                else:
                    updated['saved_gifs'] = _normalize_saved_gifs(existing.get('saved_gifs'))
                if has_saved_slots_key:
                    updated['saved_slots'] = incoming_slots
                else:
                    updated['saved_slots'] = _normalize_saved_slots(existing.get('saved_slots'))

                hl.insert(0, updated)
                trimmed_out = hl[50:]
                hl_keep = hl[:50]
                os.makedirs(DONE_DIR, exist_ok=True)
                tmp_hf = f"{hf}.tmp"
                with open(tmp_hf, 'w', encoding='utf-8') as f:
                    json.dump(hl_keep, f, ensure_ascii=False, indent=2)
                os.replace(tmp_hf, hf)

                orphan_cands = set(old_gif_names) | _collect_referenced_gif_filenames(trimmed_out)
                _cleanup_unreferenced_done_gifs(orphan_cands, hl_keep)
                orphan_media_cands = set(old_media_names) | _collect_referenced_media_filenames(trimmed_out)
                _cleanup_unreferenced_done_media(orphan_media_cands, hl_keep)
                return {
                    'saved': True,
                    'updated': True,
                    'duplicate': False,
                    'empty': False,
                    'id': uid,
                    'uploaded_platforms': list(merged_plats),
                    'saved_gifs': list(updated.get('saved_gifs', [])),
                    'saved_slots': list(updated.get('saved_slots', [])),
                }

        # 2) 동일 제목+본문 중복 판별 (새 미디어·메모·영상명이 변경/추가된 경우에는 기존 기록에 안전 반영)
        for idx_ex, existing in enumerate(hl):
            if _history_dedup_key(existing) == new_key:
                ex_id = str(existing.get('id', ''))
                ex_plats = _normalize_uploaded_platforms(existing.get('uploaded_platforms'))
                need_enrich = False
                old_gif_names = {g['filename'] for g in _normalize_saved_gifs(existing.get('saved_gifs'))}
                old_media_names = {s['filename'] for s in _normalize_saved_slots(existing.get('saved_slots')) if s.get('filename')}
                if has_selected_platforms_key and (
                    (not isinstance(existing.get('selected_platforms'), list))
                    or incoming_selected_plats != _normalize_uploaded_platforms(existing.get('selected_platforms'))
                ):
                    existing['selected_platforms'] = incoming_selected_plats
                    need_enrich = True
                if has_saved_gifs_key and incoming_gifs != _normalize_saved_gifs(existing.get('saved_gifs')):
                    existing['saved_gifs'] = incoming_gifs
                    need_enrich = True
                if has_saved_slots_key and incoming_slots != _normalize_saved_slots(existing.get('saved_slots')):
                    existing['saved_slots'] = incoming_slots
                    need_enrich = True
                if has_saved_slots_key:
                    for k in HISTORY_FIELDS:
                        if k in rec and rec[k] != existing.get(k):
                            existing[k] = rec[k]
                            need_enrich = True
                if rec.get('video_name') and not existing.get('video_name'):
                    existing['video_name'] = os.path.basename(str(rec.get('video_name')))
                    need_enrich = True
                if need_enrich:
                    existing['date'] = rec.get('date') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                    hl.pop(idx_ex)
                    hl.insert(0, existing)
                    hl_keep = hl[:50]
                    os.makedirs(DONE_DIR, exist_ok=True)
                    tmp_hf = f"{hf}.tmp"
                    with open(tmp_hf, 'w', encoding='utf-8') as f:
                        json.dump(hl_keep, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_hf, hf)
                    _cleanup_unreferenced_done_gifs(old_gif_names, hl_keep)
                    _cleanup_unreferenced_done_media(old_media_names, hl_keep)
                    return {
                        'saved': True,
                        'updated': True,
                        'duplicate': True,
                        'empty': False,
                        'id': ex_id,
                        'uploaded_platforms': ex_plats,
                        'saved_gifs': list(existing.get('saved_gifs', [])),
                        'saved_slots': list(existing.get('saved_slots', [])),
                    }
                return {
                    'saved': False,
                    'updated': False,
                    'duplicate': True,
                    'empty': False,
                    'id': ex_id,
                    'uploaded_platforms': ex_plats,
                    'saved_slots': list(_normalize_saved_slots(existing.get('saved_slots'))),
                }

        existing_ids = {str(it.get('id', '')) for it in hl if isinstance(it, dict)}
        base_id = str(rec.get('id') or datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        cand_id = base_id
        suffix = 1
        while cand_id in existing_ids:
            cand_id = f"{base_id}_{suffix}"
            suffix += 1
        rec['id'] = cand_id
        rec['uploaded_platforms'] = _normalize_uploaded_platforms(rec.get('uploaded_platforms'))
        if has_selected_platforms_key:
            rec['selected_platforms'] = incoming_selected_plats
        rec['saved_gifs'] = incoming_gifs if has_saved_gifs_key else []
        rec['saved_slots'] = incoming_slots if has_saved_slots_key else []

        hl.insert(0, rec)
        trimmed_out = hl[50:]
        hl_keep = hl[:50]
        os.makedirs(DONE_DIR, exist_ok=True)
        tmp_hf = f"{hf}.tmp"
        with open(tmp_hf, 'w', encoding='utf-8') as f:
            json.dump(hl_keep, f, ensure_ascii=False, indent=2)
        os.replace(tmp_hf, hf)
        if trimmed_out:
            _cleanup_unreferenced_done_gifs(_collect_referenced_gif_filenames(trimmed_out), hl_keep)
            _cleanup_unreferenced_done_media(_collect_referenced_media_filenames(trimmed_out), hl_keep)
        return {
            'saved': True,
            'updated': False,
            'duplicate': False,
            'empty': False,
            'id': cand_id,
            'uploaded_platforms': list(rec['uploaded_platforms']),
            'saved_gifs': list(rec['saved_gifs']),
            'saved_slots': list(rec['saved_slots']),
        }


def get_history_uploaded_platforms(history_id='', fallback_rec=None):
    """특정 히스토리 항목의 기존 uploaded_platforms 목록 조회 (과거 기록에 필드가 없으면 빈 배열 반환)"""
    hf = os.path.join(DONE_DIR, 'history.json')
    if not os.path.exists(hf):
        return []
    hid = str(history_id or '').strip()
    fb_key = _history_dedup_key(fallback_rec) if isinstance(fallback_rec, dict) else ('', '')
    with HISTORY_LOCK:
        try:
            with open(hf, 'r', encoding='utf-8') as f:
                hl = json.load(f)
            if not isinstance(hl, list):
                return []
        except Exception:
            return []
        target = None
        if hid:
            target = next((it for it in hl if isinstance(it, dict) and str(it.get('id', '')) == hid), None)
        if target is None and fb_key != ('', ''):
            target = next((it for it in hl if isinstance(it, dict) and _history_dedup_key(it) == fb_key), None)
        return _normalize_uploaded_platforms(target.get('uploaded_platforms')) if isinstance(target, dict) else []


def record_history_uploaded_platform(history_id, platform_key, fallback_rec=None):
    """업로드 성공한 플랫폼 키를 해당 히스토리 항목의 uploaded_platforms에 중복 없이 누적 저장"""
    norm_key = _normalize_platform_key(platform_key)
    if not norm_key:
        return None
    hf = os.path.join(DONE_DIR, 'history.json')
    hid = str(history_id or '').strip()
    fb_key = _history_dedup_key(fallback_rec) if isinstance(fallback_rec, dict) else ('', '')
    with HISTORY_LOCK:
        hl = []
        if os.path.exists(hf):
            try:
                with open(hf, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    hl = loaded
            except Exception:
                hl = []

        target = None
        if hid:
            target = next((it for it in hl if isinstance(it, dict) and str(it.get('id', '')) == hid), None)
        if target is None and fb_key != ('', ''):
            target = next((it for it in hl if isinstance(it, dict) and _history_dedup_key(it) == fb_key), None)
        if target is None:
            return None

        cur_list = _normalize_uploaded_platforms(target.get('uploaded_platforms'))
        if norm_key not in cur_list:
            cur_list = _normalize_uploaded_platforms(cur_list + [norm_key])
            target['uploaded_platforms'] = cur_list
            os.makedirs(DONE_DIR, exist_ok=True)
            tmp_hf = f"{hf}.tmp"
            with open(tmp_hf, 'w', encoding='utf-8') as f:
                json.dump(hl[:50], f, ensure_ascii=False, indent=2)
            os.replace(tmp_hf, hf)
        else:
            target['uploaded_platforms'] = cur_list
        return list(cur_list)


def delete_history_records(ids):
    """done/history.json에서 선택한 ID의 업로드 기록 삭제 + 다른 기록에서 미참조된 done/ 영상·done/gifs/ 파일 자동 정리"""
    if not isinstance(ids, list) or not ids:
        return {'ok': False, 'msg': '삭제할 기록을 선택해 주세요.'}
    id_set = {str(x) for x in ids if x is not None and str(x).strip()}
    hf = os.path.join(DONE_DIR, 'history.json')
    with HISTORY_LOCK:
        if not os.path.exists(hf):
            return {'ok': True, 'deleted': 0, 'deleted_gifs': 0, 'deleted_videos': 0, 'history': []}
        try:
            with open(hf, 'r', encoding='utf-8') as f:
                hist = json.load(f)
            if not isinstance(hist, list):
                hist = []
        except Exception:
            hist = []
        removed_recs = [it for it in hist if isinstance(it, dict) and str(it.get('id', '')) in id_set]
        remaining = [it for it in hist if not (isinstance(it, dict) and str(it.get('id', '')) in id_set)]
        deleted = len(hist) - len(remaining)
        os.makedirs(DONE_DIR, exist_ok=True)
        tmp_hf = f"{hf}.tmp"
        with open(tmp_hf, 'w', encoding='utf-8') as f:
            json.dump(remaining, f, ensure_ascii=False, indent=2)
        os.replace(tmp_hf, hf)
        deleted_gifs = _cleanup_unreferenced_done_gifs(_collect_referenced_gif_filenames(removed_recs), remaining)
        deleted_media = _cleanup_unreferenced_done_media(_collect_referenced_media_filenames(removed_recs), remaining)
        deleted_videos = _cleanup_unreferenced_done_videos(_collect_referenced_video_filenames(removed_recs), remaining)
        return {
            'ok': True,
            'deleted': deleted,
            'deleted_gifs': deleted_gifs + deleted_media,
            'deleted_media': deleted_media,
            'deleted_videos': deleted_videos,
            'history': remaining,
        }


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_video_file(self, fpath, head_only=False):
        if not fpath or not os.path.isfile(fpath):
            self.send_error(404, 'Video Not Found')
            return
        file_size = os.path.getsize(fpath)
        if file_size <= 0:
            self.send_error(404, 'Video Empty')
            return
        ext = os.path.splitext(fpath)[1].lower()
        if ext == '.mp4':
            ctype = 'video/mp4'
        elif ext == '.mov':
            ctype = 'video/quicktime'
        else:
            ctype = mimetypes.guess_type(fpath)[0] or 'video/mp4'
        range_hdr = self.headers.get('Range')
        start_b, end_b = 0, file_size - 1
        status_code = 200
        if range_hdr and range_hdr.startswith('bytes='):
            try:
                r_spec = range_hdr.split('=', 1)[1].split(',')[0].strip()
                p1, p2 = r_spec.split('-', 1)
                if p1:
                    start_b = int(p1)
                    end_b = int(p2) if p2 else file_size - 1
                else:
                    suffix_len = int(p2)
                    start_b = max(0, file_size - suffix_len)
                    end_b = file_size - 1
                start_b = max(0, min(start_b, file_size - 1))
                end_b = max(start_b, min(end_b, file_size - 1))
                status_code = 206
            except Exception:
                start_b, end_b, status_code = 0, file_size - 1, 200
        chunk_len = (end_b - start_b) + 1
        self.send_response(status_code)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(chunk_len))
        if status_code == 206:
            self.send_header('Content-Range', f'bytes {start_b}-{end_b}/{file_size}')
        self.end_headers()
        if head_only:
            return
        try:
            with open(fpath, 'rb') as vf:
                vf.seek(start_b)
                remaining = chunk_len
                while remaining > 0:
                    buf = vf.read(min(65536, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        clean_path = self.path.split('?')[0]
        if clean_path.startswith('/video/'):
            fname = os.path.basename(unquote(clean_path[len('/video/'):]))
            self._stream_video_file(os.path.join(UPLOAD_DIR, fname), head_only=True)
        elif clean_path.startswith('/preview_video/'):
            fname = os.path.basename(unquote(clean_path[len('/preview_video/'):]))
            self._stream_video_file(os.path.join(THUMB_DIR, fname), head_only=True)
        else:
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        parsed_url = urlsplit(self.path)
        clean_path = parsed_url.path
        qs = parse_qs(parsed_url.query)
        if clean_path == '/':
            with open(UI_FILE, 'rb') as f:
                html = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif clean_path in ('/favicon.ico', '/assets/salon_uploader.ico'):
            ico_path = os.path.join(ASSETS_DIR, 'salon_uploader.ico')
            if not os.path.isfile(ico_path):
                ico_path = os.path.join(BASE_DIR, 'salon_uploader.ico')
            if os.path.isfile(ico_path):
                with open(ico_path, 'rb') as f:
                    ico_data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'image/x-icon')
                self.send_header('Content-Length', str(len(ico_data)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(ico_data)
            else:
                self.send_error(404, 'Icon Not Found')
        elif clean_path in ('/favicon.png', '/assets/favicon.png'):
            fav_path = os.path.join(ASSETS_DIR, 'favicon.png')
            fav_data = b''
            if os.path.isfile(fav_path) and os.path.getsize(fav_path) > 100:
                with open(fav_path, 'rb') as f:
                    fav_data = f.read()
            else:
                # assets/favicon.png 누락 시 assets/salon_uploader.ico 내부 PNG 프레임에서 자동 복구
                ico_path = os.path.join(ASSETS_DIR, 'salon_uploader.ico')
                if not os.path.isfile(ico_path):
                    ico_path = os.path.join(BASE_DIR, 'salon_uploader.ico')
                if os.path.isfile(ico_path):
                    try:
                        with open(ico_path, 'rb') as f:
                            raw_ico = f.read()
                        if len(raw_ico) >= 6:
                            _, _, cnt = struct.unpack_from('<HHH', raw_ico, 0)
                            best_area = -1
                            for idx_f in range(cnt):
                                off_dir = 6 + idx_f * 16
                                if off_dir + 16 > len(raw_ico):
                                    break
                                w, h, _, _, _, _, sz, off = struct.unpack_from('<BBBBHHII', raw_ico, off_dir)
                                rw, rh = (256 if w == 0 else w), (256 if h == 0 else h)
                                chunk = raw_ico[off:off + sz]
                                if chunk.startswith(b'\x89PNG\r\n\x1a\n') and (rw * rh > best_area):
                                    best_area = rw * rh
                                    fav_data = chunk
                        if fav_data:
                            os.makedirs(ASSETS_DIR, exist_ok=True)
                            with open(fav_path, 'wb') as out_f:
                                out_f.write(fav_data)
                    except Exception:
                        fav_data = b''
            if fav_data:
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(fav_data)))
                self.send_header('Cache-Control', 'public, max-age=86400')
                self.end_headers()
                self.wfile.write(fav_data)
            else:
                self.send_error(404, 'Favicon Not Found')
        elif clean_path.startswith('/thumbs/'):
            fname = os.path.basename(unquote(clean_path))
            fpath = os.path.join(THUMB_DIR, fname)
            if os.path.isfile(fpath):
                with open(fpath, 'rb') as f:
                    img = f.read()
                ext = os.path.splitext(fname)[1].lower()
                ctype = 'image/gif' if ext == '.gif' else ('image/png' if ext == '.png' else 'image/jpeg')
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(img)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(img)
            else:
                self.send_error(404, 'Thumb Not Found')
        elif clean_path.startswith('/video/'):
            fname = os.path.basename(unquote(clean_path[len('/video/'):]))
            self._stream_video_file(os.path.join(UPLOAD_DIR, fname), head_only=False)
        elif clean_path.startswith('/preview_video/'):
            fname = os.path.basename(unquote(clean_path[len('/preview_video/'):]))
            self._stream_video_file(os.path.join(THUMB_DIR, fname), head_only=False)
        elif clean_path == '/api/status':
            with STATE_LOCK:
                status_payload = {
                    'busy': STATE['busy'],
                    'logs': list(STATE['logs']),
                    'has_backup': has_backup_files(),
                    'last_results': dict(STATE.get('last_results', {})),
                    'active_history_id': str(STATE.get('active_history_id', '')),
                    'active_uploaded_platforms': list(STATE.get('active_uploaded_platforms', [])),
                    'current_platform_key': str(STATE.get('current_platform_key', '')),
                    'current_platform_label': str(STATE.get('current_platform_label', '')),
                    'is_test_mode': bool(STATE.get('is_test_mode', False)),
                    'auth_check_seq': int(STATE.get('auth_check_seq', 0)),
                }
            self.send_json(status_payload)
        elif clean_path == '/api/profile':
            prof = load_profile()
            self.send_json({
                'ok': True,
                'branch_name': get_salon_name(prof),
                'branch_address': get_salon_address(prof),
                'region_tags': get_region_tags(prof),
                'profile': prof,
                'is_configured': is_profile_ready(prof),
            })
        elif clean_path == '/api/setup_status':
            deep_flag = str((qs.get('deep') or ['0'])[0]).strip().lower() in ('1', 'true', 'yes')
            force_flag = str((qs.get('force') or ['0'])[0]).strip().lower() in ('1', 'true', 'yes')
            self.send_json(get_setup_status(deep=deep_flag, force=force_flag))
        elif clean_path == '/api/quota_status':
            self.send_json(get_quota_status())
        elif clean_path == '/version.json':
            self.send_json(load_release_info())
        elif clean_path == '/api/release_info':
            self.send_json({'ok': True, 'info': load_release_info()})
        elif clean_path == '/api/check_update':
            self.send_json(check_public_update(timeout=4))
        elif clean_path == '/api/storage_info':
            self.send_json(get_done_storage_info())
        elif clean_path == '/api/history':
            hf = os.path.join(DONE_DIR, 'history.json')
            hist = []
            if os.path.exists(hf):
                with open(hf, 'r', encoding='utf-8') as f:
                    raw_hist = json.load(f)
                if isinstance(raw_hist, list):
                    for it in raw_hist:
                        if isinstance(it, dict):
                            item_copy = dict(it)
                            item_copy['uploaded_platforms'] = _normalize_uploaded_platforms(it.get('uploaded_platforms'))
                            hist.append(item_copy)
            self.send_json({'history': hist})
        else:
            self.send_error(404, 'Not Found')

    def do_POST(self):
        if self.path == '/api/upload_chunk':
            ctype = self.headers.get('Content-Type', '')
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            boundary = ctype.split('boundary=')[-1].encode('utf-8')
            fields, chunk_data = {}, b''
            for part in body.split(b'--' + boundary):
                if b'Content-Disposition' not in part: continue
                h_bytes, _, content = part.partition(b'\r\n\r\n')
                content = content[:-2] if content.endswith(b'\r\n') else content
                h_str = h_bytes.decode('utf-8', errors='ignore')
                if 'name="video_chunk"' in h_str: chunk_data = content
                else:
                    m = re.search(r'name="([^"]+)"', h_str)
                    if m: fields[m.group(1)] = content.decode('utf-8', errors='ignore').strip()
            fn = os.path.basename(fields.get('filename', 'video.mp4'))
            idx, total = int(fields.get('chunk_index', 0)), int(fields.get('total_chunks', 1))
            sp = os.path.join(UPLOAD_DIR, fn)
            with open(sp, 'wb' if idx == 0 else 'ab') as f: f.write(chunk_data)
            with STATE_LOCK:
                STATE['video_path'] = sp
                STATE['video_name'] = fn
            resp_obj = {'ok': True, 'filename': fn}
            if idx + 1 >= total:
                log(f'📎 영상 첨부 완료: {fn}')
                resp_obj['video_meta'] = probe_video_metadata(sp)
                prev_info = ensure_browser_preview_video(sp)
                resp_obj['preview_url'] = prev_info.get('preview_url', '')
                resp_obj['preview_transcoded'] = bool(prev_info.get('transcoded', False))
                resp_obj['preview_codec'] = prev_info.get('codec', '')
            return self.send_json(resp_obj)

        l = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(l).decode('utf-8')
        req = json.loads(body) if body else {}

        if self.path == '/api/stop':
            self.send_json(force_stop_active_task())
        elif self.path == '/api/skip_platform':
            self.send_json(skip_current_platform())
        elif self.path == '/api/logout_platform':
            with STATE_LOCK:
                is_busy = bool(STATE.get('busy'))
            if is_busy:
                return self.send_json({
                    'ok': False,
                    'msg': BUSY_TASK_MSG,
                })
            plat_key = str(req.get('platform') or '').strip().lower()
            res = logout_platform_session(plat_key)
            if res.get('ok'):
                invalidate_deep_status_cache()
                log(f"🚪 [로그아웃] {res.get('msg')}")
            else:
                log(f"⚠️ [로그아웃 실패] {res.get('msg')}")
            res['status'] = get_setup_status(deep=False)
            self.send_json(res)
        elif self.path == '/api/profile':
            is_draft = bool(req.get('is_draft', False))
            saved = save_profile(req, is_draft=is_draft)
            if not is_draft:
                log(f"👤 매장·기본정보 저장 완료: {get_salon_name(saved) or '(매장명 미입력)'} / {saved.get('designer_name') or '(디자이너 미입력)'}")
            self.send_json({
                'ok': True,
                'msg': '임시 저장되었습니다.' if is_draft else '기본정보가 저장되었습니다!',
                'branch_name': get_salon_name(saved),
                'branch_address': get_salon_address(saved),
                'region_tags': get_region_tags(saved),
                'profile': saved,
                'is_configured': is_profile_ready(saved),
            })
        elif self.path == '/api/reset_personal_data':
            self.send_json(reset_personal_data(req.get('confirm_text', '')))
        elif self.path == '/api/cleanup':
            self.send_json(handle_cleanup(req.get('action')))
        elif self.path == '/api/clear_done':
            self.send_json(clear_done_and_thumbs())
        elif self.path == '/api/open_blackbox_folder':
            self.send_json(open_blackbox_folder())
        elif self.path == '/api/history/delete':
            self.send_json(delete_history_records(req.get('ids', [])))
        elif self.path == '/api/apply_update':
            with STATE_LOCK:
                is_busy = STATE['busy']
            if is_busy:
                return self.send_json({'ok': False, 'msg': BUSY_TASK_MSG})
            log('🚀 [원클릭 업데이트] GitHub 최신 버전 다운로드 및 안전 검증을 시작합니다...')
            res = apply_public_update(report=lambda pct, msg: log(f'🔄 [업데이트 {pct}%] {msg}'))
            if res.get('ok'):
                res['auto_restart'] = True
                log(f"✅ [업데이트 완료] {res.get('msg')} (잠시 후 서버를 자동 재시작합니다...)")
                self.send_json(res)
                _schedule_server_restart(server_ref=self.server, delay_sec=0.6)
            else:
                log(f"⚠️ [업데이트 실패] {res.get('msg')}")
                self.send_json(res)
        elif self.path == '/api/history/save':
            try:
                with STATE_LOCK:
                    state_vn = STATE.get('video_name', '')
                    state_vp = STATE.get('video_path', '')
                vn = os.path.basename(str(req.get('video_name') or state_vn or '').strip())
                if vn:
                    upload_src = os.path.join(UPLOAD_DIR, vn)
                    if not os.path.isfile(upload_src) and state_vp and os.path.isfile(state_vp) and os.path.basename(state_vp) == vn:
                        upload_src = state_vp
                    if os.path.isfile(upload_src):
                        os.makedirs(DONE_DIR, exist_ok=True)
                        done_dst = os.path.join(DONE_DIR, vn)
                        try:
                            if (not os.path.exists(done_dst)) or (os.path.getsize(done_dst) != os.path.getsize(upload_src)):
                                shutil.copy2(upload_src, done_dst)
                        except Exception:
                            pass

                req_hid = str(req.get('history_id') or '').strip()
                rec = build_history_record(req, video_name=vn)
                if _history_dedup_key(rec) == ('', ''):
                    return self.send_json({
                        'ok': False,
                        'msg': '저장할 제목 또는 본문이 비어 있습니다. 먼저 [✨ 미리보기 생성]으로 원고를 변환해 주세요.',
                    })
                slot_seed = req_hid or rec['id']
                slots_in = req.get('saved_slots') if isinstance(req.get('saved_slots'), list) else (req.get('saved_slots_input') or [])
                saved_slots = backup_slots_to_done_media(slots_in, history_id=slot_seed)
                rec['saved_slots'] = saved_slots

                h_res = save_history_record(rec, update_id=req_hid)
                final_hid = str(h_res.get('id') or req_hid)
                media_cnt = sum(1 for s in saved_slots if s.get('filename'))
                if h_res.get('updated'):
                    log(f'💾 [지난 기록 갱신] 현재 본문과 미디어 {media_cnt}개(대표사진·GIF·슬롯 상태)를 기존 기록에 덮어쓰기 저장했습니다.')
                else:
                    log(f'💾 [지난 기록 저장] 현재 본문과 미디어 {media_cnt}개(대표사진·GIF·슬롯 상태)를 지난 기록에 저장했습니다.')
                self.send_json({
                    'ok': True,
                    'history_id': final_hid,
                    'saved': bool(h_res.get('saved')),
                    'updated': bool(h_res.get('updated')),
                    'saved_slots': saved_slots,
                    'selected_platforms': rec.get('selected_platforms') if 'selected_platforms' in rec else None,
                    'msg': '현재 본문과 미디어(대표사진·GIF·슬롯 상태)가 지난 기록에 저장되었습니다.',
                })
            except Exception as e:
                log(f'⚠️ [지난 기록 저장 오류] {e}')
                self.send_json({'ok': False, 'msg': f'지난 기록 저장 중 오류가 발생했습니다: {e}'})
        elif self.path == '/api/history/restore':
            hf = os.path.join(DONE_DIR, 'history.json')
            hist_list = []
            if os.path.exists(hf):
                with open(hf, 'r', encoding='utf-8') as f:
                    hist_list = json.load(f)
            r = next((it for it in hist_list if it.get('id') == req.get('id')), None)
            if not r: return self.send_json({'success': False, 'error': '없음'})
            r = {k: (normalize_branch_name(v) if isinstance(v, str) else v) for k, v in r.items()}
            r['uploaded_platforms'] = _normalize_uploaded_platforms(r.get('uploaded_platforms'))
            if isinstance(r.get('selected_platforms'), list):
                r['selected_platforms'] = _normalize_uploaded_platforms(r.get('selected_platforms'))
            r['saved_gifs'] = _normalize_saved_gifs(r.get('saved_gifs'))
            r['saved_slots'] = _normalize_saved_slots(r.get('saved_slots'))
            restored_gifs = restore_saved_gifs_to_thumbs(r.get('saved_gifs'))
            restored_slots = restore_saved_slots_to_thumbs(r.get('saved_slots'))
            vn = r.get('video_name')
            video_meta = None
            prev_info = {}
            thumbs = []
            dst = ''
            if vn:
                done_src = os.path.join(DONE_DIR, vn)
                upload_dst = os.path.join(UPLOAD_DIR, vn)
                if os.path.exists(done_src):
                    try:
                        shutil.copy2(done_src, upload_dst)
                        dst = upload_dst
                    except Exception as copy_err:
                        log(f'⚠️ [지난 기록 복원] 영상 복사 실패({copy_err}) → 원본 보관 파일로 폴백합니다.')
                        dst = done_src
                elif os.path.exists(upload_dst):
                    dst = upload_dst
            if dst and os.path.isfile(dst):
                with STATE_LOCK:
                    STATE['video_path'] = dst
                    STATE['video_name'] = vn
                video_meta = probe_video_metadata(dst)
                prev_info = ensure_browser_preview_video(dst)
                thumbs = extract_10_thumbnails(dst, count=6)
            self.send_json({
                'success': True,
                'record': r,
                'video_meta': video_meta,
                'preview_url': prev_info.get('preview_url', ''),
                'preview_transcoded': bool(prev_info.get('transcoded', False)),
                'thumbnails': thumbs,
                'restored_gifs': restored_gifs,
                'restored_slots': restored_slots,
            })
        elif self.path == '/api/ensure_preview':
            with STATE_LOCK:
                state_vn = STATE.get('video_name', '')
                state_vp = STATE.get('video_path', '')
            vn = req.get('video_name') or state_vn
            vp = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
            self.send_json(ensure_browser_preview_video(vp))
        elif self.path == '/api/capture_frame':
            with STATE_LOCK:
                state_vn = STATE.get('video_name', '')
                state_vp = STATE.get('video_path', '')
            vn = req.get('video_name') or state_vn
            vp = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
            self.send_json(capture_frame_at_time(vp, req.get('time_sec', 0.0)))
        elif self.path == '/api/create_gif':
            with STATE_LOCK:
                state_vn = STATE.get('video_name', '')
                state_vp = STATE.get('video_path', '')
            vn = req.get('video_name') or state_vn
            vp = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
            self.send_json(create_gif_from_video(vp, req.get('start_sec', 0.0), req.get('end_sec', 2.5), req.get('speed', 1.0)))
        elif self.path == '/api/transform':
            try:
                with STATE_LOCK:
                    state_vn = STATE.get('video_name', '')
                    state_vp = STATE.get('video_path', '')
                vn = req.get('video_name') or state_vn
                vp = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
                thumbs = extract_10_thumbnails(vp, count=6) if (vp and os.path.exists(vp)) else []
                v_meta = probe_video_metadata(vp) if (vp and os.path.exists(vp)) else None
                prev_info = ensure_browser_preview_video(vp) if (vp and os.path.exists(vp)) else {}
                raw_data_text = str(req.get('data_text') or '')
                used_sample_text = not bool(raw_data_text.strip())
                effective_prof, used_demo_profile = _build_effective_demo_profile()
                effective_text = build_demo_sample_master_text(effective_prof) if used_sample_text else raw_data_text
                converted = convert_master_content(effective_text, vn, profile_override=effective_prof)
                self.send_json({
                    'ok': True,
                    'data': converted,
                    'thumbnails': thumbs,
                    'video_meta': v_meta,
                    'preview_url': prev_info.get('preview_url', ''),
                    'preview_transcoded': bool(prev_info.get('transcoded', False)),
                    'used_sample_text': used_sample_text,
                    'used_demo_profile': used_demo_profile,
                })
            except Exception as e:
                log(f'⚠️ [미리보기 변환 오류] {e}')
                self.send_json({
                    'ok': False,
                    'msg': f'미리보기 변환 중 오류가 발생했습니다: {e}',
                    'thumbnails': []
                })
        elif self.path == '/api/auth_check':
            with STATE_LOCK:
                is_busy = STATE['busy']
            if is_busy: return self.send_json({'ok': False, 'msg': BUSY_TASK_MSG})
            threading.Thread(target=worker_auth_check, args=(req,), daemon=True).start()
            self.send_json({'ok': True})
        elif self.path == '/api/youtube_auth':
            with STATE_LOCK:
                is_busy = bool(STATE['busy'])
            if is_busy:
                return self.send_json({'ok': False, 'msg': BUSY_TASK_MSG})
            migrate_legacy_auth_files()
            secret_path = get_auth_file('client_secret.json')
            if not _is_valid_youtube_client_secret(secret_path):
                return self.send_json({
                    'ok': False,
                    'msg': 'auth_data/client_secret.json 파일이 없거나 유효하지 않습니다. 먼저 1~6단계에 따라 JSON 파일을 auth_data/ 폴더에 넣어주세요.',
                })
            threading.Thread(target=worker_youtube_auth, daemon=True).start()
            self.send_json({
                'ok': True,
                'msg': '브라우저에서 구글 계정 인증 창을 열었습니다. 로그인 후 권한을 허용해 주세요.',
            })
        elif self.path in ('/api/publish', '/api/upload'):
            is_test = bool(req.get('test_mode', False))
            if (not is_test) and (not is_profile_ready(load_profile())):
                return self.send_json({
                    'ok': False,
                    'need_onboarding': True,
                    'msg': '아직 매장 기본 정보가 설정되지 않았습니다. 초기 설정 마법사에서 매장명과 기본 정보를 먼저 입력해 주세요!',
                })
            with STATE_LOCK:
                is_busy = STATE['busy']
                state_vn = STATE['video_name']
            if is_busy: return self.send_json({'ok': False, 'msg': BUSY_TASK_MSG})
            save_history_flag = (not is_test) and bool(req.get('save_history', False))
            resolved_history_id = str(req.get('history_id') or '').strip() if (not is_test) else ''
            rec = None
            if save_history_flag:
                try:
                    vn = req.get('video_name') or state_vn
                    rec = build_history_record(req, video_name=vn)
                    gif_id_seed = resolved_history_id or rec['id']
                    saved_gifs = backup_selected_gifs_to_done(
                        req.get('naver_selected_images'),
                        history_id=gif_id_seed,
                        gif_meta=req.get('naver_gif_meta'),
                    )
                    rec['saved_gifs'] = saved_gifs
                    h_res = save_history_record(rec, update_id=resolved_history_id)
                    if h_res.get('id'):
                        resolved_history_id = str(h_res.get('id'))
                    if h_res.get('updated'):
                        gif_note = f" (GIF {len(saved_gifs)}개 보관)" if saved_gifs else ""
                        log(f'💾 [지난 기록 갱신] 기존 기록을 최종 수정본으로 덮어쓰기 업데이트했습니다.{gif_note}')
                    elif h_res.get('duplicate'):
                        log('ℹ️ [중복 기록 건너뜀] 동일한 제목·본문이 이미 지난 기록에 있어 중복 저장하지 않았습니다.')
                    elif h_res.get('saved') and saved_gifs:
                        log(f'🎞️ [GIF 보관 완료] 생성한 GIF {len(saved_gifs)}개를 done/gifs/에 저장했습니다.')
                except Exception: pass
            req['_save_history'] = save_history_flag
            req['_history_id'] = resolved_history_id
            req['_history_rec'] = rec
            threading.Thread(target=worker_publish, args=(req,), daemon=True).start()
            self.send_json({'ok': True, 'history_id': resolved_history_id, 'test_mode': is_test})
        else:
            self.send_error(404, 'Not Found')

ACTIVE_HTTP_SERVER = None


def _schedule_server_restart(server_ref=None, delay_sec=0.6):
    """원클릭 업데이트 완료 응답을 브라우저에 먼저 전송한 뒤,
    기존 포트(8765)를 안전 해제하고 새 파이썬 서버 프로세스를 독립 실행한 뒤 현재 프로세스를 종료."""
    srv = server_ref or ACTIVE_HTTP_SERVER

    def _do_restart():
        try:
            time.sleep(max(0.2, float(delay_sec)))
            log('🔄 [자동 재시작] 기존 서버 포트를 해제하고 최신 버전 서버를 시작합니다...')
            if srv is not None:
                try:
                    srv.shutdown()
                except Exception:
                    pass
                try:
                    srv.server_close()
                except Exception:
                    pass

            py_exe = sys.executable
            script_path = os.path.abspath(__file__)
            popen_kwargs = {
                'cwd': BASE_DIR,
                'close_fds': True,
                'stdin': subprocess.DEVNULL,
            }
            if sys.platform == 'win32':
                popen_kwargs['creationflags'] = (
                    getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                    | getattr(subprocess, 'DETACHED_PROCESS', 0)
                )
                popen_kwargs['stdout'] = subprocess.DEVNULL
                popen_kwargs['stderr'] = subprocess.DEVNULL
            else:
                popen_kwargs['start_new_session'] = True

            subprocess.Popen([py_exe, script_path], **popen_kwargs)
        except Exception as e:
            try:
                log(f'⚠️ [자동 재시작 오류] {e}')
            except Exception:
                pass
        finally:
            os._exit(0)

    threading.Thread(target=_do_restart, daemon=True).start()


class ReusableServer(ThreadingHTTPServer): allow_reuse_address = True
if __name__ == '__main__':
    clean_and_unify_blackbox_files(clear_thumbs=True)
    srv = None
    last_bind_err = None
    for attempt in range(5):
        try:
            srv = ReusableServer(('127.0.0.1', 8765), AppHandler)
            break
        except OSError as bind_err:
            last_bind_err = bind_err
            if attempt < 4:
                time.sleep(0.5)
    if srv is None:
        raise last_bind_err or RuntimeError('127.0.0.1:8765 포트 바인딩에 실패했습니다.')
    ACTIVE_HTTP_SERVER = srv
    print('🚀 서버 시작 (실시간 세부 로그 & 강제 중지 기능 탑재): http://127.0.0.1:8765', flush=True)
    srv.serve_forever()