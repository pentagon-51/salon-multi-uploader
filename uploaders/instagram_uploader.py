import os, re, sys, time, shutil
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
    save_storage_state_safely,
    wait_for_manual_login,
    wait_for_test_mode_close,
    finalize_playwright_session,
    open_playwright_session,
    launch_persistent_chromium,
    record_platform_diagnostics,
    verify_publish_button_aim,
    auto_extract_and_save_profile_id,
    load_profile,
    get_place_search_keyword,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("instagram_cookies.json")
nl = chr(10)

def dismiss_instagram_login_popups(page):
    """인스타그램 쿠키 허용 / 로그인 정보 저장 / 알림 나중에 하기 팝업 안전 처리"""
    url = (page.url or "").lower()
    # 쿠키 허용 배너는 로그인 전에도 닫아줌
    for txt in ["모든 쿠키 허용", "Allow all cookies", "선택적 쿠키 허용", "Allow essential and optional cookies"]:
        try:
            btn = page.locator(f'button:has-text("{txt}")').first
            if btn.count() > 0 and btn.is_visible(timeout=300):
                btn.click(force=True)
                time.sleep(0.8)
        except Exception:
            pass

    # 로그인 직후 '정보 저장 / 나중에 하기' 화면(onetap 등) 처리
    if "accounts/login" not in url and "two_factor" not in url and not is_any_locator_visible(page, 'input[name="username"], input[name="password"]'):
        for txt in ["정보 저장", "Save info", "나중에 하기", "Not Now"]:
            try:
                btn = page.locator(f'button:text-is("{txt}"), div[role="button"]:text-is("{txt}")').first
                if btn.count() > 0 and btn.is_visible(timeout=300):
                    btn.click(force=True)
                    time.sleep(1.0)
            except Exception:
                pass


def is_instagram_logged_in(page):
    """인스타그램 로그인 폼·2단계 인증·보안 확인을 통과하고 메인 피드에 진입했는지 엄격히 판별"""
    url = (page.url or "").lower()
    if any(k in url for k in ("accounts/login", "accounts/emailsignup", "two_factor", "challenge", "consent", "accounts/onetap")):
        return False
    if is_any_locator_visible(page, 'input[name="username"], input[name="password"]'):
        return False
    logged_in_sels = (
        'a:has-text("만들기"), a:has-text("Create"), '
        'svg[aria-label="홈"], svg[aria-label="Home"], '
        'svg[aria-label="새로운 게시물"], svg[aria-label="New post"], '
        'svg[aria-label="만들기"], a[href="/direct/inbox/"]'
    )
    return page.locator(logged_in_sels).count() > 0


def _is_ig_upload_modal_open(page):
    """인스타그램 파일 선택 모달('컴퓨터에서 선택' 버튼 또는 열린 dialog 내부 input[type='file'])이 실제로 열렸는지 확인"""
    try:
        if is_any_locator_visible(
            page,
            'button:has-text("컴퓨터에서 선택"), button:has-text("Select from computer"), '
            'div[role="dialog"]:has-text("새 게시물 만들기"), div[role="dialog"]:has-text("Create new post"), '
            'div[role="dialog"]:has-text("사진과 동영상을 여기에 끌어다 놓으세요"), div[role="dialog"]:has-text("Drag photos and videos here")',
        ):
            return True
    except Exception:
        pass
    try:
        dlg = page.locator('div[role="dialog"]').first
        if dlg.count() > 0 and dlg.is_visible(timeout=200):
            if dlg.locator('input[type="file"]').count() > 0:
                return True
    except Exception:
        pass
    return False


def _click_ig_post_submenu_if_visible(page):
    """'+ 만들기' 클릭 후 드롭다운 하위 메뉴('게시물' / 'Post')가 나타나면 클릭하고 True 반환"""
    submenu_sels = [
        'div[role="menuitem"]:has-text("게시물")',
        'a[role="link"]:has-text("게시물")',
        'span:text-is("게시물")',
        'div[role="menuitem"]:has-text("Post")',
        'a[role="link"]:has-text("Post")',
        'span:text-is("Post")',
    ]
    for sel in submenu_sels:
        try:
            loc = page.locator(sel)
            cnt = loc.count()
            for idx in range(cnt - 1, -1, -1):
                el = loc.nth(idx)
                if el.is_visible(timeout=400):
                    el.click(force=True)
                    time.sleep(1.2)
                    return True
        except Exception:
            pass
    return False


