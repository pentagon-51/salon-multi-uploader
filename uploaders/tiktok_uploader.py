import os, re, sys, time, shutil, unicodedata
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
    load_profile,
    get_place_search_keyword,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("tiktok_cookies.json")

def extract_hashtags_from_text(text, banned_tags=None):
    banned = banned_tags if banned_tags is not None else get_effective_banned_tags()
    return [t for t in re.findall(r"#([^\s#]+)", text or "") if t.lower() not in banned]

def build_tiktok_5_tags(extracted, base_tags, banned_tags=None, profile=None):
    src = base_tags if base_tags else extracted
    cleaned = [t.replace("#", "").strip() for t in (src or []) if t.replace("#", "").strip()]
    return list(dict.fromkeys(cleaned))[:5]

def _has_tiktok_session_cookie(context):
    """틱톡 로그인 세션 쿠키(sessionid/sid_tt/uid_tt 등) 존재 여부 확인"""
    if context is None:
        return False
    try:
        cookies = context.cookies(["https://www.tiktok.com"])
        session_keys = {"sessionid", "sessionid_ss", "sid_tt", "uid_tt"}
        for c in cookies:
            if c.get("name") in session_keys and (c.get("value") or "").strip():
                return True
    except Exception:
        pass
    return False


def is_tiktok_logged_in(page, context=None, login_only=False):
    """틱톡 로그인 완료 상태 판별 (login_only=True일 때는 input[type=file]에 의존하지 않고 로그인 상태·세션 쿠키로만 확인)"""
    url = (page.url or "").lower()
    if "login" in url or "signup" in url:
        return False
    if is_any_locator_visible(page, '[data-e2e="top-login-button"], button:text-is("로그인"), button:text-is("Log in"), a[href*="/login"]'):
        return False
    if not login_only and page.locator('input[type="file"]').count() > 0:
        return True
    if _has_tiktok_session_cookie(context) and "tiktok.com" in url:
        return True
    if "tiktokstudio" in url:
        return True
    if page.locator('[data-e2e="profile-icon"], [data-e2e="upload-icon"], [data-e2e="nav-profile"]').count() > 0:
        return True
    return False


def _dismiss_tiktok_guide_popups(page):
    """영상 첨부 후 나타나는 '휴대폰에서 동영상 미리 보기' 등 안내 팝업의 [확인]/[OK]/[닫기] 버튼을 안전하게 닫음"""
    if page is None:
        return False
    closed_any = False
    popup_btn_sels = [
        'div[role="dialog"] button:text-is("확인")',
        'div[role="dialog"] button:text-is("OK")',
        'div[role="dialog"] button:text-is("닫기")',
        'div[role="dialog"] button:text-is("이해했습니다")',
        'div[role="dialog"] button:text-is("Got it")',
        'div[role="dialog"] button[aria-label*="닫기"]',
        'div[role="dialog"] button[aria-label*="Close" i]',
        'div[class*="modal" i] button:text-is("확인")',
        'div[class*="modal" i] button:text-is("OK")',
        'div[class*="modal" i] button:text-is("닫기")',
        'div[class*="popover" i] button:text-is("확인")',
        'div[class*="popover" i] button:text-is("OK")',
        'div[class*="popover" i] button:text-is("닫기")',
        'div[class*="tooltip" i] button:text-is("확인")',
        'div[class*="tooltip" i] button:text-is("OK")',
        'div[class*="tooltip" i] button:text-is("닫기")',
        'div[class*="guide" i] button:text-is("확인")',
        'div[class*="guide" i] button:text-is("OK")',
        'div[class*="guide" i] button:text-is("닫기")',
        'button:text-is("확인")',
        'button:text-is("OK")',
        'button:text-is("이해했습니다")',
        'button:text-is("Got it")',
    ]
    for _ in range(3):
        clicked_round = False
        for sel in popup_btn_sels:
            try:
                loc = page.locator(sel)
                cnt = min(loc.count(), 5)
                for idx in range(cnt - 1, -1, -1):
                    btn = loc.nth(idx)
                    if btn.is_visible(timeout=250):
                        btn.click(force=True)
                        closed_any = True
                        clicked_round = True
                        print(f"🧹 [틱톡] 안내 팝업 닫기 완료 ({sel})", flush=True)
                        time.sleep(0.6)
                        break
                if clicked_round:
                    break
            except Exception:
                pass
        if not clicked_round:
            break
    return closed_any


