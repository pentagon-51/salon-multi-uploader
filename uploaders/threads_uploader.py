import os, re, sys, json, time, shutil
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
from playwright.sync_api import sync_playwright
from os_utils import (
    MOD_KEY,
    copy_to_clipboard,
    get_base_dir,
    get_auth_file,
    get_blackbox_path,
    create_blackbox_record_dir,
    is_any_locator_visible,
    is_target_closed_error,
    save_storage_state_safely,
    wait_for_manual_login,
    wait_for_test_mode_close,
    finalize_playwright_session,
    launch_persistent_chromium,
    record_platform_diagnostics,
    verify_publish_button_aim,
    auto_extract_and_save_profile_id,
    load_profile,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("threads_cookies.json")
nl = chr(10)

def is_threads_logged_in(page):
    """threads.com / threads.net 모두 지원: 로그인 유도 화면이 없고 실제 로그인 UI가 보이면 True"""
    url = (page.url or "").lower()
    if not url or ("threads.com" not in url and "threads.net" not in url):
        return False
    if "instagram.com" in url or "/login" in url:
        return False

    # 화면에 로그인 유도 문구나 버튼이 보이면 미로그인 상태
    logged_out_sels = (
        ':text-is("Instagram으로 계속하기"), '
        ':text-is("Continue with Instagram"), '
        ':text-is("Log in with Instagram"), '
        ':text-is("Threads에서 소통해보세요"), '
        ':text-is("Threads에 로그인 또는 가입하기"), '
        ':text-is("사용자 이름으로 로그인"), '
        'a:text-is("로그인"), '
        'button:text-is("로그인"), '
        'div[role="button"]:text-is("로그인"), '
        'a:text-is("Log in"), '
        'button:text-is("Log in"), '
        'div[role="button"]:text-is("Log in")'
    )
    if is_any_locator_visible(page, logged_out_sels):
        return False

    # 로그인 유도 버튼이 전혀 없으면서 최신 threads.com / threads.net 로그인 전용 UI가 확인되면 로그인 완료
    logged_in_sels = (
        'div[role="button"]:has-text("새로운 소식을 공유해보세요"), '
        'span:has-text("새로운 소식을 공유해보세요"), '
        ':text("새로운 소식을 공유해보세요"), '
        'div[role="button"]:has-text("새로운 소식이 있나요"), '
        'span:has-text("새로운 소식이 있나요"), '
        'div[role="button"]:has-text("스레드를 시작"), '
        'div[role="button"]:has-text("스레드 시작"), '
        'div[role="button"]:has-text("What\'s new"), '
        'div[role="button"]:has-text("Start a thread"), '
        'div[role="button"]:text-is("게시"), '
        'button:text-is("게시"), '
        'div[role="button"]:has-text("유령 게시물 만들기"), '
        'a:has-text("새로운 스레드"), '
        'div[role="button"]:has-text("새로운 스레드"), '
        'button:has-text("새로운 스레드"), '
        'svg[aria-label="새로운 스레드"], '
        'svg[aria-label="만들기"], '
        'svg[aria-label="Create"], '
        'svg[aria-label="New thread"], '
        'a:has-text("프로필"), '
        'svg[aria-label="프로필"], '
        'svg[aria-label="Profile"], '
        'a:has-text("알림"), '
        'svg[aria-label="알림"], '
        'svg[aria-label="활동"], '
        'svg[aria-label="Activity"], '
        'a:has-text("인사이트")'
    )
    return page.locator(logged_in_sels).count() > 0

def click_ig_continue_if_present(page):
    """팝업창 맨 앞(.last)에 있는 'Instagram으로 계속하기' 또는 영문 SSO 버튼 자동 클릭"""
    try:
        ig_sels = (
            ':text-is("Instagram으로 계속하기"), '
            'button:has-text("Instagram으로 계속하기"), '
            'div[role="button"]:has-text("Instagram으로 계속하기"), '
            'button:has-text("Continue with Instagram"), '
            'div[role="button"]:has-text("Continue with Instagram"), '
            'div[role="button"]:has-text("Log in with Instagram")'
        )
        ig_btns = page.locator(ig_sels)
        if ig_btns.count() > 0:
            # 모달 팝업이 DOM 가장 마지막에 렌더링되므로 뒤에서부터 보이는 요소를 클릭
            for i in range(ig_btns.count() - 1, -1, -1):
                cand = ig_btns.nth(i)
                if cand.is_visible():
                    cand.click(force=True)
                    print("👆 [쓰레드] 'Instagram으로 계속하기' 팝업 버튼을 자동 클릭했습니다.", flush=True)
                    time.sleep(2.0)
                    return True
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return False

def _check_stopped(stop_checker):
    if stop_checker is None:
        return False
    try:
        return bool(stop_checker())
    except Exception:
        return False


def _is_threads_composer_dialog_visible(page):
    """쓰레드 작성 모달(div[role='dialog'])이 현재 화면에 열려 있는지 확인"""
    try:
        dlgs = page.locator('div[role="dialog"]')
        cnt = dlgs.count()
        for i in range(cnt):
            if dlgs.nth(i).is_visible(timeout=200):
                return True
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return False


def _has_threads_upload_spinner(scope):
    """작성 모달 내부에 영상 업로드/처리 로딩 스피너가 표시 중인지 확인"""
    spinner_sels = (
        '[role="progressbar"], '
        'svg[aria-label*="로딩"], '
        'svg[aria-label*="Loading" i], '
        '[aria-busy="true"], '
        ':text("업로드 중"), '
        ':text("Uploading")'
    )
    try:
        loc = scope.locator(spinner_sels)
        cnt = min(loc.count(), 10)
        for i in range(cnt):
            if loc.nth(i).is_visible(timeout=150):
                return True
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return False


def _wait_threads_media_ready(page, timeout_sec=35.0, stop_checker=None):
    """영상 파일 첨부 후 모달(div[role='dialog']) 내 영상 미리보기 렌더링 및 업로드 로딩 완료까지 정밀 대기"""
    preview_sels = (
        'video, '
        'img[src^="blob:"], '
        'img[src^="data:"], '
        '[aria-label*="동영상"], '
        '[aria-label*="video" i], '
        '[aria-label*="삭제"], '
        '[aria-label*="Remove" i], '
        '[aria-label*="제거"]'
    )
    start_ts = time.time()
    stable_ticks = 0
    saw_preview = False
    while (time.time() - start_ts) < timeout_sec:
        if _check_stopped(stop_checker):
            raise InterruptedError("쓰레드 영상 첨부 대기 중 사용자 중지 요청")
        scope = page.locator('div[role="dialog"]').last if _is_threads_composer_dialog_visible(page) else page
        has_preview = False
        try:
            ploc = scope.locator(preview_sels)
            cnt = min(ploc.count(), 10)
            for i in range(cnt):
                if ploc.nth(i).is_visible(timeout=150):
                    has_preview = True
                    break
        except Exception as e:
            if is_target_closed_error(e):
                raise
        if has_preview:
            saw_preview = True
        has_spinner = _has_threads_upload_spinner(scope)
        if has_preview and not has_spinner:
            stable_ticks += 1
            if stable_ticks >= 2:
                print("✅ [쓰레드] 영상 미리보기 렌더링 및 첨부 처리 완료!", flush=True)
                return True
        else:
            stable_ticks = 0
        time.sleep(0.5)
    return saw_preview


def _is_valid_threads_post_btn_text(btn):
    """클릭 대상 요소의 inner_text()가 정확히 '게시' 또는 'Post'인지 확인하고 '옵션'/'Options' 포함 요소는 무조건 제외"""
    if btn is None:
        return False
    try:
        raw_txt = re.sub(r"\s+", " ", (btn.inner_text(timeout=300) or "")).strip()
    except Exception as e:
        if is_target_closed_error(e):
            raise
        return False
    if not raw_txt:
        return False
    low_txt = raw_txt.lower()
    if "옵션" in raw_txt or "option" in low_txt or "답글" in raw_txt or "reply" in low_txt:
        return False
    return raw_txt == "게시" or low_txt == "post"


def _dismiss_threads_topic_dropdown(page):
    """해시태그(#태그) 입력 직후 '새로운 주제 태그하기' 등 자동완성 드롭다운이 열려 [게시] 클릭을 가릴 때만
    Escape 키 대신 모달 상단 제목/빈 영역을 가볍게 클릭해 드롭다운만 안전하게 닫음"""
    dropdown_sels = (
        ':text("새로운 주제 태그하기"), '
        ':text("Tag a new topic"), '
        'div[role="listbox"], '
        'ul[role="listbox"]'
    )
    if not is_any_locator_visible(page, dropdown_sels):
        return False

    # 모달 제목('새로운 스레드' / 'New thread') 또는 모달 상단 중앙 빈 영역 클릭 (좌측 취소 버튼 회피)
    safe_header_sels = [
        'div[role="dialog"] :text-is("새로운 스레드")',
        'div[role="dialog"] :text-is("New thread")',
        'div[role="dialog"] h1',
        'div[role="dialog"] h2',
    ]
    clicked_safe = False
    for sel in safe_header_sels:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=300):
                el.click()
                clicked_safe = True
                break
        except Exception as e:
            if is_target_closed_error(e):
                raise

    if not clicked_safe:
        try:
            dlg = page.locator('div[role="dialog"]').last
            if dlg.count() > 0 and dlg.is_visible(timeout=300):
                box = dlg.bounding_box()
                if box and box.get("width", 0) > 120 and box.get("height", 0) > 60:
                    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + 22)
                    clicked_safe = True
        except Exception as e:
            if is_target_closed_error(e):
                raise

    if clicked_safe:
        time.sleep(0.6)
        print("🧹 [쓰레드] 주제 태그 추천 드롭다운 감지 → 모달 상단 클릭으로 안전 닫힘 처리", flush=True)
    return clicked_safe