def _open_instagram_create_modal(page):
    """좌측 사이드바 '+ 만들기' 클릭 후 작성 모달 또는 '게시물' 하위 메뉴 노출을 검증하며 최대 3회 재시도"""
    if _is_ig_upload_modal_open(page):
        return True

    trigger_groups = [
        [
            'a[role="link"]:has-text("만들기")',
            'a[role="link"]:has-text("Create")',
            'svg[aria-label="새로운 게시물"] >> xpath=ancestor::a[1]',
            'svg[aria-label="New post"] >> xpath=ancestor::a[1]',
        ],
        [
            'svg[aria-label="새로운 게시물"] >> xpath=ancestor::div[@role="button"][1]',
            'svg[aria-label="New post"] >> xpath=ancestor::div[@role="button"][1]',
            'svg[aria-label="만들기"] >> xpath=ancestor::a[1]',
            'svg[aria-label="Create"] >> xpath=ancestor::a[1]',
            'svg[aria-label="만들기"] >> xpath=ancestor::div[@role="button"][1]',
        ],
        [
            'a:has-text("만들기")',
            'a:has-text("Create")',
            'div[role="button"]:has-text("만들기")',
            'svg[aria-label="새로운 게시물"]',
            'svg[aria-label="만들기"]',
            'span:has-text("만들기")',
            'span:has-text("Create")',
        ],
    ]

    for attempt in range(3):
        clicked = False
        # 회차별로 우선순위 셀렉터 그룹을 순환해 첫 클릭 무반응 시 상위 클릭 가능 요소(ancestor::a / div[@role="button"])로 보강
        ordered_sels = trigger_groups[attempt] + [s for gi, grp in enumerate(trigger_groups) if gi != attempt for s in grp]
        for sel in ordered_sels:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible(timeout=1200):
                    el.scroll_into_view_if_needed()
                    el.click(force=(attempt > 0))
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            time.sleep(0.8)
            continue

        # 클릭 후 모달 또는 '게시물/Post' 하위 메뉴가 떴는지 최대 2.5초 검증
        for _ in range(5):
            if _is_ig_upload_modal_open(page):
                return True
            if _click_ig_post_submenu_if_visible(page):
                # 하위 메뉴 클릭 후 파일 선택 모달 노출까지 최대 4초 대기
                for _ in range(8):
                    if _is_ig_upload_modal_open(page):
                        return True
                    time.sleep(0.5)
            time.sleep(0.5)

        print(f"↪ [인스타그램] 만들기 모달 미오픈 감지 — 재시도 ({attempt + 1}/3)...", flush=True)
        dismiss_instagram_login_popups(page)

    return _is_ig_upload_modal_open(page)