def _is_tiktok_caption_applied(caption_box, expected_caption, video_path=""):
    """캡션 입력창에 실제 본문·태그가 정상 반영되었는지 확인 (빈 값 또는 영상 파일명 잔존 시 False)"""
    try:
        raw_txt = (caption_box.inner_text(timeout=1500) or "").strip()
    except Exception:
        try:
            raw_txt = (caption_box.text_content(timeout=1500) or "").strip()
        except Exception:
            raw_txt = ""
    norm_actual = unicodedata.normalize("NFC", re.sub(r"\s+", " ", raw_txt)).strip()
    norm_expected = unicodedata.normalize("NFC", re.sub(r"\s+", " ", expected_caption or "")).strip()
    if not norm_expected:
        return True
    if not norm_actual:
        return False
    if video_path:
        v_base = unicodedata.normalize("NFC", os.path.basename(video_path)).strip()
        v_stem = unicodedata.normalize("NFC", os.path.splitext(v_base)[0]).strip()
        if norm_actual == v_base or norm_actual == v_stem:
            return False
    probe = re.sub(r"\s+", "", norm_expected)[:12]
    actual_compact = re.sub(r"\s+", "", norm_actual)
    if probe and probe not in actual_compact:
        return False
    return True


def _apply_tiktok_cover_image(page, cover_image):
    """틱톡 업로드 화면에서 [커버 편집] -> [표지 업로드]로 대표사진(.jpg/.jpeg/.png)을 자동 적용"""
    if not cover_image or not os.path.isfile(cover_image):
        return False
    ext = os.path.splitext(cover_image)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        return False

    try:
        print(f"🖼️ [틱톡] 대표사진 커버 자동 적용 시도 중... ({os.path.basename(cover_image)})", flush=True)
        # 1) [커버 편집] / [Edit cover] 버튼 클릭
        edit_btn_sels = [
            'button:has-text("커버 편집")',
            'div[role="button"]:has-text("커버 편집")',
            'span:text-is("커버 편집")',
            'button:has-text("표지 편집")',
            'div[role="button"]:has-text("표지 편집")',
            'span:text-is("표지 편집")',
            'button:has-text("Edit cover")',
            'div[role="button"]:has-text("Edit cover")',
            'span:text-is("Edit cover")',
        ]
        edit_btn = None
        for sel in edit_btn_sels:
            try:
                cand = page.locator(sel).first
                if cand.count() > 0 and cand.is_visible(timeout=1200):
                    edit_btn = cand
                    break
            except Exception:
                pass

        if edit_btn is None:
            print("ℹ️ [틱톡] [커버 편집] 버튼을 찾지 못해 기본 커버로 진행합니다.", flush=True)
            return False

        edit_btn.scroll_into_view_if_needed()
        edit_btn.click(force=True)
        time.sleep(1.4)

        # 2) 모달 내 [표지 업로드] / [Upload cover] 탭·버튼 클릭 후 파일 업로드
        upload_tab_sels = [
            'div[role="dialog"] button:has-text("표지 업로드")',
            'div[role="dialog"] div[role="tab"]:has-text("표지 업로드")',
            'div[role="dialog"] span:text-is("표지 업로드")',
            'div[role="dialog"] button:has-text("커버 업로드")',
            'div[role="dialog"] div[role="tab"]:has-text("커버 업로드")',
            'div[role="dialog"] span:text-is("커버 업로드")',
            'div[role="dialog"] button:has-text("Upload cover")',
            'div[role="dialog"] div[role="tab"]:has-text("Upload cover")',
            'div[role="dialog"] span:text-is("Upload cover")',
            '[class*="modal" i] button:has-text("표지 업로드")',
            '[class*="modal" i] div[role="tab"]:has-text("표지 업로드")',
            '[class*="modal" i] span:text-is("표지 업로드")',
            '[class*="modal" i] button:has-text("Upload cover")',
            '[class*="modal" i] div[role="tab"]:has-text("Upload cover")',
            'button:has-text("표지 업로드")',
            'div[role="tab"]:has-text("표지 업로드")',
            'span:text-is("표지 업로드")',
            'button:has-text("Upload cover")',
            'div[role="tab"]:has-text("Upload cover")',
            'span:text-is("Upload cover")',
        ]
        uploaded_file = False
        for tsel in upload_tab_sels:
            try:
                tab = page.locator(tsel).first
                if tab.count() > 0 and tab.is_visible(timeout=800):
                    tab.click(force=True)
                    time.sleep(0.9)
                    break
            except Exception:
                pass

        # 2-1) 이미지 전용 input[type="file"] 직접 주입 우선 시도
        img_input_sels = [
            'div[role="dialog"] input[type="file"][accept*="image"]',
            '[class*="modal" i] input[type="file"][accept*="image"]',
            'input[type="file"][accept*="image"]',
            'div[role="dialog"] input[type="file"]',
            '[class*="modal" i] input[type="file"]',
        ]
        for isel in img_input_sels:
            try:
                fin = page.locator(isel).last
                if fin.count() > 0:
                    fin.set_input_files(cover_image)
                    uploaded_file = True
                    time.sleep(2.0)
                    break
            except Exception:
                pass

        # 2-2) input이 숨겨져 있거나 동적 생성되는 경우 file chooser로 업로드
        if not uploaded_file:
            chooser_btn_sels = [
                'div[role="dialog"] button:has-text("표지 업로드")',
                'div[role="dialog"] button:has-text("사진 업로드")',
                'div[role="dialog"] button:has-text("이미지 업로드")',
                'div[role="dialog"] button:has-text("Upload")',
                'div[role="dialog"] div[role="button"]:has-text("업로드")',
                '[class*="modal" i] button:has-text("업로드")',
                '[class*="modal" i] button:has-text("Upload")',
            ]
            for bsel in chooser_btn_sels:
                try:
                    btn = page.locator(bsel).last
                    if btn.count() > 0 and btn.is_visible(timeout=600):
                        with page.expect_file_chooser(timeout=4000) as fc_info:
                            btn.click(force=True)
                        fc_info.value.set_files(cover_image)
                        uploaded_file = True
                        time.sleep(2.0)
                        break
                except Exception:
                    pass

        if not uploaded_file:
            print("⚠️ [틱톡] 커버 이미지 파일 입력칸을 찾지 못해 모달을 닫고 기본 커버로 진행합니다.", flush=True)
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass
            return False

        # 3) 모달 내 [확인] / [Confirm] / [저장] / [Save] 버튼 클릭
        confirm_sels = [
            'div[role="dialog"] button:text-is("확인")',
            'div[role="dialog"] button:text-is("Confirm")',
            'div[role="dialog"] button:text-is("저장")',
            'div[role="dialog"] button:text-is("Save")',
            '[class*="modal" i] button:text-is("확인")',
            '[class*="modal" i] button:text-is("Confirm")',
            '[class*="modal" i] button:text-is("저장")',
            '[class*="modal" i] button:text-is("Save")',
            'div[role="dialog"] button:has-text("확인")',
            'div[role="dialog"] button:has-text("Confirm")',
            'div[role="dialog"] button:has-text("저장")',
            'div[role="dialog"] button:has-text("Save")',
            '[class*="modal" i] button:has-text("확인")',
            '[class*="modal" i] button:has-text("Confirm")',
        ]
        confirmed = False
        for csel in confirm_sels:
            try:
                cbtn = page.locator(csel).last
                if cbtn.count() > 0 and cbtn.is_visible(timeout=1200):
                    for _ in range(10):
                        if not cbtn.is_disabled():
                            break
                        time.sleep(0.4)
                    cbtn.click(force=True)
                    confirmed = True
                    time.sleep(1.5)
                    break
            except Exception:
                pass

        if confirmed:
            print("✅ [틱톡] 대표사진 커버 업로드 및 적용 완료!", flush=True)
            return True

        print("⚠️ [틱톡] 커버 모달 [확인/저장] 버튼을 찾지 못해 모달을 닫고 진행합니다.", flush=True)
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass
        return False
    except Exception as e:
        print(f"⚠️ [틱톡] 대표사진 커버 적용 건너뜀({e}) — 기본 커버로 계속 진행합니다.", flush=True)
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass
        return False


