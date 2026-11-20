import os, re, sys, json, time, glob, stat, random, shutil, threading, subprocess, datetime, mimetypes
from urllib.parse import unquote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from naver_blog_uploader import upload_to_naver_blog, classify_category
from tiktok_uploader import upload_to_tiktok
from instagram_uploader import upload_to_instagram
from auto_updater import (
    load_release_info,
    check_public_update,
    apply_public_update,
)
from os_utils import (
    get_base_dir,
    get_auth_dir,
    get_blackbox_login_dir,
    get_blackbox_upload_dir,
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
    get_salon_name,
    get_salon_address,
    get_place_search_keyword,
    get_region_keyword,
    get_region_tags,
    get_blog_categories,
    is_profile_ready,
    recommend_blog_category,
)

BASE_DIR = get_base_dir()
AUTH_DIR = get_auth_dir()
BLACKBOX_LOGIN_DIR = get_blackbox_login_dir()
BLACKBOX_UPLOAD_DIR = get_blackbox_upload_dir()
LEGACY_BLACKBOX_DIR = os.path.join(BASE_DIR, 'blackbox')
UPLOAD_DIR = os.path.join(BASE_DIR, 'upload')
DONE_DIR = os.path.join(BASE_DIR, 'done')
BACKUP_DIR = os.path.join(BASE_DIR, '_old_backup')
THUMB_DIR = os.path.join(BASE_DIR, 'temp_thumbs')
DEBUG_DIR = os.path.join(BASE_DIR, 'debug_records')
UI_FILE = os.path.join(BASE_DIR, 'app_ui.html')
migrate_legacy_auth_files()
DAILY_USAGE_PATH = get_auth_file('daily_usage.json')
USAGE_LOCK = threading.Lock()

SAFE_DAILY_LIMITS = {
    'naver': 5,
    'clip': 5,
    'tt': 5,
    'ig': 3,
    'pin': 10,
    'threads': 5,
    'daangn': 2,
    'x': 5,
}
YOUTUBE_POINTS_PER_UPLOAD = 1600
YOUTUBE_MAX_POINTS = 10000
YOUTUBE_MAX_COUNT = 6

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DONE_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)
STATE_LOCK = threading.Lock()
ACTIVE_WORKER_THREAD = None
STATE = {
    'video_path': '',
    'video_name': '',
    'busy': False,
    'stop_requested': False,
    'active_run_id': 0,
    'logs': ['준비 완료! 영상을 업로드하세요.'],
    'last_results': {},
}

class UILogStream:
    """각 플랫폼 업로더(naver, clip, threads 등)의 print 출력을 웹 UI 실시간 로그창으로 자동 중계"""
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
    'app_server.py', 'app_ui.html', 'os_utils.py', 'auto_updater.py', 'AI_RULES.md', 'check_personal_leak.py',
    'build_release.py', '배포판_만들기_및_업로드.command',
    'version.json', 'version.txt', 'LICENSE.txt', 'updater_config.json', '.gitignore', 'planh_uploader.ico',
    '0_처음사용자_안내서.html', '1_최초설치_자동인스톨.bat', '2_프로그램_실행.bat', '.installed_done', '_설치완료_보관함',
    'naver_blog_uploader.py', 'naver_clip_uploader.py', 'tiktok_uploader.py', 'instagram_uploader.py', 'pinterest_uploader.py', 'threads_uploader.py', 'daangn_uploader.py', 'x_uploader.py',
    'app.log',
    'auth_data', 'blackbox_login', 'blackbox_upload', 'upload', 'done', 'debug_records', '_old_backup', 'temp_thumbs', '__pycache__'
}

STYLE_EXPANSION_MAP = {
    '태슬': ['태슬컷', '태슬펌', '칼단발', '단발컷', '여자단발', '단발스타일'],
    '단발': ['단발펌', '단발레이어드컷', '보니펌', '모즈펌', '단발머리', '단발C컬펌'],
    '레이어드': ['레이어드컷', '레이어드펌', '중단발레이어드', '긴머리레이어드', '사이드뱅', 'C컬펌'],
    '허쉬': ['허쉬컷', '허쉬펌', '중단발허쉬컷', '레이어드컷', '샤기컷'],
    '빌드': ['빌드펌', '중단발펌', 'S컬펌', '레이어드펌', '여신앞머리'],
    '엘리자벳': ['엘리자벳펌', '긴머리펌', '굵은웨이브', '여신머리', 'S컬펌'],
    '구름': ['구름펌', '긴머리웨이브', '물결펌', '볼륨펌'],
    '히피': ['히피펌', '젤리펌', '단발히피펌', '긴머리히피펌', '웨이브펌'],
    '볼륨매직': ['볼륨매직', '매직셋팅', '곱슬매직', '복구매직', 'C컬펌'],
    '매직': ['볼륨매직', '매직셋팅', '곱슬매직', 'C컬펌'],
    '다운펌': ['다운펌', '옆머리다운펌', '남자다운펌', '아이롱다운펌', '남자커트'],
    '옆머리': ['다운펌', '옆머리다운펌', '남자다운펌', '남자커트'],
    '뜨는': ['다운펌', '옆머리다운펌', '남자다운펌', '남자머리'],
    '가르마': ['가르마펌', '애즈펌', '남자가르마펌', '시스루가르마', '남자펌'],
    '애즈': ['애즈펌', '가르마펌', '남자애즈펌', '시스루애즈펌', '남자펌'],
    '댄디': ['댄디컷', '시스루댄디컷', '댄디펌', '남자댄디컷', '다운펌'],
    '시스루': ['시스루뱅', '시스루댄디컷', '시스루애즈펌', '앞머리컷'],
    '크롭': ['크롭컷', '남자크롭컷', '스왓컷', '다운펌', '남자짧은머리'],
    '드롭': ['드롭컷', '남자드롭컷', '드롭펌', '다운펌', '남자짧은머리'],
    '아이비리그': ['아이비리그컷', '남자아이비리그컷', '남자짧은머리', '다운펌', '맨즈헤어'],
    '가일': ['가일컷', '가일펌', '남자가일펌', '포마드펌', '남자머리'],
    '리젠트': ['리젠트컷', '리젠트펌', '남자리젠트펌', '다운펌', '남자짧은머리'],
    '쉐도우': ['쉐도우펌', '남자쉐도우펌', '스핀스왈로펌', '남자볼륨펌', '남자펌'],
    '상고': ['상고컷', '남자상고컷', '투블럭상고', '다운펌', '남자커트'],
    '페이드': ['페이드컷', '남자페이드컷', '바버컷', '상고컷', '맨즈헤어'],
    '보브': ['보브컷', '숏단발', '단발컷', '볼륨매직'],
    '염색': ['톤다운염색', '애쉬브라운', '밀크브라운', '여자염색', '퍼스널컬러'],
    '탈색': ['애쉬그레이', '발레아쥬', '솜브레', '탈색염색'],
}

KNOWN_NATURAL_STYLE_TAGS = {
    t for vals in STYLE_EXPANSION_MAP.values() for t in vals
}

AWKWARD_TAG_SUFFIXES = (
    '해결', '고민해결', '완성', '비법', '꿀팁', '꿀팁공유', '고민', '추천해요',
    '추천드려요', '정리해요', '관리법', '스타일링팁', '완벽정리', '방법', '후기추천',
)
AWKWARD_TAG_SUBSTRINGS = (
    '뜨는옆머리', '손질편한', '아침손질', '고데기없이', '그냥말려도', '털어말리',
    '스타일추천', '머리추천', '고민끝', '실패없는', '인생머리',
)

COOKIE_STALE_DAYS = 20

