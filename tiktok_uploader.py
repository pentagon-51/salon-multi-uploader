import os, re, time, shutil
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
    launch_persistent_chromium,
    record_platform_diagnostics,
    load_profile,
    get_place_search_keyword,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("tiktok_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
PROMO_TAGS = ["드롭컷", "크롭컷", "아이비리그컷", "댄디컷", "상고컷", "레이어드컷", "태슬컷"]
os.makedirs(DEBUG_DIR, exist_ok=True)

def extract_hashtags_from_text(text, banned_tags=None):
    banned = banned_tags if banned_tags is not None else get_effective_banned_tags()
    return [t for t in re.findall(r"#([^\s#]+)", text or "") if t.lower() not in banned]

def build_tiktok_5_tags(extracted, base_tags, banned_tags=None, profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    banned = banned_tags if banned_tags is not None else get_effective_banned_tags(prof)
    combined = [t.replace("#", "").strip() for t in (extracted + base_tags) if t.replace("#", "").strip()]
    filtered = [t for t in combined if t.lower() not in banned]
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_only = []
    for t in filtered:
        if t not in region_set and t != "손질편한머리" and t not in style_only:
            style_only.append(t)
    max_style = max(1, 5 - len(region_tags))
    for p in PROMO_TAGS:
        if len(style_only) >= max_style: break
        if p not in style_only: style_only.append(p)
    return (style_only[:max_style] + region_tags)[:5]

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


def upload_to_tiktok(title="", body="", tags=None, video_path=None, place_name=None, draft=False, login_only=False, stop_checker=None, test_mode=False):
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

    with sync_playwright() as p:
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
            caption_box.click(force=True); time.sleep(0.4)
            page.keyboard.press(f"{MOD_KEY}+a"); page.keyboard.press("Backspace"); time.sleep(0.4)
            copy_to_clipboard(full_caption); page.keyboard.press(f"{MOD_KEY}+v"); time.sleep(1.0)
            page.keyboard.press("Escape"); time.sleep(1.0)

            try:
                target_place = (place_name if place_name is not None else get_place_search_keyword(prof)).strip()
                if target_place:
                    page.evaluate("window.scrollBy(0, 350)"); time.sleep(1.0)
                    loc_input = page.locator('input[placeholder*="위치"], input[placeholder*="장소"], input[placeholder*="Location"], input[placeholder*="Search locations"]').first
                    if loc_input.count() == 0:
                        loc_input = page.locator('div:has-text("위치")').locator('input').first
                    if loc_input.count() > 0 and loc_input.is_visible():
                        loc_input.click(force=True); time.sleep(0.5)
                        page.keyboard.press(f"{MOD_KEY}+a"); page.keyboard.press("Backspace")
                        loc_input.fill(target_place); time.sleep(2.5)
                        loc_kw = target_place.split()[0]
                        place_item = page.locator(f'div[role="option"]:has-text("{loc_kw}"), li[role="option"]:has-text("{loc_kw}"), span:has-text("{target_place}")').first
                        if place_item.count() > 0 and place_item.is_visible():
                            place_item.click(force=True)
                        time.sleep(1.0)
            except Exception: pass

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
                print("🧪 [테스트 모드 - 틱톡] 최종 게시 직전까지 입력·설정 확인 완료 (실제 게시 안 함)", flush=True)
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