def _find_threads_post_button(page):
    """작성 모달(div[role='dialog']) 내부에서 정확히 '게시' 또는 'Post' 텍스트만 일치하는 버튼을 우선 탐색하고,
    모달 내부에서 찾지 못한 경우 화면 전체에서 정확히 '게시'/'Post'인 보이는 버튼('옵션'/'Options'/'답글'/'Reply' 제외)을 폴백 탐색."""
    dialog_sels = [
        'div[role="dialog"] div[role="button"]:text-is("게시")',
        'div[role="dialog"] button:text-is("게시")',
        'div[role="dialog"] div[role="button"]:text-is("Post")',
        'div[role="dialog"] button:text-is("Post")',
    ]
    fallback_sels = [
        'div[role="button"]:text-is("게시")',
        'button:text-is("게시")',
        'div[role="button"]:text-is("Post")',
        'button:text-is("Post")',
    ]

    def _search_group(sels):
        first_valid = None
        for sel in sels:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for idx in range(cnt - 1, -1, -1):
                    cand = loc.nth(idx)
                    if not cand.is_visible(timeout=200):
                        continue
                    if not _is_valid_threads_post_btn_text(cand):
                        continue
                    if _is_threads_post_btn_enabled(cand):
                        return cand
                    if first_valid is None:
                        first_valid = cand
            except Exception as e:
                if is_target_closed_error(e):
                    raise
        return first_valid

    # 1순위: div[role="dialog"] 내부 탐색
    btn = _search_group(dialog_sels)
    if btn is not None:
        return btn
    # 2순위: 모달 내부에서 못 찾은 경우 화면 전체 폴백 탐색 (활성 버튼 우선)
    return _search_group(fallback_sels)


