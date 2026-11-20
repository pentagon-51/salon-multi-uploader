import os, re, json, time, shutil
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
    finalize_playwright_session,
    launch_persistent_chromium,
    load_profile,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("threads_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
os.makedirs(DEBUG_DIR, exist_ok=True)
nl = chr(10)

def is_threads_logged_in(page):
    """로그아웃 버튼이나 인스타그램 로그인 화면이 없고, 실제 로그인 상태인지 엄격히 판별"""
    url = (page.url or "").lower()
    if "instagram.com" in url or "/login" in url:
        return False

    # 화면에 로그인 유도 문구나 버튼이 보이면 무조건 미로그인 상태
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

    # 로그인 유도 버튼이 전혀 없으면서 글쓰기 영역이 보일 때만 로그인 완료로 인정
    compose_sels = (
        'div[role="button"]:has-text("새로운 소식이 있나요"), '
        'span:has-text("새로운 소식이 있나요"), '
        'div[role="button"]:has-text("스레드를 시작"), '
        'div[role="button"]:has-text("스레드 시작"), '
        'div[role="button"]:has-text("What\'s new"), '
        'div[role="button"]:has-text("Start a thread"), '
        'svg[aria-label="새로운 스레드"], '
        'svg[aria-label="만들기"], '
        'svg[aria-label="Create"], '
        'svg[aria-label="New thread"]'
    )
    return page.locator(compose_sels).count() > 0

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

def upload_to_threads(body="", tags=None, video_path=None, login_only=False, stop_checker=None):
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

    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)
    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    filtered = [t for t in raw_list if t.lower() not in banned_tags]
    region_tags = get_region_tags(prof)
    top_tag = filtered[0] if filtered else (region_tags[0] if region_tags else "헤어스타일")
    full_text = f"{clean_body}{nl}{nl}#{top_tag}".strip()

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
                save_storage_state_safely(context, COOKIES_PATH, "쓰레드")

            if _is_stopped():
                return False
            if not is_threads_logged_in(page):
                raise RuntimeError("쓰레드 로그인이 완료되지 않아 중단했습니다.")

            save_storage_state_safely(context, COOKIES_PATH, "쓰레드")
            if login_only:
                print("✅ [쓰레드] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            # 글쓰기 모달창 열기
            print("✍️ [쓰레드] 새 스레드 작성창 열기 시도 중...", flush=True)
            compose_open_sels = [
                'div[role="button"]:has-text("새로운 소식이 있나요")',
                'span:has-text("새로운 소식이 있나요")',
                'div[role="button"]:has-text("스레드를 시작")',
                'div[role="button"]:has-text("스레드 시작")',
                'svg[aria-label="새로운 스레드"]',
                'svg[aria-label="만들기"]',
                'svg[aria-label="Create"]'
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

            print("📎 [쓰레드] 영상 파일 첨부 중...", flush=True)
            file_in = page.locator('div[role="dialog"] input[type="file"], input[type="file"]').first
            file_in.wait_for(state="attached", timeout=15000)
            file_in.set_input_files(video_path)
            time.sleep(4.5)

            print("📝 [쓰레드] 본문 및 대표 토픽 태그 입력 중...", flush=True)
            tbox = page.locator('div[role="dialog"] div[contenteditable="true"], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]').first
            tbox.wait_for(state="visible", timeout=15000)
            tbox.click(force=True)
            time.sleep(0.4)
            copy_to_clipboard(full_text)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.5)

            print("🚀 [쓰레드] 영상 처리 대기 및 [게시] 버튼 클릭 중...", flush=True)
            post_btn = page.locator('div[role="dialog"] div[role="button"]:text-is("게시"), div[role="dialog"] div[role="button"]:has-text("게시"), div[role="button"]:text-is("게시"), div[role="button"]:text-is("Post")').last
            post_btn.wait_for(state="visible", timeout=20000)

            for _ in range(30):
                aria_dis = post_btn.get_attribute("aria-disabled")
                if aria_dis != "true":
                    break
                time.sleep(1.0)

            time.sleep(1.5)
            post_btn.click(force=True)
            time.sleep(6.0)

            save_storage_state_safely(context, COOKIES_PATH, "쓰레드")
            return True
        except InterruptedError:
            return False
        except Exception as e:
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