def upload_to_tiktok(title="", body="", tags=None, video_path=None, place_name=None, cover_image=None, draft=False, login_only=False, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [틱톡] 업로드할 영상 파일이 없습니다:", video_path, flush=True)
        return False

    nl = chr(10)
    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)
    clean_title, clean_body = (title or "").strip(), (body or "").strip()
    extracted_tags = extract_hashtags_from_text(clean_title + " " + clean_body, banned_tags)
    final_tags = build_tiktok_5_tags(extracted_tags, tags or [], banned_tags, profile=prof)

    body_no_tags = re.sub(r"#[^\s#]+", "", clean_body).strip()
    caption_text = (clean_title + nl + body_no_tags).strip() if (body_no_tags and body_no_tags != clean_title) else clean_title
    full_caption = f"{caption_text}\n\n{' '.join(['#' + t for t in final_tags if t])}".strip()

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("틱톡", mode=record_mode)
    browser = None
    context = None
    page = None
    video_obj = None

    with open_playwright_session() as p:
        try:
            context = launch_persistent_chromium(
                p,
                headless=False,
                viewport={"width": 1360, "height": 920},
                record_video_dir=record_dir,
                record_video_size={"width": 1360, "height": 920},
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            entry_url = (
                "https://www.tiktok.com/tiktokstudio"
                if login_only
                else "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
            )
            page.goto(entry_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)

            def _on_tt_tick(pg, tick_idx, elapsed):
                for txt in ["모두 수락", "Accept all", "Allow all"]:
                    try:
                        b = pg.locator(f'button:has-text("{txt}")').first
                        if b.count() > 0 and b.is_visible(timeout=300):
                            b.click(force=True)
                            time.sleep(0.6)
                    except Exception:
                        pass

            def _check_tt_login(pg):
                return is_tiktok_logged_in(pg, context=context, login_only=login_only)

            if not _check_tt_login(page):
                wait_for_manual_login(
                    page,
                    "틱톡",
                    _check_tt_login,
                    timeout_sec=180,
                    on_tick=_on_tt_tick,
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "틱톡")

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "틱톡")
                print("✅ [틱톡] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            if page.locator('input[type="file"]').count() == 0:
                page.goto("https://www.tiktok.com/tiktokstudio/upload?from=webapp", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_selector('input[type="file"]', state="attached", timeout=30000)
                time.sleep(2)

            page.locator('input[type="file"]').first.set_input_files(video_path)
            print("⏳ [틱톡] 영상 파일 전송 시작...", flush=True)

            caption_box = page.locator('div[contenteditable="true"]').first
            caption_box.wait_for(state="visible", timeout=60000)
            time.sleep(3)
            _dismiss_tiktok_guide_popups(page)

            def _paste_tiktok_caption():
                caption_box.click(force=True)
                time.sleep(0.4)
                page.keyboard.press(f"{MOD_KEY}+a")
                page.keyboard.press("Backspace")
                time.sleep(0.4)
                copy_to_clipboard(full_caption)
                page.keyboard.press(f"{MOD_KEY}+v")
                time.sleep(1.0)
                page.keyboard.press("Escape")
                time.sleep(0.8)

            _paste_tiktok_caption()
            if not _is_tiktok_caption_applied(caption_box, full_caption, video_path=video_path):
                print("⚠️ [틱톡] 캡션이 비어 있거나 파일명 그대로 남아 있어 안내 팝업을 다시 닫고 1회 재입력합니다...", flush=True)
                _dismiss_tiktok_guide_popups(page)
                time.sleep(0.6)
                _paste_tiktok_caption()

            if cover_image:
                _apply_tiktok_cover_image(page, cover_image)

            try:
                target_place = (place_name if place_name is not None else get_place_search_keyword(prof)).strip()
                if target_place:
                    page.evaluate("window.scrollBy(0, 350)"); time.sleep(0.8)
                    loc_sels = [
                        'input[placeholder*="위치 검색"]',
                        'input[placeholder*="위치"]',
                        'input[placeholder*="Location" i]',
                        'input[placeholder*="Search locations" i]',
                        'input[placeholder*="장소"]',
                    ]
                    loc_input = None
                    for lsel in loc_sels:
                        try:
                            cand = page.locator(lsel).first
                            if cand.count() > 0 and cand.is_visible(timeout=1000):
                                loc_input = cand
                                break
                        except Exception:
                            pass
                    if loc_input is None:
                        try:
                            fb_in = page.locator('div:has-text("위치")').locator('input').first
                            if fb_in.count() > 0 and fb_in.is_visible(timeout=800):
                                loc_input = fb_in
                        except Exception:
                            pass
                    if loc_input is not None:
                        loc_input.scroll_into_view_if_needed()
                        loc_input.click(force=True); time.sleep(0.4)
                        page.keyboard.press(f"{MOD_KEY}+a"); page.keyboard.press("Backspace")
                        loc_input.fill(target_place)
                        time.sleep(2.2)

                        opt_sel = 'div[role="option"], li[role="option"], div[class*="option" i]'
                        try:
                            page.locator(opt_sel).first.wait_for(state="visible", timeout=3500)
                        except Exception:
                            pass

                        loc_kw = target_place.split()[0]
                        place_picked = False
                        for psel in [
                            f'div[role="option"]:has-text("{target_place}")',
                            f'li[role="option"]:has-text("{target_place}")',
                            f'div[class*="option" i]:has-text("{target_place}")',
                            f'div[role="option"]:has-text("{loc_kw}")',
                            f'li[role="option"]:has-text("{loc_kw}")',
                            f'div[class*="option" i]:has-text("{loc_kw}")',
                            'div[role="option"]',
                            'li[role="option"]',
                            'div[class*="option" i]',
                        ]:
                            try:
                                p_item = page.locator(psel).first
                                if p_item.count() > 0 and p_item.is_visible(timeout=600):
                                    p_item.scroll_into_view_if_needed()
                                    p_item.click(force=True)
                                    place_picked = True
                                    print(f"📍 [틱톡] 위치('{target_place}') 선택 완료!", flush=True)
                                    time.sleep(0.9)
                                    break
                            except Exception:
                                pass
                        if not place_picked:
                            print(f"ℹ️ [틱톡] 위치('{target_place}') 드롭다운 결과를 찾지 못해 건너뛰고 진행합니다.", flush=True)
            except Exception as loc_err:
                print(f"ℹ️ [틱톡] 위치 검색 건너뜀({loc_err})", flush=True)

            try:
                page.evaluate("window.scrollBy(0, 300)"); time.sleep(1.0)
                priv = page.locator('div[role="combobox"]:has-text("나만 보기"), div[role="combobox"]:has-text("비공개"), div[role="combobox"]:has-text("친구"), div[role="combobox"]:has-text("Only me"), button[role="combobox"]:has-text("나만 보기")').first
                if priv.count() > 0 and priv.is_visible():
                    priv.click(force=True); time.sleep(1.0)
                    pub_opt = page.locator('div[role="option"]:has-text("모두"), div[role="option"]:has-text("전체"), li[role="option"]:has-text("모두"), span:text-is("모두"), span:text-is("전체 공개")').first
                    if pub_opt.count() > 0:
                        pub_opt.click(force=True); time.sleep(1.0)
            except Exception: pass

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)"); time.sleep(5)
            post_btn = page.locator('button[data-e2e="post_video_button"]').first
            if post_btn.count() == 0:
                post_btn = page.locator('button:text-is("게시"), button:text-is("Post")').last
            post_btn.wait_for(state="visible", timeout=60000)

            for i in range(60):
                dis, aria_dis = post_btn.get_attribute("disabled"), post_btn.get_attribute("aria-disabled")
                loading, cls = post_btn.get_attribute("data-loading"), (post_btn.get_attribute("class") or "")
                if i >= 6 and dis is None and aria_dis != "true" and loading != "true" and "disabled" not in cls.lower():
                    break
                time.sleep(1.5)

            if test_mode:
                aim_res = verify_publish_button_aim(
                    post_btn,
                    expected_keywords=["게시", "Post"],
                    platform_name="틱톡",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 틱톡] 최종 게시 버튼 조준 검증 통과 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "틱톡")
                wait_for_test_mode_close(context, "틱톡", stop_checker=stop_checker)
                return True

            time.sleep(2.0); post_btn.scroll_into_view_if_needed(); time.sleep(0.5); post_btn.click()

            for _ in range(20):
                time.sleep(1.5)
                modal_btn = page.locator('div[role="dialog"] button:has-text("지금 게시"), div[role="dialog"] button:has-text("계속"), div[role="dialog"] button:has-text("게시"), button[data-e2e="post_now_button"]').last
                if modal_btn.count() > 0 and modal_btn.is_visible():
                    modal_btn.click(); time.sleep(3)
                if "content" in page.url or page.locator('text="동영상이 게시되었습니다"').count() > 0:
                    break

            time.sleep(3)
            save_storage_state_safely(context, COOKIES_PATH, "틱톡")
            return True

        except InterruptedError as ie:
            record_platform_diagnostics("틱톡", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("틱톡", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [틱톡] 오류: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "틱톡",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="틱톡",
            )
# === 파일 끝 ===