def _is_threads_post_btn_enabled(btn):
    """aria-disabled, disabled, tabindex, 투명도(opacity), pointer-events, 비활성 클래스를 모두 점검"""
    if btn is None:
        return False
    try:
        state = btn.evaluate(
            """(el) => {
                if (!el) return false;
                const checkNode = (node) => {
                    if (!node || node.nodeType !== 1) return true;
                    const ariaDis = (node.getAttribute('aria-disabled') || '').toLowerCase();
                    if (ariaDis === 'true') return false;
                    if (node.hasAttribute('disabled') || node.disabled === true) return false;
                    if ((node.getAttribute('tabindex') || '').trim() === '-1') return false;
                    const cls = String(node.className || '').toLowerCase();
                    if (cls.includes('disabled')) return false;
                    const st = window.getComputedStyle(node);
                    if (st) {
                        if (st.pointerEvents === 'none' || st.cursor === 'not-allowed') return false;
                        const op = parseFloat(st.opacity || '1');
                        if (!isNaN(op) && op < 0.7) return false;
                    }
                    return true;
                };
                if (!checkNode(el)) return false;
                if (el.parentElement && !checkNode(el.parentElement)) return false;
                const inner = el.querySelector('div, span');
                if (inner) {
                    const ist = window.getComputedStyle(inner);
                    if (ist) {
                        const iop = parseFloat(ist.opacity || '1');
                        if (!isNaN(iop) && iop < 0.7) return false;
                    }
                }
                return true;
            }"""
        )
        return bool(state)
    except Exception as e:
        if is_target_closed_error(e):
            raise
        return False