def upload_to_instagram(body="", tags=None, video_path=None, is_draft=True, login_only=False, place_name=None, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [인스타그램] 업로드할 영상 파일이 없습니다:", video_path, flush=True)
        return None

    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)
    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    final_5 = list(dict.fromkeys(raw_list))[:5]

    tag_str = " ".join(["#" + t for t in final_5])
    full_caption = (clean_body + ((nl + nl + tag_str) if tag_str else "")).strip()
    target_place = (place_name if place_name is not None else get_place_search_keyword(prof)).strip()

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("인스타그램", mode=record_mode)
    browser = None
    context = None
    page = None
    video_obj = None

    with open_playwright_session() as p:
        try:
            context = launch_persistent_chromium(
                p,
                headless=False,
                viewport={"width": 1280, "height": 900},
                locale="ko-KR",
                record_video_dir=record_dir,
                record_video_size={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)
            dismiss_instagram_login_popups(page)

            if not is_instagram_logged_in(page):
                wait_for_manual_login(
                    page,
                    "인스타그램",
                    is_instagram_logged_in,
                    timeout_sec=180,
                    on_tick=lambda pg, idx, el: dismiss_instagram_login_popups(pg),
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "인스타그램")

            try:
                new_ig = auto_extract_and_save_profile_id(page, "ig")
                if new_ig:
                    print(f"🔗 [프로필 자동 연동] 인스타그램 아이디(@{new_ig}) 자동 저장 완료!", flush=True)
            except Exception:
                pass

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "인스타그램")
                print("✅ [인스타그램] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            for txt in ["나중에 하기", "Not Now", "취소", "Cancel"]:
                try:
                    btn = page.locator(f'button:has-text("{txt}")').first
                    if btn.is_visible(timeout=1500):
                        btn.click(); time.sleep(1)
                except Exception:
                    pass

            if not _open_instagram_create_modal(page):
                raise RuntimeError("인스타그램 만들기(+ 새로운 게시물) 작성 모달을 열지 못했습니다.")

            try:
                with page.expect_file_chooser(timeout=10000) as fc_info:
                    page.locator('button:has-text("컴퓨터에서 선택"), button:has-text("Select from computer")').first.click()
                fc_info.value.set_files(video_path)
            except Exception:
                page.locator('input[type="file"]').first.set_input_files(video_path)

            time.sleep(4)

            for ok_txt in ["확인", "OK"]:
                try:
                    ok_btn = page.locator(f'button:has-text("{ok_txt}")').first
                    if ok_btn.is_visible(timeout=2000):
                        ok_btn.click(); time.sleep(1.5)
                        break
                except Exception:
                    pass

            try:
                crop_btn = page.locator('svg[aria-label="자르기 선택"], svg[aria-label="Select crop"]').first
                if crop_btn.is_visible(timeout=2000):
                    crop_btn.click(); time.sleep(1)
                    orig_btn = page.locator('span:has-text("원본"), span:has-text("Original")').first
                    if orig_btn.is_visible(timeout=1500):
                        orig_btn.click(); time.sleep(1)
            except Exception:
                pass

            for _ in range(2):
                for nxt in ["다음", "Next"]:
                    try:
                        n_btn = page.locator(f'div[role="button"]:has-text("{nxt}"), button:has-text("{nxt}")').first
                        if n_btn.is_visible(timeout=4000):
                            n_btn.click(); time.sleep(2)
                            break
                    except Exception:
                        pass

            cap_box = page.locator('div[aria-label="문구를 입력하세요..."], div[aria-label="Write a caption..."], div[role="textbox"]').first
            cap_box.wait_for(state="visible", timeout=10000)
            cap_box.click(); time.sleep(0.5)
            copy_to_clipboard(full_caption)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.5)

            try:
                if target_place:
                    loc_input = page.locator('input[placeholder*="위치"], input[placeholder*="location"], input[name="queryBox"]').first
                    if loc_input.is_visible(timeout=3000):
                        loc_input.click(); time.sleep(0.6)
                        page.keyboard.insert_text(target_place); time.sleep(2.0)
                        loc_kw = target_place.split()[0]
                        loc_choice = page.locator(f'button:has-text("{loc_kw}"), div[role="button"]:has-text("{loc_kw}")').first
                        if loc_choice.is_visible(timeout=3000):
                            loc_choice.click(); time.sleep(1.0)
            except Exception:
                pass

            share_btn = None
            for sh in ["공유하기", "Share"]:
                try:
                    cand = page.locator(f'div[role="button"]:has-text("{sh}"), button:has-text("{sh}")').first
                    if cand.is_visible(timeout=4000):
                        share_btn = cand
                        break
                except Exception:
                    pass

            if test_mode:
                aim_res = verify_publish_button_aim(
                    share_btn,
                    expected_keywords=["공유하기", "Share"],
                    platform_name="인스타그램",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 인스타그램] 최종 공유 버튼 조준 검증 통과 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "인스타그램")
                wait_for_test_mode_close(context, "인스타그램", stop_checker=stop_checker)
                return "test_mode_ok"

            if share_btn is not None:
                share_btn.click()
            for _ in range(20):
                time.sleep(1.5)
                if page.locator('text="게시물이 공유되었습니다"').count() > 0 or page.locator('text="Your post has been shared"').count() > 0:
                    break

            save_storage_state_safely(context, COOKIES_PATH, "인스타그램")
            return "https://www.instagram.com/"

        except InterruptedError as ie:
            record_platform_diagnostics("인스타그램", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return None
        except Exception as e:
            record_platform_diagnostics("인스타그램", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [인스타그램] 오류: {e}", flush=True)
            return None
        finally:
            finalize_playwright_session(
                "인스타그램",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="인스타그램",
            )
# === 파일 끝 ===