def _check_cookie_status_and_age(cookie_filename):
    """auth_data/ 기준 쿠키 유효성 및 최종 수정일 20일 경과 여부 반환: (state, age_days)"""
    cpath = get_auth_file(cookie_filename)
    if not is_valid_cookie_file(cpath, auto_clean=True):
        return 'red', None
    try:
        elapsed = max(0.0, time.time() - os.path.getmtime(cpath))
        age_days = int(elapsed // 86400)
        if elapsed >= COOKIE_STALE_DAYS * 86400:
            return 'yellow', age_days
        return 'green', age_days
    except Exception:
        return 'green', 0

def get_setup_status():
    """9개 플랫폼 로그인/API 상태 신호등(green/yellow/red) 및 쿠키 경과 일수 반환"""
    migrate_legacy_auth_files()
    secret_path = get_auth_file('client_secret.json')
    token_path = get_auth_file('youtube_token.json')
    has_secret = os.path.exists(secret_path) and os.path.getsize(secret_path) > 10
    has_token = os.path.exists(token_path) and os.path.getsize(token_path) > 10
    if has_secret and has_token:
        yt_state = 'green'
    elif has_secret:
        yt_state = 'yellow'
    else:
        yt_state = 'red'

    naver_state, naver_age = _check_cookie_status_and_age('naver_cookies.json')
    tt_state, tt_age = _check_cookie_status_and_age('tiktok_cookies.json')
    ig_state, ig_age = _check_cookie_status_and_age('instagram_cookies.json')
    pin_state, pin_age = _check_cookie_status_and_age('pinterest_cookies.json')
    th_state, th_age = _check_cookie_status_and_age('threads_cookies.json')
    dg_state, dg_age = _check_cookie_status_and_age('daangn_cookies.json')
    x_state, x_age = _check_cookie_status_and_age('x_cookies.json')

    return {
        'yt': yt_state,
        'naver': naver_state,
        'clip': naver_state,
        'tt': tt_state,
        'ig': ig_state,
        'pin': pin_state,
        'threads': th_state,
        'daangn': dg_state,
        'x': x_state,
        'cookie_ages': {
            'naver': naver_age,
            'clip': naver_age,
            'tt': tt_age,
            'ig': ig_age,
            'pin': pin_age,
            'threads': th_age,
            'daangn': dg_age,
            'x': x_age,
        },
        'has_client_secret': has_secret,
        'has_youtube_token': has_token,
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
        try:
            proc = subprocess.run(
                [
                    'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=width,height,side_data_list:stream_tags=rotate',
                    '-show_entries', 'format=duration',
                    '-of', 'json', video_path,
                ],
                capture_output=True, text=True, timeout=10,
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
    if meta['duration_sec'] > 140 or meta['size_mb'] > 512:
        warnings.append('140초 또는 512MB 초과 · 엑스(X) 업로드 제한 주의')
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
        return {'ok': False, 'msg': '업로드/점검 작업이 진행 중입니다. 먼저 작업을 중지해 주세요.'}

    deleted_files = []
    exact_targets = [
        'youtube_token.json', 'client_secret.json',
        'user_profile.json', 'daily_usage.json', 'login_credentials.json', 'app.log',
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

    for dpath in [AUTH_DIR, BLACKBOX_LOGIN_DIR, BLACKBOX_UPLOAD_DIR, UPLOAD_DIR, DONE_DIR, DEBUG_DIR, THUMB_DIR]:
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

    # 구버전 통합 blackbox 폴더가 남아 있으면 영구 삭제
    if os.path.exists(LEGACY_BLACKBOX_DIR):
        if _force_rmtree(LEGACY_BLACKBOX_DIR):
            deleted_files.append('blackbox/')

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

def force_stop_active_task():
    """진행 중인 자동화 브라우저(Playwright 전용 크롬 창)를 즉시 종료하고 백그라운드 스레드/상태를 깔끔하게 정리"""
    global ACTIVE_WORKER_THREAD
    with STATE_LOCK:
        STATE['stop_requested'] = True
        STATE['active_run_id'] += 1
        worker_t = ACTIVE_WORKER_THREAD

    log('🛑 [강제 중지 요청] 실행 중인 자동화 브라우저를 닫고 작업을 중단합니다...')
    try:
        # Playwright가 띄운 자동화 크롬 창만 타겟팅하여 즉시 종료 (개인 크롬 창은 유지)
        if sys.platform == 'win32':
            ps_cmd = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { ($_.Name -match 'chrome|chromium') -and ($_.CommandLine -like '*--disable-blink-features=AutomationControlled*') } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        else:
            subprocess.run(['pkill', '-f', '--', '--disable-blink-features=AutomationControlled'], stderr=subprocess.DEVNULL)
    except Exception:
        pass

    if worker_t is not None and worker_t.is_alive() and worker_t is not threading.current_thread():
        try:
            worker_t.join(timeout=4.0)
        except Exception:
            pass
    else:
        time.sleep(0.3)

    worker_still_alive = bool(worker_t is not None and worker_t.is_alive() and worker_t is not threading.current_thread())
    cleanup_chrome_profile_locks()
    with STATE_LOCK:
        STATE['busy'] = False
        # 작업 스레드가 아직 완전히 종료되지 않았으면 stop_requested=True를 유지해 잔여 대기 로그 차단
        if not worker_still_alive:
            STATE['stop_requested'] = False
        if ACTIVE_WORKER_THREAD is worker_t:
            ACTIVE_WORKER_THREAD = None

    clean_and_unify_blackbox_files(clear_thumbs=False)
    log('✅ [강제 중지 완료] 자동화 브라우저 종료 및 작업 상태 정리가 완료되었습니다.')
    return {'ok': True, 'msg': '진행 중인 업로드 작업을 강제 중지했습니다.'}

def clean_and_unify_blackbox_files(clear_thumbs=False):
    """1) 구버전 auth/블랙박스 파일 전용 폴더 이관 2) 불필요한 .png 삭제
    3) 로그인용(blackbox_login/)과 업로드용(blackbox_upload/)으로 녹화본 분리 통합
    4) 구버전 blackbox/ 폴더 이관 후 제거 및 debug_records/temp_thumbs 임시 찌꺼기 정리"""
    platforms = ['네이버블로그', '네이버클립', '틱톡', '인스타그램', '핀터레스트', '쓰레드', '당근', '엑스']
    try:
        migrate_legacy_auth_files()
        os.makedirs(BLACKBOX_LOGIN_DIR, exist_ok=True)
        os.makedirs(BLACKBOX_UPLOAD_DIR, exist_ok=True)

        for fn in os.listdir(BASE_DIR):
            if fn.lower().endswith('.png'):
                try: os.remove(os.path.join(BASE_DIR, fn))
                except Exception: pass

        with STATE_LOCK:
            is_busy = STATE.get('busy')

        def _move_if_newer(src_path, dst_path):
            if not os.path.isfile(src_path):
                return
            try:
                if os.path.abspath(src_path) == os.path.abspath(dst_path):
                    return
                if os.path.getsize(src_path) > 0 and (not os.path.exists(dst_path) or os.path.getmtime(src_path) >= os.path.getmtime(dst_path)):
                    shutil.move(src_path, dst_path)
                else:
                    os.remove(src_path)
            except Exception:
                pass

        # 1) 강제종료/중단으로 debug_records/_rec_<플랫폼>_<login|upload>_* 하위 폴더에 남은 .webm 회수
        if not is_busy and os.path.isdir(DEBUG_DIR):
            for entry in list(os.listdir(DEBUG_DIR)):
                sub_p = os.path.join(DEBUG_DIR, entry)
                if not os.path.isdir(sub_p):
                    continue
                matched_p = None
                is_login_rec = ('_login_' in entry) or entry.endswith('_login') or ('로그인' in entry)
                for p in platforms:
                    if f'_{p}_' in entry or p in entry:
                        matched_p = p
                        break
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
                if non_empty and matched_p:
                    newest_w = max(non_empty, key=lambda x: os.path.getmtime(x))
                    if is_login_rec:
                        dst_w = os.path.join(BLACKBOX_LOGIN_DIR, f"블랙박스_{matched_p}_로그인.webm")
                    else:
                        dst_w = os.path.join(BLACKBOX_UPLOAD_DIR, f"블랙박스_{matched_p}.webm")
                    _move_if_newer(newest_w, dst_w)
                _force_rmtree(sub_p)

        # 2) 플랫폼별 업로드용(blackbox_upload/블랙박스_<플랫폼>.webm) 및 로그인용(blackbox_login/블랙박스_<플랫폼>_로그인.webm) 통합
        for p in platforms:
            upload_dst = os.path.join(BLACKBOX_UPLOAD_DIR, f"블랙박스_{p}.webm")
            upload_candidates = [
                os.path.join(BASE_DIR, f"블랙박스_{p}.webm"),
                os.path.join(BASE_DIR, f"블랙박스_{p}_최신.webm"),
                os.path.join(BASE_DIR, f"에러녹화_{p}.webm"),
                os.path.join(LEGACY_BLACKBOX_DIR, f"블랙박스_{p}.webm"),
                os.path.join(LEGACY_BLACKBOX_DIR, f"블랙박스_{p}_최신.webm"),
                os.path.join(LEGACY_BLACKBOX_DIR, f"에러녹화_{p}.webm"),
                os.path.join(BLACKBOX_UPLOAD_DIR, f"블랙박스_{p}_최신.webm"),
                os.path.join(BLACKBOX_UPLOAD_DIR, f"에러녹화_{p}.webm"),
                os.path.join(DEBUG_DIR, f"블랙박스_{p}.webm"),
                os.path.join(DEBUG_DIR, f"블랙박스_{p}_최신.webm"),
                os.path.join(DEBUG_DIR, f"에러녹화_{p}.webm"),
            ]
            existing = [c for c in upload_candidates if os.path.isfile(c)]
            if existing:
                valid_existing = [c for c in existing if os.path.getsize(c) > 0]
                if valid_existing:
                    newest = max(valid_existing, key=lambda x: os.path.getmtime(x))
                    _move_if_newer(newest, upload_dst)
                for c in existing:
                    if os.path.exists(c):
                        try: os.remove(c)
                        except Exception: pass

            login_dst = os.path.join(BLACKBOX_LOGIN_DIR, f"블랙박스_{p}_로그인.webm")
            login_candidates = [
                os.path.join(BASE_DIR, f"블랙박스_{p}_로그인.webm"),
                os.path.join(LEGACY_BLACKBOX_DIR, f"블랙박스_{p}_로그인.webm"),
                os.path.join(BLACKBOX_UPLOAD_DIR, f"블랙박스_{p}_로그인.webm"),
                os.path.join(DEBUG_DIR, f"블랙박스_{p}_로그인.webm"),
            ]
            login_existing = [c for c in login_candidates if os.path.isfile(c)]
            if login_existing:
                valid_login = [c for c in login_existing if os.path.getsize(c) > 0]
                if valid_login:
                    newest_l = max(valid_login, key=lambda x: os.path.getmtime(x))
                    _move_if_newer(newest_l, login_dst)
                for c in login_existing:
                    if os.path.exists(c):
                        try: os.remove(c)
                        except Exception: pass

        # 3) 루트 및 구버전 blackbox/에 남아 있는 기타 블랙박스_*.webm 파일도 성격에 맞게 이관
        for scan_dir in (BASE_DIR, LEGACY_BLACKBOX_DIR):
            if not os.path.isdir(scan_dir):
                continue
            for fn in list(os.listdir(scan_dir)):
                if fn.startswith('블랙박스_') and fn.endswith('.webm'):
                    src = os.path.join(scan_dir, fn)
                    target_dir = BLACKBOX_LOGIN_DIR if ('로그인' in fn or 'login' in fn.lower()) else BLACKBOX_UPLOAD_DIR
                    _move_if_newer(src, os.path.join(target_dir, fn))

        # 4) 이관 완료된 구버전 blackbox/ 폴더 제거
        if not is_busy and os.path.isdir(LEGACY_BLACKBOX_DIR):
            _force_rmtree(LEGACY_BLACKBOX_DIR)

        # 5) 작업 미진행 시 debug_records 잔여 임시 파일 정리 (블랙박스_*.webm은 보존 이관)
        if not is_busy and os.path.exists(DEBUG_DIR):
            for df in list(os.listdir(DEBUG_DIR)):
                fp = os.path.join(DEBUG_DIR, df)
                if os.path.isfile(fp):
                    if df.startswith('블랙박스_') and df.endswith('.webm'):
                        target_dir = BLACKBOX_LOGIN_DIR if ('로그인' in df or 'login' in df.lower()) else BLACKBOX_UPLOAD_DIR
                        _move_if_newer(fp, os.path.join(target_dir, df))
                        continue
                    try: os.remove(fp)
                    except Exception: pass

        if clear_thumbs and not is_busy and os.path.exists(THUMB_DIR):
            for tf in os.listdir(THUMB_DIR):
                fp = os.path.join(THUMB_DIR, tf)
                if os.path.isfile(fp):
                    try: os.remove(fp)
                    except Exception: pass
    except Exception:
        pass

def has_backup_files():
    return os.path.exists(BACKUP_DIR) and len(os.listdir(BACKUP_DIR)) > 0

PROTECTED_CODE_EXTS = ('.py', '.html', '.bat', '.command')
PROTECTED_ROOT_FILES = {
    'version.json', 'version.txt', 'LICENSE.txt', 'updater_config.json', '.gitignore', '.installed_done',
    'AI_RULES.md', 'export_code.py', 'check_personal_leak.py', 'build_release.py', '배포판_만들기_및_업로드.command',
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

def handle_cleanup(action='clean_all'):
    """단일 통합 파일 정리 (clean_all 및 기존 quarantine/purge 호환).
    핵심 코드·설정·개발도구·auth_data·blackbox_login·blackbox_upload·upload·done은 철저히 보호하고
    .png 스냅샷, debug_records, temp_thumbs, _old_backup, 루트 임시 산출물만 안전 정리."""
    with STATE_LOCK:
        is_busy = STATE.get('busy')
    if is_busy:
        msg = '작업이 진행 중입니다. 업로드나 로그인 점검이 끝난 뒤 파일 정리를 실행해 주세요.'
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
    removed_thumbs = 0
    removed_backup = 0

    # 루트 .png 스냅샷 및 임시 산출물 정리 (보호 파일은 절대 제외)
    for fn in list(os.listdir(BASE_DIR)):
        src = os.path.join(BASE_DIR, fn)
        if not os.path.isfile(src):
            continue
        if fn.lower().endswith('.png'):
            try:
                os.remove(src)
                removed_png += 1
            except Exception:
                pass
            continue
        if _is_protected_root_file(fn):
            continue
        if fn in TEMP_ROOT_FILES or fn.lower().endswith(('.tmp', '.bak', '.old')) or not _is_protected_root_file(fn):
            try:
                os.remove(src)
                removed_temp_root.append(fn)
            except Exception:
                pass

    # debug_records / temp_thumbs 내 잔여 파일 수 먼저 집계 후 블랙박스 이관 + 임시 폴더 정리
    if os.path.isdir(DEBUG_DIR):
        removed_debug = sum(
            1 for entry in os.listdir(DEBUG_DIR)
            if not (entry.startswith('블랙박스_') and entry.endswith('.webm'))
        )
    if os.path.isdir(THUMB_DIR):
        removed_thumbs = len(os.listdir(THUMB_DIR))

    # 블랙박스 영상 blackbox_login/·blackbox_upload/ 분리 이관 + debug_records / temp_thumbs 정리
    clean_and_unify_blackbox_files(clear_thumbs=True)

    # 하위 디렉터리가 남아 있는 경우까지 안전 정리
    for target_dir in (DEBUG_DIR, THUMB_DIR):
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

    # _old_backup 구버전 찌꺼기 폴더 안전 영구 삭제
    if os.path.exists(BACKUP_DIR):
        try:
            removed_backup = len(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else 1
        except Exception:
            removed_backup = 1
        _force_rmtree(BACKUP_DIR)

    parts = []
    if removed_png:
        parts.append(f"스냅샷(.png) {removed_png}개")
    if removed_temp_root:
        parts.append(f"임시 파일 {len(removed_temp_root)}개({', '.join(removed_temp_root[:5])}{' 등' if len(removed_temp_root) > 5 else ''})")
    if removed_debug:
        parts.append(f"debug_records {removed_debug}개")
    if removed_thumbs:
        parts.append(f"temp_thumbs {removed_thumbs}개")
    if removed_backup:
        parts.append(f"_old_backup 구버전 백업 {removed_backup}개")

    if parts:
        summary = " · ".join(parts)
        msg = f"🧹 파일 정리 완료! ({summary}) — 핵심 코드·auth_data·blackbox_login·blackbox_upload·upload·done은 안전하게 보호되었습니다."
    else:
        msg = "🧹 이미 깨끗한 상태입니다! (정리할 임시/백업 파일 없음 · 핵심 파일 모두 보호됨)"

    log(msg)
    return {'ok': True, 'msg': msg, 'has_backup': has_backup_files()}

def extract_10_thumbnails(video_path, count=10):
    if not video_path or not os.path.exists(video_path):
        return []
    os.makedirs(THUMB_DIR, exist_ok=True)
    for old_f in os.listdir(THUMB_DIR):
        if old_f.startswith('thumb_') and old_f.endswith('.jpg'):
            try: os.remove(os.path.join(THUMB_DIR, old_f))
            except Exception: pass

    ts = int(time.time())
    thumbnails = []

    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames > 0:
            start_f = int(total_frames * 0.05)
            end_f = int(total_frames * 0.95)
            step = max(1, (end_f - start_f) // max(1, (count - 1)))
            target_frames = [min(total_frames - 1, start_f + i * step) for i in range(count)]
            for idx, f_no in enumerate(target_frames, start=1):
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_no)
                ret, frame = cap.read()
                if ret:
                    fname = f"thumb_{idx:02d}.jpg"
                    fpath = os.path.join(THUMB_DIR, fname)
                    cv2.imwrite(fpath, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    thumbnails.append({'url': f'/thumbs/{fname}?t={ts}', 'path': fpath})
        cap.release()
        if thumbnails:
            log(f"📸 영상에서 블로그용 캡처 {len(thumbnails)}장을 추출했습니다.")
            return thumbnails
    except Exception:
        pass

    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True, timeout=10
        )
        duration = float(probe.stdout.strip() or 0)
        if duration > 0:
            start_t = duration * 0.05
            end_t = duration * 0.95
            step_t = (end_t - start_t) / max(1, (count - 1))
            for i in range(count):
                sec = start_t + i * step_t
                fname = f"thumb_{i+1:02d}.jpg"
                fpath = os.path.join(THUMB_DIR, fname)
                subprocess.run(
                    ['ffmpeg', '-y', '-ss', f"{sec:.2f}", '-i', video_path, '-frames:v', '1', '-q:v', '2', fpath],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
                )
                if os.path.exists(fpath):
                    thumbnails.append({'url': f'/thumbs/{fname}?t={ts}', 'path': fpath})
        if thumbnails:
            log(f"📸 (FFmpeg) 영상에서 블로그용 캡처 {len(thumbnails)}장을 추출했습니다.")
    except Exception as e:
        log(f"⚠️ 영상 캡처 추출 실패: {e}")

    return thumbnails


def capture_frame_at_time(video_path, time_sec):
    """영상의 지정 시점(time_sec)에서 고화질 캡처 JPEG 1장을 추출해 temp_thumbs/에 저장"""
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

    # 1순위: FFmpeg (아이폰 .mov 회전 메타데이터 및 HEVC 코덱 안정 지원)
    try:
        proc = subprocess.run(
            ['ffmpeg', '-y', '-ss', f"{t:.3f}", '-i', video_path, '-frames:v', '1', '-q:v', '2', fpath],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
        )
        if proc.returncode == 0 and os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
            log(f"📸 [장면 캡처] {t:.2f}초 장면 저장 완료: {fname}")
            return {'ok': True, 'url': f'/thumbs/{fname}?t={ts_ms}', 'path': fpath, 'time_sec': round(t, 2), 'kind': 'image'}
    except Exception:
        pass

    # 2순위: OpenCV 폴백
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ret, frame = cap.read()
            cap.release()
            if ret:
                cv2.imwrite(fpath, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    log(f"📸 [장면 캡처(CV2)] {t:.2f}초 장면 저장 완료: {fname}")
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
                'ffmpeg', '-y',
                '-ss', f"{s_t:.3f}",
                '-t', f"{duration:.3f}",
                '-i', video_path,
                '-vf', vf_filter,
                '-loop', '0',
                fpath,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45
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


def _is_natural_search_tag(tag, banned_tags, region_tags=None):
    """어색한 광고형 합성어('뜨는옆머리해결' 등)를 걸러내고 실제 검색·유입용 자연 키워드만 통과"""
    if not tag or len(tag) < 2:
        return False
    low = tag.lower()
    if low in banned_tags:
        return False
    if tag == '손질편한머리' or (region_tags and tag in region_tags):
        return False
    if tag in KNOWN_NATURAL_STYLE_TAGS:
        return True
    if any(tag.endswith(sfx) for sfx in AWKWARD_TAG_SUFFIXES):
        return False
    if any(sub in tag for sub in AWKWARD_TAG_SUBSTRINGS):
        return False
    # 지나치게 긴 문장형 한국어 합성어 차단 (예: 10자 초과 미등록 태그)
    if re.search(r'[가-힣]', tag) and len(tag) > 9:
        return False
    return True


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


def build_smart_tags(raw_tags, full_text, cat, profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    banned_tags = get_effective_banned_tags(prof)
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_tags = []
    seen_low = set()

    def _add_tag(candidate):
        tok = _sanitize_tag_token(candidate)
        if not tok:
            return
        if not _is_natural_search_tag(tok, banned_tags, region_tags=region_set):
            return
        if tok.lower() not in seen_low:
            seen_low.add(tok.lower())
            style_tags.append(tok)

    # 1) 원문 본문/해시태그 텍스트에서 실제 시술 스타일 키워드 우선 매칭
    for t in (raw_tags or []):
        _add_tag(t)

    men_only_keys = {'다운펌', '옆머리', '뜨는', '가르마', '애즈', '댄디', '크롭', '드롭', '아이비리그', '가일', '리젠트', '쉐도우', '상고', '페이드'}
    women_only_keys = {'태슬', '단발', '허쉬', '빌드', '엘리자벳', '구름', '히피', '보브'}
    match_src = ' '.join(style_tags) + ' ' + (full_text or '')
    for key, exp_list in STYLE_EXPANSION_MAP.items():
        if cat == 'Men' and key in women_only_keys:
            continue
        if cat == 'Women' and key in men_only_keys and key not in ('가르마', '애즈'):
            continue
        if key in match_src:
            for item in exp_list:
                _add_tag(item)

    # 2) 부족분은 실제 검색량이 높은 대표 시술명으로 자연스럽게 보충
    promo_pool = (
        ['드롭컷', '크롭컷', '다운펌', '애즈펌', '가르마펌', '댄디컷', '상고컷', '남자커트']
        if cat == 'Men'
        else ['레이어드컷', '태슬컷', '단발펌', '빌드펌', '허쉬컷', '볼륨매직', 'C컬펌', '여자커트']
    )
    for p in promo_pool:
        _add_tag(p)

    max_5_style = max(1, 5 - len(region_tags))
    max_10_style = max(1, 10 - len(region_tags))
    return style_tags, (style_tags[:max_5_style] + region_tags)[:5], (style_tags[:max_10_style] + region_tags)[:10], region_tags

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
    for raw_line in raw.splitlines():
        cleaned = _clean_invisible_line(raw_line)
        if not cleaned:
            if out and not prev_blank:
                out.append('')
                prev_blank = True
            continue

        # 과한 점선 장식(┈┈┈┈┈┈ 등) 제거 (하단 공식 구분선 ━━━ 는 유지)
        if _is_divider_line(cleaned) and not cleaned.startswith('━━━'):
            continue

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
    """타 플랫폼(유튜브/클립/당근 등)용 핵심 문장 추출 시 인스타 커스텀 서명·DM·@아이디 줄을 제외하기 위한 판별기"""
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


def convert_master_content(data_text, video_name='', profile_override=None):
    """순수 파이썬 메인 원고 변환 엔진 (API 키 불필요 · 9개 플랫폼 자동 분배 및 인스타 커스텀 스타일 보호)"""
    nl = chr(10)
    prof = profile_override if isinstance(profile_override, dict) else load_profile()
    salon_name = get_salon_name(prof, fallback='우리 매장')
    salon_addr = get_salon_address(prof)
    region_kw = get_region_keyword(prof)
    region_title_prefix = f"{region_kw} 미용실 " if region_kw else (f"{salon_name} " if salon_name != '우리 매장' else "")
    region_near_label = f"{region_kw} 근처에서" if region_kw else "가까운 곳에서"
    region_neighbor_label = f"{region_kw} 이웃님들께" if region_kw else "우리 동네 이웃님들께"
    daangn_prefix = f"[{region_kw}] " if region_kw else f"[{salon_name}] "

    designer_name = (prof.get('designer_name') or '').strip()
    designer_label = f" {designer_name}" if designer_name else ""
    designer_pipe = f" | {designer_name}" if designer_name else ""

    data_text = normalize_branch_name(data_text or '')
    sections = parse_multi_block_sections(data_text)
    is_multi_block = ('숏폼전용본문' in sections or '블로그전용본문' in sections)
    is_bonmun_format = (not is_multi_block) and any(k in sections for k in ('메인본문', 'SEO마케팅파일명', '보충댓글', '제목3종', '해시태그'))
    seo_filename = sections.get('SEO마케팅파일명', '') or sections.get('파일제목', '')
    short_section_raw = sections.get('숏폼전용본문', '')

    if is_multi_block:
        short_raw = short_section_raw or sections.get('메인본문', '') or sections.get('_unassigned', '') or data_text
        blog_custom_body = sections.get('블로그전용본문', '')
        titles_block = sections.get('제목3종', '')
        tags_block = sections.get('해시태그', '')
        raw_tags = re.findall(r'#([^\s#]+)', tags_block + ' ' + short_raw)
        raw_lines = [_clean_invisible_line(l) for l in short_raw.splitlines() if _clean_invisible_line(l)]
    elif is_bonmun_format:
        short_raw = sections.get('메인본문', '') or sections.get('_unassigned', '')
        blog_custom_body = ''
        titles_block = sections.get('제목3종', '')
        tags_block = sections.get('해시태그', '')
        raw_tags = re.findall(r'#([^\s#]+)', tags_block + ' ' + short_raw)
        raw_lines = [_clean_invisible_line(l) for l in short_raw.splitlines() if _clean_invisible_line(l)]
    else:
        short_raw = data_text
        blog_custom_body = ''
        titles_block = ''
        raw_lines = [_clean_invisible_line(l) for l in data_text.splitlines() if _clean_invisible_line(l)]
        raw_tags = re.findall(r'#([^\s#]+)', data_text)

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

    content_lines = kr_content_lines or all_non_sig_lines or ['차분하고 깔끔하게 정돈된 맞춤 헤어 스타일입니다.']

    seo_hint = re.sub(r'[_\-\.]+', ' ', _clean_invisible_line(seo_filename))
    full_line_text = ' '.join(content_lines)
    combined_hint = f"{full_line_text} {blog_custom_body} {seo_hint} {' '.join(raw_tags)} {video_name or STATE.get('video_name', '')}"
    gender_cat = classify_category(combined_hint)
    user_blog_cats = get_blog_categories(prof)
    recommended_blog_cat = recommend_blog_category(combined_hint, user_blog_cats) if user_blog_cats else ""
    style_kws, tags_5, tags_10, region_tags = build_smart_tags(raw_tags, f"{full_line_text} {blog_custom_body} {seo_hint}", gender_cat, profile=prof)

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
        design_lines = [normalize_branch_name(x) for x in content_lines[:-1]]
    elif len(content_lines) == 2:
        mid_lines = [normalize_branch_name(content_lines[1].strip())]
        care_comment = normalize_branch_name(content_lines[1].strip())
        design_lines = [normalize_branch_name(content_lines[0].strip())]
    else:
        mid_lines = []
        care_comment = "샴푸 후 두피부터 털어 말리고 에센스만 소량 바르면 라인이 잡힙니다."
        design_lines = [normalize_branch_name(content_lines[0].strip())]

    parsed_titles = [
        normalize_branch_name(re.sub(r'^[1-3][\.\)]\s*', '', _clean_invisible_line(l)).strip('"').strip("'"))
        for l in titles_block.splitlines()
        if not _is_noise_label_line(l)
    ]
    parsed_titles = [t for t in parsed_titles if t]
    if parsed_titles:
        hook_line = parsed_titles[0] or hook_line
    naver_title = normalize_branch_name(
        parsed_titles[2] if len(parsed_titles) >= 3 else f"{region_title_prefix}{main_kw}, {desc_phrase}".strip()
    )[:50]

    gender_target = "남성 고객님" if gender_cat == "Men" else "고객님"
    style_core = kr_style_kws[0] if kr_style_kws else main_kw

    if blog_custom_body:
        naver_body = format_mobile_readable(
            ensure_blog_media_placeholders(normalize_branch_name(blog_custom_body)),
            max_line_len=28,
            preserve_media_markers=True,
        )
    else:
        bullet_design = f"{nl}{nl}".join([f"**✔ {line.rstrip('.')}**" for line in design_lines])

        intro_pool = [
            (
                f"안녕하세요.{nl}"
                f"**{salon_name}{designer_label}**입니다.{nl}{nl}"
                f"오늘 소개할 시술은 [[『 {main_kw} 』]]입니다.{nl}"
                f"머리가 조금만 자라도 라인이 쉽게 흐트러져서{nl}"
                f"아침마다 손질이 번거로웠던 {gender_target} 사례예요.{nl}{nl}"
                f"과하게 힘주지 않아도 깔끔하게 떨어지는 실루엣,{nl}"
                f"실제 시술 순서와 관리 포인트까지 한 번에 정리해 드릴게요."
            ),
            (
                f"안녕하세요, **{salon_name}{designer_label}**입니다.{nl}{nl}"
                f"매장에서 꾸준히 문의가 많은 [[『 {main_kw} 』]] 시술 기록입니다.{nl}"
                f"기장을 확 줄이지 않으면서도 인상을 또렷하게 바꾸고 싶을 때{nl}"
                f"가장 만족도가 높은 스타일이기도 해요.{nl}{nl}"
                f"실제 모질에 맞춰 어떻게 라인을 정리했는지{nl}"
                f"시술 전후 사진과 함께 차근차근 보여드릴게요."
            ),
            (
                f"반갑습니다. **{salon_name}{designer_label}**입니다.{nl}{nl}"
                f"오늘은 [[『 {main_kw} 』]] 시술 변화를 가져왔습니다.{nl}"
                f"같은 스타일이라도 모량과 모류에 따라{nl}"
                f"결과 차이가 크게 나는 편이에요.{nl}{nl}"
                f"집에서 말리기만 해도 형태가 유지되도록{nl}"
                f"어디에 중점을 두고 작업했는지 담백하게 공유해 드릴게요."
            ),
            (
                f"안녕하세요. **{salon_name}{designer_label}**입니다.{nl}{nl}"
                f"거울 볼 때마다 답답해 보이던 라인을 정리하러 오신{nl}"
                f"{gender_target}의 [[『 {main_kw} 』]] 시술입니다.{nl}{nl}"
                f"샵에서 드라이했을 때만 예쁜 머리가 아니라{nl}"
                f"평소 일상에서도 편하게 유지되는 방향에 집중했어요.{nl}"
                f"상담부터 마무리까지 핵심 포인트만 짚어드릴게요."
            ),
            (
                f"안녕하세요, **{salon_name}{designer_label}**입니다.{nl}{nl}"
                f"요즘 [[『 {main_kw} 』]] 상담이 부쩍 늘었습니다.{nl}"
                f"특히 부스스함이나 애매한 기장감 때문에{nl}"
                f"정돈된 실루엣을 원하시는 분들이 많아요.{nl}{nl}"
                f"오늘 시술에서 실제로 손본 디테일과{nl}"
                f"집에서 바로 따라 할 수 있는 손질 팁까지 함께 적어둘게요."
            ),
        ]

        problem_pool = [
            (
                f"[[🔎 시술 전 고민 체크]]{nl}{nl}"
                f"방문 당시 가장 불편하셨던 부분은 크게 두 가지였습니다.{nl}"
                f"먼저 끝선이 고르게 모이지 않고 자꾸 바깥으로 뻗치는 점,{nl}"
                f"그리고 시간이 지날수록 무게감이 한쪽으로 몰리는 점이었어요.{nl}{nl}"
                f"이런 경우 겉 길이만 다듬으면 며칠 지나 다시 부해지기 쉽습니다.{nl}"
                f"특히 샴푸 후 말렸을 때 옆이 뜨거나 아래가 뭉치는 현상이 반복되죠.{nl}{nl}"
                f"그래서 이번 시술은 **안쪽 무게 분포와 외곽 라인을 먼저 정리**하는 데 집중했습니다.{nl}"
                f"바탕이 정돈되어야 전체 실루엣이 깔끔하게 잡힙니다."
            ),
            (
                f"[[🔎 방문 전 모발 상태 진단]]{nl}{nl}"
                f"처음 상담할 때 모류 방향과 숱 분포부터 꼼꼼히 확인했습니다.{nl}"
                f"평소 드라이를 오래 해도 원하는 형태가 금방 풀리고,{nl}"
                f"옆라인이 어정쩡하게 뜨거나 무거워 보이는 고민이 있으셨어요.{nl}{nl}"
                f"모발 굵기와 곱슬 정도를 함께 체크해 보니{nl}"
                f"무게가 쌓이는 높이가 얼굴형과 잘 맞지 않는 상태였습니다.{nl}{nl}"
                f"**불필요한 부피만 덜어내는 베이스 커트**부터 차분하게 잡고 들어가{nl}"
                f"자연스럽게 떨어지는 흐름을 먼저 만들어 드렸어요."
            ),
            (
                f"[[🔎 왜 손질이 어려웠을까?]]{nl}{nl}"
                f"머리가 잘 안 잡히는 이유는 대부분 숱보다 '무게 위치' 때문입니다.{nl}"
                f"무게가 아래나 옆에 과하게 쌓여 있으면{nl}"
                f"아무리 드라이를 해도 라인이 무겁고 답답해 보이죠.{nl}{nl}"
                f"반대로 숱을 너무 많이 치면 끝이 날리고 부스스해집니다.{nl}"
                f"적정 양감을 남기면서 뭉치는 구간만 정확히 빼주는 게 핵심이에요.{nl}{nl}"
                f"그래서 이번 {style_core} 작업에서는{nl}"
                f"**두상 굴곡에 맞춰 층과 질감을 새로 배치**하는 데 가장 신경 썼습니다."
            ),
            (
                f"[[🔎 시술 전 체크 포인트]]{nl}{nl}"
                f"사진으로 보던 느낌을 그대로 내기 어려운 이유는{nl}"
                f"사람마다 모발 굵기와 가마 방향이 다르기 때문입니다.{nl}{nl}"
                f"이번 {gender_target}도 평소 가르마 습관과 곱슬 정도를 먼저 체크했어요.{nl}"
                f"억지로 꺾어 누르는 방식은 며칠만 지나도 금방 원래대로 돌아옵니다.{nl}{nl}"
                f"그래서 **모류가 흐르는 방향을 살리면서 외곽만 단정하게 정리**하는 쪽으로{nl}"
                f"부담 없이 유지되는 설계를 잡았습니다."
            ),
            (
                f"[[🔎 상담에서 확인한 핵심 고민]]{nl}{nl}"
                f"바쁜 아침에 고데기나 롤빗을 오래 쓰기 부담스럽고,{nl}"
                f"그냥 말렸을 때도 단정한 인상을 원하셨습니다.{nl}{nl}"
                f"시술 전 모발 끝 손상도와 전체 밸런스를 함께 살펴보니{nl}"
                f"길이를 많이 자르지 않아도 라인만 정돈하면 분위기가 확 달라질 상태였어요.{nl}{nl}"
                f"**외곽선 정돈과 결 정리의 균형**을 맞추는 방향으로{nl}"
                f"깔끔하고 실용적인 {style_core}을 진행했습니다."
            ),
        ]

        detail_pool = [
            (
                f"[[📐 모질·두상별 디테일 조절]]{nl}{nl}"
                f"같은 **{main_kw}**라도 모질에 따라 작업 방식이 달라집니다.{nl}{nl}"
                f"• **모량이 많고 두꺼운 편이라면?**{nl}"
                f"안쪽 양감을 과하지 않게 덜어내 답답함을 줄이고,{nl}"
                f"겉면은 매끈하게 남겨 부스스함을 막아줍니다.{nl}{nl}"
                f"• **모발이 가늘고 볼륨이 쉽게 가라앉는다면?**{nl}"
                f"끝선 두께감을 살려 날리지 않게 잡아주고,{nl}"
                f"필요한 지점에만 가벼운 결을 넣어 형태 유지력을 높입니다.{nl}{nl}"
                f"• **옆머리나 뒷볼륨이 신경 쓰인다면?**{nl}"
                f"뜨는 구간은 차분히 눌러주고, 꺼지는 구간은 입체감을 살려 비율을 맞춥니다.{nl}{nl}"
                f"이 과정을 거쳐야 시술 직후뿐 아니라{nl}"
                f"한 달쯤 지나 머리가 자라면서도 라인이 덜 무너집니다."
            ),
            (
                f"[[📐 얼굴형과 두상에 맞춘 밸런스]]{nl}{nl}"
                f"**{main_kw}**은 몇 밀리 차이로 분위기가 확 달라집니다.{nl}{nl}"
                f"• **옆라인과 턱선 커버가 필요하다면?**{nl}"
                f"얼굴 외곽을 따라 부드럽게 연결되는 길이를 남겨{nl}"
                f"정면과 측면 모두 자연스럽게 이어지도록 맞춥니다.{nl}{nl}"
                f"• **볼륨이 꺼지거나 반대로 뜨는 구간이 있다면?**{nl}"
                f"뜨는 부분은 차분히 눌러주고, 살아야 할 포인트만 살려{nl}"
                f"전체 비율이 또렷해 보이게 정리합니다.{nl}{nl}"
                f"• **평소 앞머리·가르마 흐름까지 고려**{nl}"
                f"억지 고정 없이 손으로 넘겨도 자연스럽게 연결되도록 다듬었습니다.{nl}{nl}"
                f"눈에 띄는 과장보다 깔끔한 비율에 집중했습니다."
            ),
            (
                f"[[📐 커트 후 질감 정리 포인트]]{nl}{nl}"
                f"베이스 라인을 잡은 뒤 마른 상태에서 한 번 더 확인합니다.{nl}"
                f"젖어 있을 때와 말랐을 때 떨어지는 결이 다르기 때문이에요.{nl}{nl}"
                f"• **뻗치기 쉬운 모발 끝**은 질감을 가볍게 정돈해{nl}"
                f"자연스럽게 원하는 방향으로 모이게 합니다.{nl}{nl}"
                f"• **가르마와 앞머리 연결 구간**은 끊겨 보이지 않도록{nl}"
                f"부드럽게 이어 주어 손빗질만 해도 정리되게 맞췄습니다.{nl}{nl}"
                f"• **뒤통수 볼륨과 목선 라인**도 거울로 함께 체크하며{nl}"
                f"어느 각도에서 봐도 깔끔하게 떨어지도록 마무리했어요."
            ),
            (
                f"[[📐 오래가는 형태감을 위한 기준]]{nl}{nl}"
                f"시술할 때 가장 중요하게 보는 건 '3주 뒤 모습'입니다.{nl}{nl}"
                f"• **숱이 많은 모발**은 안쪽 공간을 적절히 비워{nl}"
                f"자라나도 옆이 뭉치지 않게 설계합니다.{nl}{nl}"
                f"• **힘없는 모발**은 층을 과하게 내지 않고{nl}"
                f"실루엣이 탄탄하게 유지되는 선에서 무게를 조절합니다.{nl}{nl}"
                f"• **모류 방향이 강한 부분**은 커트 각도를 세밀하게 맞춰{nl}"
                f"말리기만 해도 들뜨지 않고 차분히 안착되게 잡았습니다.{nl}{nl}"
                f"내 모질 조건에 딱 맞춘 덕분에 단정한 느낌이 오래 이어집니다."
            ),
            (
                f"[[📐 1:1 맞춤 디자인 체크]]{nl}{nl}"
                f"평소 자주 입는 옷 스타일과 출근 준비 시간까지 고려해{nl}"
                f"**{main_kw}** 기장과 질감을 조율했습니다.{nl}{nl}"
                f"• **뜨거나 부해 보이는 부분**은 깔끔하게 눌러 정리하고,{nl}"
                f"• **부족한 입체감**은 자연스러운 결로 채워 넣었습니다.{nl}"
                f"• **모발 끝선**은 가볍게 흩날리지 않게 밀도를 맞춰 정돈했어요.{nl}{nl}"
                f"정면은 물론 옆모습, 뒷모습까지 군더더기 없이 떨어져{nl}"
                f"어느 각도에서 봐도 깔끔하게 정리된 모습입니다."
            ),
        ]

        qa_pool = [
            (
                f"[[💬 자주 묻는 질문 Q&A]]{nl}{nl}"
                f"**Q. 시술 후 유지 기간은 보통 어느 정도인가요?**{nl}"
                f"A. 모발 자라는 속도에 따라 다르지만, 보통 4~6주 정도 깔끔한 형태가 유지됩니다.{nl}"
                f"그 이후에는 라인만 가볍게 다듬어 주시면 처음 실루엣을 오래 가져갈 수 있어요.{nl}{nl}"
                f"**Q. 커트만 해도 될까요, 펌을 함께 하는 게 좋을까요?**{nl}"
                f"A. 직모·반곱슬·모발 굵기에 따라 달라집니다.{nl}"
                f"방문 시 모질 상태를 직접 확인한 뒤 꼭 필요한 시술만 제안해 드립니다."
            ),
            (
                f"[[💬 방문 전 자주 물어보시는 부분]]{nl}{nl}"
                f"**Q. 샵에서 한 것처럼 집에서도 손질이 잘 될까요?**{nl}"
                f"A. 애초에 말리기만 해도 떨어지도록 라인을 잡기 때문에 부담이 적습니다.{nl}"
                f"마무리할 때 본인 모질에 맞는 드라이 방향도 바로 알려드려요.{nl}{nl}"
                f"**Q. 손상모나 염색 모발도 진행할 수 있나요?**{nl}"
                f"A. 현재 손상도에 맞춰 약제와 질감 처리 강도를 조절해 진행합니다.{nl}"
                f"무리한 시술은 권하지 않으니 편하게 상담부터 받아보셔도 좋습니다."
            ),
            (
                f"[[💬 상담 시 많이 나오는 질문]]{nl}{nl}"
                f"**Q. 기장이 애매한데 지금 방문해도 될까요?**{nl}"
                f"A. 오히려 기장이 어정쩡할 때 라인을 한 번 정리해 두면 기르기도 훨씬 편합니다.{nl}"
                f"목표 길이에 맞춰 불필요한 무게만 덜어내는 방식으로 진행해 드려요.{nl}{nl}"
                f"**Q. 예약 시 따로 준비할 게 있나요?**{nl}"
                f"A. 원하는 분위기의 참고 사진 1~2장 정도만 가져오셔도 충분합니다.{nl}"
                f"두상과 얼굴형에 어울리게 비율을 맞춰 추천해 드립니다."
            ),
            (
                f"[[💬 시술 전 궁금한 점 정리]]{nl}{nl}"
                f"**Q. 아침 스타일링 시간이 얼마나 걸리나요?**{nl}"
                f"A. 샴푸 후 타월 드라이를 충분히 하시면 보통 3~5분 안에 정리됩니다.{nl}"
                f"고데기를 꼭 쓰지 않아도 기본 윤곽이 살아나 출근 준비가 한결 편해요.{nl}{nl}"
                f"**Q. 다음 방문 주기는 언제가 적당한가요?**{nl}"
                f"A. 깔끔한 라인을 유지하시려면 한 달~한 달 반 사이 방문을 추천합니다.{nl}"
                f"자란 부분만 정리해도 전체 완성도가 다시 살아납니다."
            ),
            (
                f"[[💬 고객님들이 자주 묻는 체크포인트]]{nl}{nl}"
                f"**Q. 숱이 많거나 곱슬이 있어도 깔끔하게 나올까요?**{nl}"
                f"A. 안쪽 양감 조절과 필요 시 볼륨매직·다운펌을 조합하면 차분하게 정돈됩니다.{nl}"
                f"부해지는 원인만 정확히 잡아주면 오히려 형태가 잘 유지되는 편이에요.{nl}{nl}"
                f"**Q. 시술 시간은 대략 얼마나 걸리나요?**{nl}"
                f"A. 커트 기준 약 40~50분, 펌 병행 시 모질과 기장에 따라 1시간 30분~2시간 30분 정도 소요됩니다."
            ),
        ]

        recommend_pool = [
            (
                f"[[✅ 이런 분들께 잘 어울려요]]{nl}{nl}"
                f"1) 아침마다 드라이 시간을 줄이고 싶으신 분{nl}"
                f"2) 과한 변화보다 깔끔하고 정돈된 인상을 원하시는 분{nl}"
                f"3) 머리가 자라면서도 라인이 덜 무너지는 {style_core}을 찾으시는 분{nl}{nl}"
                f"현재 기장이나 손상도에 따라 시술 조합은 유연하게 바뀔 수 있어요.{nl}"
                f"방문 시 모발 상태를 직접 보고 가장 현실적인 방법을 안내해 드립니다."
            ),
            (
                f"[[✅ 추천드리는 모질 & 고민]]{nl}{nl}"
                f"1) 끝선이 자꾸 뻗치거나 옆라인이 지저분해 고민이신 분{nl}"
                f"2) 롤빗이나 고데기 없이 가볍게 말려 손질하고 싶으신 분{nl}"
                f"3) 얼굴형에 자연스럽게 어울리는 {style_core} 비율을 찾으시는 분{nl}{nl}"
                f"모질마다 필요한 시술이 다르기 때문에{nl}"
                f"불필요한 시술은 빼고 꼭 필요한 과정만 추천해 드리고 있습니다."
            ),
            (
                f"[[✅ 상담 전 체크해 보시면 좋아요]]{nl}{nl}"
                f"1) 평소 가르마 방향이나 자주 묶는 습관이 있는지{nl}"
                f"2) 최근 6개월 이내 염색·펌·매직 이력이 있는지{nl}"
                f"3) 아침에 드라이에 쓸 수 있는 시간이 어느 정도인지{nl}{nl}"
                f"이 세 가지만 편하게 말씀해 주셔도{nl}"
                f"**{main_kw}** 방향을 훨씬 정확하게 잡아드릴 수 있습니다."
            ),
            (
                f"[[✅ 이런 분께 특히 만족도가 높아요]]{nl}{nl}"
                f"1) 기장 변화는 크지 않게, 분위기만 산뜻하게 바꾸고 싶은 분{nl}"
                f"2) 샵에서 한 날만 예쁜 머리 말고 평소 유지력을 중요하게 보시는 분{nl}"
                f"3) {region_near_label} 꼼꼼하게 1:1 상담받고 싶으신 분{nl}{nl}"
                f"사진 한두 장만 가져오셔도 모질에 맞게 조율해 진행해 드립니다."
            ),
            (
                f"[[✅ 시술 후 달라지는 일상]]{nl}{nl}"
                f"1) 샴푸 후 말리는 시간이 확실히 줄어듭니다.{nl}"
                f"2) 별도 스타일링 없이도 외곽 실루엣이 깔끔하게 잡힙니다.{nl}"
                f"3) 출근·외출 전 거울 앞에서 손이 덜 가는 편한 머리가 됩니다.{nl}{nl}"
                f"화려한 연출보다 매일 쓰기 편한 실용적인 {style_core}을 지향합니다."
            ),
        ]

        care_closing_pool = [
            (
                f"[[💨 집에서 3분 손질법]]{nl}{nl}"
                f"[[💬 \"{care_comment.rstrip('.')}\"]]{nl}{nl}"
                f"1️⃣ 타월로 물기를 꼼꼼히 닦아 드라이 시간을 줄여주세요.{nl}"
                f"2️⃣ 바람을 위에서 아래로 주며 두피부터 가볍게 털어 말립니다.{nl}"
                f"3️⃣ 80% 이상 마르면 모발 끝 위주로 **에센스나 오일**을 소량만 발라 마무리해요.{nl}{nl}"
                f"[미디어 자료6]{nl}{nl}"
                f"[[『 {main_kw} 』]] 스타일이 내 모질에 맞을지 고민되신다면,{nl}"
                f"**{salon_name}{designer_label}**에서 편하게 상담부터 받아보세요.{nl}"
                f"읽어주셔서 감사합니다."
            ),
            (
                f"[[💨 데일리 드라이 & 관리 팁]]{nl}{nl}"
                f"[[💬 \"{care_comment.rstrip('.')}\"]]{nl}{nl}"
                f"• 젖은 상태에서 굵은 빗이나 손가락으로 결을 한 번 정리해 주세요.{nl}"
                f"• 두피 뿌리부터 먼저 말린 뒤 모류 방향으로 자연스럽게 넘겨줍니다.{nl}"
                f"• 마지막에 **가벼운 에센스나 컬 크림**을 소량 터치하면 끝입니다.{nl}{nl}"
                f"[미디어 자료6]{nl}{nl}"
                f"나에게 어울리는 [[『 {main_kw} 』]] 기장과 디테일이 궁금하시다면{nl}"
                f"**{salon_name}{designer_label}**으로 언제든 문의 남겨주세요.{nl}"
                f"오늘도 깔끔한 하루 보내시길 바랍니다."
            ),
            (
                f"[[💨 아침 준비 줄이는 손질 가이드]]{nl}{nl}"
                f"[[💬 \"{care_comment.rstrip('.')}\"]]{nl}{nl}"
                f"① 샴푸 후 물기를 충분히 제거한 상태에서 시작하세요.{nl}"
                f"② 뜨거운 바람보다 중온 바람으로 두피→모발 끝 순서로 말려줍니다.{nl}"
                f"③ 건조한 끝부분에만 **헤어 오일 한 방울** 가볍게 발라 정돈해요.{nl}{nl}"
                f"[미디어 자료6]{nl}{nl}"
                f"과하지 않고 깔끔한 [[『 {main_kw} 』]] 변화를 원하신다면{nl}"
                f"**{salon_name}{designer_label}**에서 1:1 맞춤으로 도와드릴게요.{nl}"
                f"예약 및 문의는 아래 링크를 참고해 주세요."
            ),
            (
                f"[[💨 유지력 높이는 홈케어 포인트]]{nl}{nl}"
                f"[[💬 \"{care_comment.rstrip('.')}\"]]{nl}{nl}"
                f"1) 두피부터 완전히 말려야 볼륨이 오래 유지됩니다.{nl}"
                f"2) 말릴 때 손빗질로 결을 가볍게 쓸어내리면 부스스함이 줄어듭니다.{nl}"
                f"3) 마무리 단계에 **에센스 또는 가벼운 스타일링 제품**만 살짝 더해 주세요.{nl}{nl}"
                f"[미디어 자료6]{nl}{nl}"
                f"지금까지 **{salon_name}{designer_label}**의{nl}"
                f"[[『 {main_kw} 』]] 시술 기록이었습니다.{nl}"
                f"궁금한 점은 아래 예약·상담 링크로 편하게 남겨주세요."
            ),
            (
                f"[[💨 간단하게 끝내는 마무리 손질]]{nl}{nl}"
                f"[[💬 \"{care_comment.rstrip('.')}\"]]{nl}{nl}"
                f"✔ 물기를 충분히 털어낸 뒤 모근부터 건조해 주세요.{nl}"
                f"✔ 전체가 거의 마르면 원하는 방향으로 가볍게 손질해 라인을 잡습니다.{nl}"
                f"✔ 마지막으로 **윤기 에센스**를 소량 바르면 결이 깔끔하게 정리됩니다.{nl}{nl}"
                f"[미디어 자료6]{nl}{nl}"
                f"본인의 모질과 두상에 딱 맞는 [[『 {main_kw} 』]] 디자인,{nl}"
                f"**{salon_name}{designer_label}**에서 부담 없이 상담해 보세요.{nl}"
                f"감사합니다."
            ),
        ]

        aftercare_pool = [
            (
                f"[[🗓️ 시술 후 1주차 · 4주차 변화 & 관리 팁]]{nl}{nl}"
                f"• **1주차**: 갓 정리한 라인이 가장 또렷한 시기입니다.{nl}"
                f"샴푸 후 젖은 채로 오래 두지 말고 두피부터 가볍게 말려 주세요.{nl}"
                f"• **3~4주차**: 모발이 1cm가량 자라며 옆·뒷볼륨 위치가 조금씩 내려옵니다.{nl}"
                f"이때 드라이 바람 방향만 모근 쪽으로 잡아 주면 형태가 오래 유지돼요.{nl}"
                f"• **추천 리터치**: 깔끔한 윤곽을 유지하려면 4~6주 뒤 라인 정리만 받아도 충분합니다."
            ),
            (
                f"[[🗓️ 집에서도 오래 유지하는 실전 포인트]]{nl}{nl}"
                f"• **샴푸 직후**: 수건으로 비비지 말고 꾹꾹 눌러 물기를 뺀 뒤 말리세요.{nl}"
                f"큐티클 손상이 줄어들어 부스스함 없이 결이 매끈하게 떨어집니다.{nl}"
                f"• **외출 전 1분 체크**: 습한 날에는 에센스를 모발 끝에만 얇게 발라 주면{nl}"
                f"잔머리가 덜 뜨고 처음 잡은 {style_core} 라인이 저녁까지 유지됩니다.{nl}"
                f"• **다음 관리**: 한 달 반 전후로 무게감만 가볍게 정리하면 첫 느낌이 살아납니다."
            ),
            (
                f"[[🗓️ 기간별 유지력 & 리터치 안내]]{nl}{nl}"
                f"• **초반 2주**: 별도 손질 없이 털어 말리기만 해도 실루엣이 바로 잡힙니다.{nl}"
                f"아침 준비 시간이 눈에 띄게 줄어드는 걸 느끼실 거예요.{nl}"
                f"• **한 달 이후**: 자라난 모근 무게가 쌓이면 옆라인이 살짝 무거워질 수 있습니다.{nl}"
                f"가벼운 커트 리터치로 무게 위치만 다시 올려 주면 새 머리처럼 정돈돼요.{nl}"
                f"• **제품 선택**: 무거운 왁스나 오일보다 산뜻한 로션·에센스 타입을 권해드립니다."
            ),
            (
                f"[[🗓️ 일상 속 손질 난이도 줄이는 법]]{nl}{nl}"
                f"• **잠들기 전**: 머리가 덜 마른 상태로 자면 아침에 눌림과 뻗침이 심해집니다.{nl}"
                f"두피와 옆라인만이라도 90% 이상 말리고 주무시면 다음 날 훨씬 편해요.{nl}"
                f"• **드라이 순서**: 뒤통수 → 옆라인 → 앞머리 순서로 말리면 비율이 잘 잡힙니다.{nl}"
                f"• **재방문 주기**: 모발 성장 속도에 맞춰 4~6주 간격으로 정리하시면{nl}"
                f"기르는 과정에서도 지저분한 구간 없이 깔끔하게 넘길 수 있습니다."
            ),
            (
                f"[[🗓️ 계절·습도별 간단 유지 팁]]{nl}{nl}"
                f"• **건조한 날**: 정전기가 올라오면 끝선이 날려 보이기 쉬우니{nl}"
                f"손바닥에 에센스를 소량 펴 발라 겉결만 가볍게 쓸어내려 주세요.{nl}"
                f"• **비 오는 습한 날**: 열기구 온도를 높이기보다 찬바람으로 마지막 10초만 식혀 주면{nl}"
                f"부해짐이 덜하고 정돈된 라인이 오래갑니다.{nl}"
                f"• **유지 리터치**: 5주 안팎으로 안쪽 양감만 정리해 주면 만족도가 꾸준히 이어집니다."
            ),
        ]

        picked_intro = random.choice(intro_pool)
        picked_problem = random.choice(problem_pool)
        picked_detail = random.choice(detail_pool)
        picked_qa = random.choice(qa_pool)
        picked_recommend = random.choice(recommend_pool)
        picked_aftercare = random.choice(aftercare_pool)
        picked_care = random.choice(care_closing_pool)

        naver_body = format_mobile_readable(
            normalize_branch_name(
                f"{picked_intro}{nl}{nl}"
                f"[미디어 자료1]{nl}{nl}"
                f"{picked_problem}{nl}{nl}"
                f"[미디어 자료2]{nl}{nl}"
                f"[[✂️ 오늘의 시술 포인트]]{nl}{nl}"
                f"{bullet_design}{nl}{nl}"
                f"라인과 질감의 균형을 먼저 잡아두면{nl}"
                f"별도 스타일링 없이도 정돈된 실루엣이 살아납니다.{nl}{nl}"
                f"[미디어 자료3]{nl}{nl}"
                f"{picked_detail}{nl}{nl}"
                f"[미디어 자료4]{nl}{nl}"
                f"{picked_recommend}{nl}{nl}"
                f"{picked_qa}{nl}{nl}"
                f"[미디어 자료5]{nl}{nl}"
                f"{picked_aftercare}{nl}{nl}"
                f"{picked_care}"
            ),
            max_line_len=28,
            preserve_media_markers=True,
        )

    # 플랫폼별 인기 글쓰기 틀 적용 (제목 & 본문 · 모바일 호흡 줄바꿈)
    yt_title_cand = f"{hook_line} | {style_core}" if style_core not in hook_line else hook_line
    yt_title = normalize_branch_name(yt_title_cand if len(yt_title_cand) <= 55 else hook_line[:55])
    yt_points = [f"• {l}" for l in content_lines[1:3]] if len(content_lines) > 1 else [f"• {main_kw} 맞춤 디자인"]
    yt_body = format_mobile_readable(
        normalize_branch_name(
            f"{hook_line}{nl}{nl}"
            f"{nl.join(yt_points)}{nl}"
            f"• 손질 팁: {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}"
        ),
        max_line_len=28,
        preserve_media_markers=False,
    )

    clip_title_cand = hook_line if style_core in hook_line else f"{style_core} | {hook_line}"
    clip_title = normalize_branch_name(clip_title_cand[:38].rstrip(' |,'))
    clip_mid = nl.join([f"✔ {l}" for l in mid_lines[:2]]) if mid_lines else f"✔ {main_kw} 맞춤 시술"
    clip_body = format_mobile_readable(
        normalize_branch_name(
            f"{hook_line}{nl}{nl}"
            f"{clip_mid}{nl}"
            f"💡 {care_comment}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}{nl}"
            f"아래 장소 태그에서 바로 예약 가능해요."
        ),
        max_line_len=26,
        preserve_media_markers=False,
    )

    tt_title = normalize_branch_name(hook_line[:45])
    if len(content_lines) >= 3:
        tt_raw = f"{mid_lines[0]}{nl}💡 {care_comment}"
    elif len(content_lines) == 2:
        tt_raw = f"{content_lines[1]}"
    else:
        tt_raw = f"💡 {care_comment}"
    tt_body = format_mobile_readable(
        normalize_branch_name(tt_raw),
        max_line_len=26,
        preserve_media_markers=False,
    )

    ig_preserved = _prepare_instagram_preserved_body(short_section_raw if short_section_raw else short_raw)
    ig_has_custom_footer = _has_custom_ig_footer(ig_preserved, salon_name=salon_name, designer_name=designer_name)
    ig_has_english = _has_english_copy(ig_preserved)

    if ig_preserved and (bool(short_section_raw) or ig_has_custom_footer or ig_has_english):
        if ig_has_custom_footer:
            ig_body = normalize_branch_name(ig_preserved)
        else:
            ig_body = normalize_branch_name(
                f"{ig_preserved}{nl}{nl}"
                f"📌 저장해두고 상담 때 보여주세요{nl}"
                f"📍 {salon_name}{designer_pipe}"
            )
    else:
        ig_mid = nl.join([f"✔ {l}" for l in mid_lines[:2]]) if mid_lines else f"✔ {main_kw} 맞춤 정리"
        ig_base = (
            f"{hook_line}{nl}{nl}"
            f"{ig_mid}{nl}"
            f"💡 {care_comment}"
        )
        if not _has_custom_ig_footer(ig_base, salon_name=salon_name, designer_name=designer_name):
            ig_base = (
                f"{ig_base}{nl}{nl}"
                f"📌 저장해두고 상담 때 보여주세요{nl}"
                f"📍 {salon_name}{designer_pipe}"
            )
        ig_body = format_mobile_readable(
            normalize_branch_name(ig_base),
            max_line_len=24,
            preserve_media_markers=False,
        )

    pin_title = normalize_branch_name(f"{main_kw}, {desc_phrase} | {salon_name}"[:52])
    pin_summary = f"{hook_line}. {care_comment}".strip()
    pin_body = normalize_branch_name(
        f"[{salon_name}{designer_label}] {pin_summary} "
        f"예약·상담은 아래 링크에서 확인해 주세요."
    )[:220]

    fallback_region_tag = region_tags[0] if region_tags else "헤어스타일"
    threads_top_tag = _sanitize_tag_token(kr_style_kws[0] if kr_style_kws else fallback_region_tag)
    threads_second = mid_lines[0] if mid_lines else care_comment
    threads_q_pool = [
        f"아침에 머리 손질할 때 제일 신경 쓰이는 부분 있으신가요?",
        f"비슷한 모질 고민 있으시면 댓글로 편하게 물어보세요!",
        f"요즘 {style_core} 고민 중이셨다면 참고해 보셔도 좋아요. 궁금한 점 남겨주세요!",
        f"평소 드라이하는 데 보통 몇 분 정도 걸리시나요?",
    ]
    threads_q = random.choice(threads_q_pool)
    threads_body = format_mobile_readable(
        normalize_branch_name(
            f"{hook_line}{nl}{nl}"
            f"{threads_second}{nl}"
            f"{care_comment}{nl}{nl}"
            f"{threads_q}{nl}{nl}"
            f"📍 {salon_name}{designer_pipe}"
        ),
        max_line_len=24,
        preserve_media_markers=False,
    )
    threads_tags_str = format_clean_tags([threads_top_tag])

    daangn_title_raw = f"{daangn_prefix}{hook_line}" if style_core in hook_line else f"{daangn_prefix.rstrip('] ')} {style_core}] {hook_line}"
    daangn_title = normalize_branch_name(daangn_title_raw[:48])
    daangn_bullets = nl.join([f"✔ {l}" for l in design_lines[:2]])
    daangn_addr_line = f"📍 {salon_addr} ({salon_name})" if salon_addr else f"📍 {salon_name}"
    daangn_body = format_mobile_readable(
        normalize_branch_name(
            f"안녕하세요, {salon_name}{designer_label}입니다.{nl}{nl}"
            f"{region_neighbor_label} 오늘의 {style_core} 시술 소식 전해드려요.{nl}{nl}"
            f"{daangn_bullets}{nl}{nl}"
            f"💡 집에서 손질 팁{nl}{care_comment}{nl}{nl}"
            f"궁금한 점이나 예약 문의는 당근 채팅으로 편하게 남겨주세요.{nl}"
            f"{daangn_addr_line}"
        ),
        max_line_len=28,
        preserve_media_markers=False,
    )

    x_point = mid_lines[0] if mid_lines else care_comment
    x_head = hook_line[:46].rstrip()
    x_tail = f"📍 {salon_name}"
    x_mid_budget = max(16, 102 - len(x_head) - len(x_tail) - 2)
    x_mid = x_point[:x_mid_budget].rstrip(' ,.')
    x_body = normalize_branch_name(f"{x_head}{nl}{x_mid}{nl}{x_tail}")[:105].rstrip()
    x_tail_tag = region_tags[-1:] if region_tags else []
    x_tags_str = format_clean_tags((style_kws[: (2 - len(x_tail_tag))] + x_tail_tag)[:2])

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
        'threads_body': threads_body, 'threads_tags': threads_tags_str,
        'daangn_title': daangn_title, 'daangn_body': daangn_body,
        'x_body': x_body, 'x_tags': x_tags_str
    }

# 기존 내부 호출 호환 별칭 (순수 파이썬 메인 변환 엔진 단일화)
fallback_convert = convert_master_content

def upload_to_youtube(title, body='', tags=None, video_path=None, privacy='public'):
    migrate_legacy_auth_files()
    scopes = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
    token_path, secret_path = get_auth_file('youtube_token.json'), get_auth_file('client_secret.json')
    if not os.path.exists(secret_path) or os.path.getsize(secret_path) <= 10:
        raise RuntimeError("client_secret.json 파일이 없습니다. 초기 설정 가이드 3단계를 확인해 주세요.")
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
    banned_tags = get_effective_banned_tags(prof)
    salon_name = get_salon_name(prof, fallback='헤어살롱')
    salon_addr = get_salon_address(prof)
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    designer_name = (prof.get('designer_name') or '').strip()
    designer_label = f" {designer_name}" if designer_name else ""
    booking_url = (prof.get('booking_url') or '').strip()
    booking_line = f"👉 예약·상담: {booking_url}{nl}" if booking_url else ""
    addr_line = f"📍 {salon_addr}{nl}" if salon_addr else ""

    clean_tags = [t.replace('#', '').strip() for t in (tags or []) if t.replace('#', '').strip() and t.replace('#', '').strip().lower() not in banned_tags]
    style_only = [t for t in dict.fromkeys(clean_tags) if t not in region_set]
    top_style = style_only[0] if style_only else '헤어스타일'
    region_hash = ' '.join([f'#{rt}' for rt in region_tags])
    tail_hash = f"#Shorts #{top_style} {region_hash}".strip()

    footer = f"{salon_name}{designer_label} (1:1 우선 예약제){nl}{booking_line}{addr_line}{tail_hash}"
    desc = (normalize_branch_name(body or '').strip() + nl + nl + footer).strip()
    yt_tags = list(dict.fromkeys(style_only[: max(1, 10 - len(region_tags))] + region_tags))[:10]

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
    return 'https://youtube.com/shorts/' + res['id']

def worker_auth_check(payload):
    global ACTIVE_WORKER_THREAD
    with STATE_LOCK:
        STATE['busy'] = True
        STATE['stop_requested'] = False
        STATE['active_run_id'] += 1
        my_run_id = STATE['active_run_id']
        ACTIVE_WORKER_THREAD = threading.current_thread()

    login_targets = [
        (payload.get('use_naver'), '네이버 블로그', 'https://blog.naver.com/GoBlogWrite.naver'),
        (payload.get('use_clip'), '네이버 클립', 'https://clipcreators.naver.com/web'),
        (payload.get('use_tt'), '틱톡', 'https://www.tiktok.com/tiktokstudio'),
        (payload.get('use_ig'), '인스타그램', 'https://www.instagram.com/'),
        (payload.get('use_pin'), '핀터레스트', 'https://kr.pinterest.com/pin-creation-tool/'),
        (payload.get('use_threads'), '쓰레드', 'https://www.threads.com/'),
        (payload.get('use_daangn'), '당근 비즈', 'https://business.daangn.com/'),
        (payload.get('use_x'), '엑스(X)', 'https://x.com/compose/post'),
    ]
    selected = [(label, url) for enabled, label, url in login_targets if enabled]

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
    try:
        from playwright.sync_api import sync_playwright

        names_str = ', '.join(label for label, _ in selected)
        log(f'⏳ [통합 로그인] 전용 크롬 1창에 {len(selected)}개 플랫폼 탭을 여는 중... ({names_str})')

        with sync_playwright() as p:
            # 로그인 점검 시 비밀번호 노출 방지를 위해 record_video_dir 없이 영구 크롬 프로필로 실행
            context = launch_persistent_chromium(p, headless=False, viewport=None, record_video_dir=None)
            context_closed = [False]
            try:
                context.on('close', lambda *_: context_closed.__setitem__(0, True))
            except Exception:
                pass

            first_page = None
            for idx, (label, url) in enumerate(selected):
                if _is_stop_requested(my_run_id) or context_closed[0]:
                    break
                try:
                    if idx == 0 and context.pages:
                        pg = context.pages[0]
                    else:
                        pg = context.new_page()
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

            if not _is_stop_requested(my_run_id) and not context_closed[0]:
                log('🔑 [통합 로그인] 열린 크롬 창에서 각 탭에 로그인(2차 인증·로그인 유지 체크)을 완료한 뒤, 크롬 창 우측 상단 [X] 버튼으로 닫아주세요. 닫는 즉시 영구 저장됩니다!')

            last_sync_ts = 0.0
            while not context_closed[0]:
                if _is_stop_requested(my_run_id):
                    log('🛑 [작업 중지됨] 사용자 요청으로 통합 로그인 창을 종료합니다.')
                    break

                try:
                    open_pages = [pg for pg in context.pages if not pg.is_closed()]
                except Exception:
                    break
                if not open_pages:
                    break

                now_ts = time.time()
                if now_ts - last_sync_ts >= 3.0:
                    try:
                        sync_cookies_from_persistent_context(context)
                    except Exception:
                        pass
                    last_sync_ts = now_ts

                # 1초 간격 대기 (Playwright 이벤트 펌핑 포함)
                try:
                    open_pages[0].wait_for_timeout(1000)
                except Exception:
                    time.sleep(1.0)

            try:
                sync_cookies_from_persistent_context(context)
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            context = None

        if not _is_stop_requested(my_run_id):
            log('✅ [통합 로그인 완료] 전용 크롬 프로필에 세션이 영구 저장되었습니다!')
    except Exception as e:
        if not _is_stop_requested(my_run_id):
            log(f'⚠️ 통합 로그인 중 오류: {e}')
    finally:
        if context is not None:
            try:
                sync_cookies_from_persistent_context(context)
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
        cleanup_chrome_profile_locks()
        with STATE_LOCK:
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

def _set_last_result(key, ok, msg, run_id=None):
    with STATE_LOCK:
        if run_id is not None and STATE.get('active_run_id') != run_id:
            return
        STATE['last_results'][key] = {'ok': bool(ok), 'msg': msg}

def worker_publish(payload):
    global ACTIVE_WORKER_THREAD
    with STATE_LOCK:
        STATE['busy'] = True
        STATE['stop_requested'] = False
        STATE['last_results'] = {}
        STATE['active_run_id'] += 1
        my_run_id = STATE['active_run_id']
        ACTIVE_WORKER_THREAD = threading.current_thread()
        state_vn = STATE['video_name']
        state_vp = STATE['video_path']
    naver_completed = False
    prof = load_profile()
    vn = payload.get('video_name') or state_vn
    video_path = os.path.join(UPLOAD_DIR, os.path.basename(vn)) if vn else state_vp
    stop_checker = lambda: _is_stop_requested(my_run_id)
    try:
        for _ in (0,):
            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_yt'):
                try:
                    log('🔴 [유튜브 쇼츠] 업로드 시작...')
                    yt_url = upload_to_youtube(normalize_branch_name(payload.get('yt_title', '')), normalize_branch_name(payload.get('yt_body', '')), split_tags(payload.get('yt_tags')), video_path, 'public')
                    if yt_url:
                        record_daily_usage('yt')
                        _set_last_result('yt', True, '완료', my_run_id)
                    else:
                        _set_last_result('yt', False, '업로드 실패', my_run_id)
                    log('✅ [유튜브 쇼츠] 완료!')
                except Exception as e:
                    _set_last_result('yt', False, str(e)[:80], my_run_id)
                    log(f'⚠️ [유튜브 쇼츠] 오류: {e}')

            default_place = get_place_search_keyword(prof)

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_naver'):
                try:
                    cat = (payload.get('naver_category') or '').strip()
                    cat_log = cat or '기본 게시판'
                    n_tags = split_tags(payload.get('naver_tags'))
                    sel_imgs = [p for p in (payload.get('naver_selected_images') or []) if p]
                    gif_cnt = sum(1 for p in sel_imgs if p.lower().endswith('.gif'))
                    img_cnt = len(sel_imgs) - gif_cnt
                    media_desc = f"사진 {img_cnt}장 · GIF {gif_cnt}개" if gif_cnt else f"사진 {img_cnt}장"
                    log(f'🟢 [네이버 블로그 - {cat_log}] 발행 시작 (선택 미디어 {media_desc})...')
                    log(f"🏷️ [네이버 블로그] 등록할 태그({min(len(n_tags), 10)}개): " + ", ".join([f"#{t.replace('#', '')}" for t in n_tags[:10]]))
                    b_ok = upload_to_naver_blog(
                        normalize_branch_name(payload.get('naver_title', '')),
                        normalize_branch_name(payload.get('naver_body', '')),
                        n_tags,
                        video_path,
                        cat,
                        normalize_branch_name(payload.get('naver_place') or default_place),
                        selected_images=payload.get('naver_selected_images') or [],
                        stop_checker=stop_checker,
                    )
                    if b_ok:
                        naver_completed = True
                        record_daily_usage('naver')
                        _set_last_result('naver', True, '발행 완료', my_run_id)
                    else:
                        _set_last_result('naver', False, '발행 중단/실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'✅ [네이버 블로그 - {cat_log}] 발행 완료!' if b_ok else f'⚠️ [네이버 블로그 - {cat_log}] 발행 중단/실패')
                except Exception as e:
                    _set_last_result('naver', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [네이버 블로그] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_clip'):
                try:
                    from naver_clip_uploader import upload_to_naver_clip
                    log('🟢 [네이버 클립] 게시 시작...')
                    c_ok = upload_to_naver_clip(normalize_branch_name(payload.get('clip_title', '')), normalize_branch_name(payload.get('clip_body', '')), split_tags(payload.get('clip_tags')), video_path, normalize_branch_name(payload.get('clip_place') or default_place), stop_checker=stop_checker)
                    if c_ok:
                        record_daily_usage('clip')
                        _set_last_result('clip', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('clip', False, '게시 실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [네이버 클립] 게시 완료!' if c_ok else '⚠️ [네이버 클립] 게시 실패')
                except Exception as e:
                    _set_last_result('clip', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [네이버 클립] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_tt'):
                try:
                    log('⚫ [틱톡] 게시 시작...')
                    tt_place = normalize_branch_name(payload.get('tt_location') or default_place)
                    t_ok = upload_to_tiktok(normalize_branch_name(payload.get('tt_title', '')), normalize_branch_name(payload.get('tt_body', '')), split_tags(payload.get('tt_tags')), video_path, place_name=tt_place, draft=False, stop_checker=stop_checker)
                    if t_ok:
                        record_daily_usage('tt')
                        _set_last_result('tt', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('tt', False, '취소/실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [틱톡] 게시 완료!' if t_ok else '⚠️ [틱톡] 취소됨')
                except Exception as e:
                    _set_last_result('tt', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [틱톡] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_ig'):
                try:
                    log('🟣 [인스타그램 릴스] 게시 시작...')
                    ig_place = normalize_branch_name(payload.get('ig_location') or default_place)
                    ig_ok = upload_to_instagram(normalize_branch_name(payload.get('ig_body', '')), split_tags(payload.get('ig_tags')), video_path, is_draft=False, place_name=ig_place, stop_checker=stop_checker)
                    if ig_ok:
                        record_daily_usage('ig')
                        _set_last_result('ig', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('ig', False, '게시 실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [인스타그램 릴스] 게시 완료!' if ig_ok else '⚠️ [인스타그램 릴스] 게시 실패')
                except Exception as e:
                    _set_last_result('ig', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [인스타그램] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_pin'):
                try:
                    from pinterest_uploader import upload_to_pinterest
                    log('🔴 [핀터레스트] 게시 시작...')
                    pin_link = payload.get('pin_dest_url') if payload.get('pin_dest_url') is not None else prof.get('booking_url', '')
                    pin_board = payload.get('pin_board') if payload.get('pin_board') is not None else prof.get('pinterest_board', '')
                    pin_url = upload_to_pinterest(title=normalize_branch_name(payload.get('pin_title', '')), description=normalize_branch_name(payload.get('pin_body', '')), tags=split_tags(payload.get('pin_tags')), video_path=video_path, link=pin_link, board_name=pin_board, stop_checker=stop_checker)
                    if pin_url:
                        record_daily_usage('pin')
                        _set_last_result('pin', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('pin', False, '중단/실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [핀터레스트] 게시 완료!' if pin_url else '⚠️ [핀터레스트] 중단됨')
                except Exception as e:
                    _set_last_result('pin', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [핀터레스트] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_threads'):
                try:
                    from threads_uploader import upload_to_threads
                    log('🧵 [쓰레드] 게시 시작...')
                    th_ok = upload_to_threads(body=normalize_branch_name(payload.get('threads_body', '')), tags=split_tags(payload.get('threads_tags')), video_path=video_path, stop_checker=stop_checker)
                    if th_ok:
                        record_daily_usage('threads')
                        _set_last_result('threads', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('threads', False, '게시 실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [쓰레드] 게시 완료!' if th_ok else '⚠️ [쓰레드] 게시 실패')
                except Exception as e:
                    _set_last_result('threads', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [쓰레드] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_daangn'):
                try:
                    from daangn_uploader import upload_to_daangn
                    daangn_imgs = [
                        p for p in (payload.get('naver_selected_images') or [])
                        if p and os.path.exists(p) and not p.lower().endswith('.gif')
                    ][:4]
                    log(f'🥕 [당근 비즈프로필] 소식 발행 시작 (첨부 사진 {len(daangn_imgs)}장)...')
                    d_ok = upload_to_daangn(title=normalize_branch_name(payload.get('daangn_title', '')), body=normalize_branch_name(payload.get('daangn_body', '')), video_path=video_path, selected_images=daangn_imgs, stop_checker=stop_checker)
                    if d_ok:
                        record_daily_usage('daangn')
                        _set_last_result('daangn', True, '발행 완료', my_run_id)
                    else:
                        _set_last_result('daangn', False, '발행 실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [당근 비즈프로필] 발행 완료!' if d_ok else '⚠️ [당근 비즈프로필] 발행 실패')
                except Exception as e:
                    _set_last_result('daangn', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [당근 비즈프로필] 오류: {e}')

            if _is_stop_requested(my_run_id):
                break
            if payload.get('use_x'):
                try:
                    from x_uploader import upload_to_x
                    log('𝕏 [엑스(X)] 게시 시작...')
                    x_ok = upload_to_x(body=normalize_branch_name(payload.get('x_body', '')), tags=split_tags(payload.get('x_tags')), video_path=video_path, stop_checker=stop_checker)
                    if x_ok:
                        record_daily_usage('x')
                        _set_last_result('x', True, '게시 완료', my_run_id)
                    else:
                        _set_last_result('x', False, '게시 실패', my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log('✅ [엑스(X)] 게시 완료!' if x_ok else '⚠️ [엑스(X)] 게시 실패')
                except Exception as e:
                    _set_last_result('x', False, str(e)[:80], my_run_id)
                    if not _is_stop_requested(my_run_id):
                        log(f'⚠️ [엑스(X)] 오류: {e}')

        if _is_stop_requested(my_run_id):
            log('🛑 [작업 중지 완료] 사용자 요청으로 남은 업로드를 모두 취소했습니다.')
        else:
            if video_path and os.path.exists(video_path):
                shutil.copy2(video_path, os.path.join(DONE_DIR, os.path.basename(video_path)))
                log('📁 완료된 영상 done 폴더 백업 완료!')
            with STATE_LOCK:
                run_results = dict(STATE.get('last_results', {})) if STATE['active_run_id'] == my_run_id else {}
            had_failed = any(not bool(v.get('ok')) for v in run_results.values())
            if had_failed:
                log('⚠️ 일부 플랫폼 업로드에서 오류가 발생했습니다.')
                log('💡 플랫폼 화면이 바뀌었나요? 상단 [🔄 업데이트 확인] 또는 하단 [💬 질문·소통방]을 확인하세요.')
            else:
                log('🎉 선택한 플랫폼 작업이 모두 완료되었습니다!')
    except Exception as e:
        if not _is_stop_requested(my_run_id):
            log(f'⚠️ 업로드 중 오류 발생: {e}')
            log('💡 플랫폼 화면이 바뀌었나요? 상단 [🔄 업데이트 확인] 또는 하단 [💬 질문·소통방]을 확인하세요.')
    finally:
        with STATE_LOCK:
            if STATE['active_run_id'] == my_run_id:
                STATE['busy'] = False
                STATE['stop_requested'] = False
            elif not STATE['busy']:
                STATE['stop_requested'] = False
            if ACTIVE_WORKER_THREAD is threading.current_thread():
                ACTIVE_WORKER_THREAD = None
        clean_and_unify_blackbox_files(clear_thumbs=naver_completed)

class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        clean_path = self.path.split('?')[0]
        if clean_path == '/':
            with open(UI_FILE, 'rb') as f:
                html = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif clean_path == '/favicon.ico':
            ico_path = os.path.join(BASE_DIR, 'planh_uploader.ico')
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
            fpath = os.path.join(UPLOAD_DIR, fname)
            if not os.path.isfile(fpath):
                self.send_error(404, 'Video Not Found')
                return
            file_size = os.path.getsize(fpath)
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
            self.send_header('Content-Length', str(chunk_len))
            if status_code == 206:
                self.send_header('Content-Range', f'bytes {start_b}-{end_b}/{file_size}')
            self.end_headers()
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
        elif clean_path == '/api/status':
            with STATE_LOCK:
                status_payload = {
                    'busy': STATE['busy'],
                    'logs': list(STATE['logs']),
                    'has_backup': has_backup_files(),
                    'last_results': dict(STATE.get('last_results', {})),
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
            self.send_json(get_setup_status())
        elif clean_path == '/api/quota_status':
            self.send_json(get_quota_status())
        elif clean_path == '/version.json':
            self.send_json(load_release_info())
        elif clean_path == '/api/release_info':
            self.send_json({'ok': True, 'info': load_release_info()})
        elif clean_path == '/api/check_update':
            self.send_json(check_public_update(timeout=4))
        elif clean_path == '/api/history':
            hf = os.path.join(DONE_DIR, 'history.json')
            hist = []
            if os.path.exists(hf):
                with open(hf, 'r', encoding='utf-8') as f:
                    hist = json.load(f)
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
            return self.send_json(resp_obj)

        l = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(l).decode('utf-8')
        req = json.loads(body) if body else {}

        if self.path == '/api/stop':
            self.send_json(force_stop_active_task())
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
        elif self.path == '/api/apply_update':
            with STATE_LOCK:
                is_busy = STATE['busy']
            if is_busy:
                return self.send_json({'ok': False, 'msg': '업로드/점검 작업이 진행 중입니다. 작업이 끝난 뒤 업데이트해 주세요.'})
            log('🚀 [원클릭 업데이트] GitHub 최신 버전 다운로드 및 안전 검증을 시작합니다...')
            res = apply_public_update(report=lambda pct, msg: log(f'🔄 [업데이트 {pct}%] {msg}'))
            if res.get('ok'):
                log(f"✅ [업데이트 완료] {res.get('msg')}")
            else:
                log(f"⚠️ [업데이트 실패] {res.get('msg')}")
            self.send_json(res)
        elif self.path == '/api/history/restore':
            hf = os.path.join(DONE_DIR, 'history.json')
            hist_list = []
            if os.path.exists(hf):
                with open(hf, 'r', encoding='utf-8') as f:
                    hist_list = json.load(f)
            r = next((it for it in hist_list if it.get('id') == req.get('id')), None)
            if not r: return self.send_json({'success': False, 'error': '없음'})
            r = {k: (normalize_branch_name(v) if isinstance(v, str) else v) for k, v in r.items()}
            vn = r.get('video_name')
            video_meta = None
            if vn and os.path.exists(os.path.join(DONE_DIR, vn)):
                dst = os.path.join(UPLOAD_DIR, vn)
                shutil.copy2(os.path.join(DONE_DIR, vn), dst)
                with STATE_LOCK:
                    STATE['video_path'] = dst
                    STATE['video_name'] = vn
                video_meta = probe_video_metadata(dst)
            self.send_json({'success': True, 'record': r, 'video_meta': video_meta})
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
                thumbs = extract_10_thumbnails(vp, count=10) if (vp and os.path.exists(vp)) else []
                v_meta = probe_video_metadata(vp) if (vp and os.path.exists(vp)) else None
                converted = convert_master_content(req.get('data_text', ''), vn)
                self.send_json({
                    'ok': True,
                    'data': converted,
                    'thumbnails': thumbs,
                    'video_meta': v_meta
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
            if is_busy: return self.send_json({'ok': False, 'msg': '이미 작업 진행 중입니다. 중지하려면 우측 상단 [🛑 강제 중지] 버튼을 눌러주세요.'})
            threading.Thread(target=worker_auth_check, args=(req,), daemon=True).start()
            self.send_json({'ok': True})
        elif self.path in ('/api/publish', '/api/upload'):
            if not is_profile_ready(load_profile()):
                return self.send_json({
                    'ok': False,
                    'need_onboarding': True,
                    'msg': '아직 매장 기본 정보가 설정되지 않았습니다. 초기 설정 마법사에서 매장명과 기본 정보를 먼저 입력해 주세요!',
                })
            with STATE_LOCK:
                is_busy = STATE['busy']
                state_vn = STATE['video_name']
            if is_busy: return self.send_json({'ok': False, 'msg': '이미 업로드가 진행 중입니다. 중지하려면 우측 상단 [🛑 강제 중지] 버튼을 눌러주세요.'})
            try:
                hf = os.path.join(DONE_DIR, 'history.json')
                hl = []
                if os.path.exists(hf):
                    with open(hf, 'r', encoding='utf-8') as f:
                        hl = json.load(f)
                vn = req.get('video_name') or state_vn
                rec = {'id': datetime.datetime.now().strftime('%Y%m%d_%H%M%S'), 'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'video_name': os.path.basename(vn) if vn else ''}
                for k in ['source_text', 'naver_title', 'naver_body', 'naver_tags', 'naver_category', 'clip_title', 'clip_body', 'clip_tags', 'yt_title', 'yt_body', 'yt_tags', 'tt_title', 'tt_body', 'tt_tags', 'ig_body', 'ig_tags', 'pin_title', 'pin_body', 'pin_tags', 'threads_body', 'threads_tags', 'daangn_title', 'daangn_body', 'x_body', 'x_tags']:
                    rec[k] = normalize_branch_name(req.get(k, '')) if isinstance(req.get(k, ''), str) else req.get(k, '')
                hl.insert(0, rec)
                with open(hf, 'w', encoding='utf-8') as f:
                    json.dump(hl[:50], f, ensure_ascii=False, indent=2)
            except Exception: pass
            threading.Thread(target=worker_publish, args=(req,), daemon=True).start()
            self.send_json({'ok': True})
        else:
            self.send_error(404, 'Not Found')

class ReusableServer(ThreadingHTTPServer): allow_reuse_address = True
if __name__ == '__main__':
    clean_and_unify_blackbox_files(clear_thumbs=True)
    print('🚀 서버 시작 (실시간 세부 로그 & 강제 중지 기능 탑재): http://127.0.0.1:8765', flush=True)
    ReusableServer(('127.0.0.1', 8765), AppHandler).serve_forever()