def _wait_threads_post_btn_enabled(page, timeout_sec=40.0, stop_checker=None):
    """[게시]/[Post] 버튼이 실제로 클릭 가능한 활성 상태가 될 때까지 최대 timeout_sec 대기"""
    deadline = time.time() + timeout_sec
    last_btn = None
    while time.time() < deadline:
        if _check_stopped(stop_checker):
            raise InterruptedError("쓰레드 [게시] 활성화 대기 중 사용자 중지 요청")
        btn = _find_threads_post_button(page)
        if btn is not None:
            last_btn = btn
            scope = page.locator('div[role="dialog"]').last if _is_threads_composer_dialog_visible(page) else page
            if _is_threads_post_btn_enabled(btn) and not _has_threads_upload_spinner(scope):
                return btn
        time.sleep(0.5)
    if last_btn is None:
        raise RuntimeError("쓰레드 [게시] 버튼을 찾지 못했습니다.")
    raise RuntimeError("쓰레드 영상 처리 지연 또는 비활성 상태로 [게시] 버튼이 활성화되지 않았습니다.")


def _wait_threads_publish_complete(page, had_dialog=True, timeout_sec=40.0, stop_checker=None):
    """[게시] 클릭 후 실제 게시 완료(모달 완전 닫힘 또는 게시 완료 토스트)까지 최대 timeout_sec 정밀 검증.
    '게시 중...'/'Posting...' 진행 중에는 종료하지 않고 끝까지 대기하며, 미확인 시 RuntimeError 발생."""
    posting_sels = (
        ':text("게시 중"), '
        ':text("Posting"), '
        ':text("업로드 중"), '
        ':text("Uploading")'
    )
    done_toast_sels = (
        ':text-is("게시됨"), '
        ':text("게시되었습니다"), '
        ':text("스레드가 게시되었습니다"), '
        ':text-is("Posted"), '
        '[role="alert"]:has-text("게시됨"), '
        '[role="alert"]:has-text("Posted"), '
        '[role="alert"]:has-text("보기"), '
        '[role="status"]:has-text("게시됨"), '
        '[role="status"]:has-text("Posted"), '
        '[role="status"]:has-text("보기")'
    )
    error_sels = (
        ':text("게시하지 못했습니다"), '
        ':text("업로드하지 못했습니다"), '
        ':text("Couldn\'t post"), '
        ':text("Failed to post")'
    )

    start_ts = time.time()
    saw_posting = False
    dialog_closed_since = None

    while (time.time() - start_ts) < timeout_sec:
        if _check_stopped(stop_checker):
            raise InterruptedError("쓰레드 게시 완료 대기 중 사용자 중지 요청")

        if is_any_locator_visible(page, error_sels):
            raise RuntimeError("쓰레드에서 게시 실패 오류가 표시되었습니다.")

        is_posting = is_any_locator_visible(page, posting_sels)
        if is_posting:
            saw_posting = True
            dialog_closed_since = None
            time.sleep(0.7)
            continue

        # 완료 토스트/알림 감지 (게시 중 문구가 사라진 상태)
        if is_any_locator_visible(page, done_toast_sels):
            print("✅ [쓰레드] 게시 완료 알림을 확인했습니다!", flush=True)
            time.sleep(1.0)
            return True

        dlg_open = _is_threads_composer_dialog_visible(page)
        if had_dialog and not dlg_open:
            # 모달이 닫힌 직후 '게시 중...' 토스트가 뜰 수 있으므로 1.5초 안정화 확인
            if dialog_closed_since is None:
                dialog_closed_since = time.time()
            elif (time.time() - dialog_closed_since) >= 1.5:
                # 모달이 닫혔고 게시 중 토스트도 없으며 안정화 완료
                if saw_posting:
                    print("✅ [쓰레드] '게시 중...' 전송 완료 및 작성 모달 닫힘을 확인했습니다!", flush=True)
                else:
                    print("✅ [쓰레드] 작성 모달 닫힘(게시 완료)을 확인했습니다!", flush=True)
                return True
        else:
            dialog_closed_since = None

        time.sleep(0.5)

    raise RuntimeError("쓰레드 게시 완료가 확인되지 않았습니다.")


def upload_to_threads(body="", tags=None, video_path=None, login_only=False, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [쓰레드] 영상 파일을 찾을 수 없습니다.", flush=True)
        return False

    def _is_stopped():
        if stop_checker is None:
            return False
        try:
            return bool(stop_checker())
        except Exception:
            return False

    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    deduped = list(dict.fromkeys(raw_list))[:1]
    top_tag = deduped[0] if deduped else ""
    full_text = f"{clean_body}{nl}{nl}#{top_tag}".strip() if top_tag else clean_body

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("쓰레드", mode=record_mode)
    browser = None
    context = None
    page = None
    video_obj = None

    with sync_playwright() as p:
        try:
            if _is_stopped():
                return False
            context = launch_persistent_chromium(
                p,
                headless=False,
                viewport={"width": 1280, "height": 900},
                record_video_dir=record_dir,
                record_video_size={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            print("🌐 [쓰레드] 페이지 접속 중...", flush=True)
            page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)
            if _is_stopped():
                return False

            def _on_threads_tick(pg, tick_idx, elapsed):
                if _is_stopped() or pg.is_closed():
                    return
                # 쿠키 동의 배너만 정리 (수동 로그인/비밀번호 입력 팝업은 절대 건드리지 않음)
                for c_txt in ["모든 쿠키 허용", "Allow all cookies"]:
                    if _is_stopped() or pg.is_closed():
                        return
                    try:
                        cb = pg.locator(f'button:has-text("{c_txt}")').first
                        if cb.count() > 0 and cb.is_visible(timeout=300):
                            cb.click(force=True)
                            time.sleep(0.6)
                    except Exception as e:
                        if is_target_closed_error(e):
                            raise

            if not is_threads_logged_in(page):
                wait_for_manual_login(
                    page,
                    "쓰레드",
                    is_threads_logged_in,
                    timeout_sec=180,
                    on_tick=_on_threads_tick,
                    stop_checker=stop_checker,
                )
                if _is_stopped():
                    return False
                save_storage_state_safely(context, COOKIES_PATH, "쓰레드", mark_verified=True)

            if _is_stopped():
                return False
            if not is_threads_logged_in(page):
                raise RuntimeError("쓰레드 로그인이 완료되지 않아 중단했습니다.")

            try:
                new_th = auto_extract_and_save_profile_id(page, "threads")
                if new_th:
                    print(f"🔗 [프로필 자동 연동] 쓰레드 아이디(@{new_th}) 자동 저장 완료!", flush=True)
            except Exception:
                pass

            save_storage_state_safely(context, COOKIES_PATH, "쓰레드", mark_verified=True)
            if login_only:
                print("✅ [쓰레드] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            # 글쓰기 모달창 열기
            print("✍️ [쓰레드] 새 스레드 작성창 열기 시도 중...", flush=True)
            compose_open_sels = [
                'div[role="button"]:has-text("새로운 소식을 공유해보세요")',
                'span:has-text("새로운 소식을 공유해보세요")',
                ':text("새로운 소식을 공유해보세요")',
                'div[role="button"]:has-text("새로운 소식이 있나요")',
                'span:has-text("새로운 소식이 있나요")',
                'a:has-text("새로운 스레드")',
                'div[role="button"]:has-text("새로운 스레드")',
                'button:has-text("새로운 스레드")',
                'div[role="button"]:has-text("스레드를 시작")',
                'div[role="button"]:has-text("스레드 시작")',
                'svg[aria-label="새로운 스레드"]',
                'svg[aria-label="만들기"]',
                'svg[aria-label="Create"]',
                'svg[aria-label="New thread"]'
            ]
            for sel in compose_open_sels:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=2000):
                        el.click(force=True)
                        time.sleep(2.0)
                        break
                except Exception:
                    pass

            print("📎 [쓰레드] 영상 파일 첨부 및 미리보기 렌더링 대기 중...", flush=True)
            file_in = page.locator('div[role="dialog"] input[type="file"], input[type="file"]').first
            file_in.wait_for(state="attached", timeout=15000)
            file_in.set_input_files(video_path)
            _wait_threads_media_ready(page, timeout_sec=35.0, stop_checker=stop_checker)

            print("📝 [쓰레드] 본문 및 대표 토픽 태그 입력 중...", flush=True)
            tbox = page.locator('div[role="dialog"] div[contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]').first
            tbox.wait_for(state="visible", timeout=15000)
            tbox.click(force=True)
            time.sleep(0.4)
            copy_to_clipboard(full_text)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.5)
            _dismiss_threads_topic_dropdown(page)

            print("🚀 [쓰레드] 영상 처리 및 [게시] 버튼 활성화 대기 중 (최대 40초)...", flush=True)
            post_btn = _wait_threads_post_btn_enabled(page, timeout_sec=40.0, stop_checker=stop_checker)

            if test_mode:
                aim_res = verify_publish_button_aim(
                    post_btn,
                    expected_keywords=["게시", "Post"],
                    forbidden_keywords=["옵션", "Options", "취소", "Cancel", "임시저장", "Draft"],
                    platform_name="쓰레드",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 쓰레드] 최종 게시 버튼 조준 검증 통과 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "쓰레드")
                wait_for_test_mode_close(context, "쓰레드", stop_checker=stop_checker)
                return True

            _dismiss_threads_topic_dropdown(page)
            had_dialog = _is_threads_composer_dialog_visible(page)
            time.sleep(0.8)
            if not _is_valid_threads_post_btn_text(post_btn) or not _is_threads_post_btn_enabled(post_btn):
                post_btn = _wait_threads_post_btn_enabled(page, timeout_sec=15.0, stop_checker=stop_checker)
            if not _is_valid_threads_post_btn_text(post_btn):
                raise RuntimeError("쓰레드 [게시] 버튼 텍스트 검증 실패(옵션 등 다른 버튼 감지)로 클릭을 중단했습니다.")
            try:
                post_btn.scroll_into_view_if_needed()
                post_btn.click(timeout=5000)
            except Exception as click_err:
                if is_target_closed_error(click_err):
                    raise
                if not _is_valid_threads_post_btn_text(post_btn) or not _is_threads_post_btn_enabled(post_btn):
                    raise RuntimeError("쓰레드 [게시] 버튼이 비활성 상태이거나 올바른 버튼이 아니어서 클릭하지 않았습니다.") from click_err
                post_btn.click(force=True)

            print("⏳ [쓰레드] 실제 게시 완료 확인 대기 중 (최대 40초)...", flush=True)
            _wait_threads_publish_complete(page, had_dialog=had_dialog, timeout_sec=40.0, stop_checker=stop_checker)

            save_storage_state_safely(context, COOKIES_PATH, "쓰레드", mark_verified=True)
            return True
        except InterruptedError as ie:
            record_platform_diagnostics("쓰레드", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("쓰레드", page=page, exc=e)
            if not _is_stopped():
                print(f"⚠️ [쓰레드] 중단/오류: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "쓰레드",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="쓰레드",
            )