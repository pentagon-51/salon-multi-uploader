import os, time, shutil
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
    verify_publish_button_aim,
    normalize_branch_name,
    load_profile,
    get_salon_name,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("pinterest_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
os.makedirs(DEBUG_DIR, exist_ok=True)

def is_pinterest_logged_in(page):
    """핀터레스트 로그인 폼·모달·로그인 버튼이 없고 실제 로그인된 상태인지 엄격히 판별"""
    url = (page.url or "").lower()
    if any(k in url for k in ("login", "signup", "/auth")):
        return False
    if is_any_locator_visible(page, 'input[type="password"]'):
        return False
    if is_any_locator_visible(
        page,
        'button[data-test-id="simple-login-button"], button:text-is("로그인"), button:text-is("Log in"), a:text-is("로그인"), a:text-is("Log in")',
    ):
        return False
    if page.locator('input[type="file"], [data-test-id="header-profile"], [data-test-id="header-accounts-options-button"], [data-test-id="pin-builder"]').count() > 0:
        return True
    return "pin-creation-tool" in url or "pin-builder" in url


def upload_to_pinterest(title="", description="", tags=None, video_path=None, link=None, board_name=None, board_id="", login_only=False, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [핀터레스트] 업로드할 영상 파일을 찾을 수 없습니다:", video_path, flush=True)
        return None

    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)

    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    filtered = [t for t in raw_list if t.lower() not in banned_tags]
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_only = [t for t in dict.fromkeys(filtered) if t not in region_set]
    tag_list = (style_only[: max(1, 5 - len(region_tags))] + region_tags)[:5]

    hash_str = " ".join([f"#{t}" for t in dict.fromkeys(tag_list)])
    full_desc = f"{(description or '').strip()}\n\n{hash_str}".strip() if hash_str else (description or "").strip()
    target_link = (link if link is not None else prof.get("booking_url", "") or "").strip()
    target_board_name = (board_name if board_name is not None else prof.get("pinterest_board", "") or "").strip()

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("핀터레스트", mode=record_mode)
    browser = None
    context = None
    page = None
    video_obj = None

    with sync_playwright() as p:
        try:
            context = launch_persistent_chromium(
                p,
                headless=False,
                viewport={"width": 1280, "height": 900},
                record_video_dir=record_dir,
                record_video_size={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            page.goto("https://kr.pinterest.com/pin-creation-tool/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)

            clicked_login = [False]
            def _on_pin_tick(pg, tick_idx, elapsed):
                for c_txt in ["모두 수락", "Accept all"]:
                    try:
                        cb = pg.locator(f'button:has-text("{c_txt}")').first
                        if cb.count() > 0 and cb.is_visible(timeout=300):
                            cb.click(force=True)
                            time.sleep(0.6)
                    except Exception:
                        pass
                if not clicked_login[0] and not is_any_locator_visible(pg, 'input[type="password"]'):
                    for lsel in ['button[data-test-id="simple-login-button"]', 'button:text-is("로그인")', 'button:text-is("Log in")']:
                        try:
                            lb = pg.locator(lsel).first
                            if lb.count() > 0 and lb.is_visible(timeout=400):
                                lb.click(force=True)
                                clicked_login[0] = True
                                print("👆 [핀터레스트] 로그인 창을 자동으로 열었습니다.", flush=True)
                                time.sleep(1.5)
                                break
                        except Exception:
                            pass

            if not is_pinterest_logged_in(page):
                wait_for_manual_login(
                    page,
                    "핀터레스트",
                    is_pinterest_logged_in,
                    timeout_sec=180,
                    on_tick=_on_pin_tick,
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "핀터레스트")

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "핀터레스트")
                print("✅ [핀터레스트] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            if "pin-creation-tool" not in page.url and "pin-builder" not in page.url:
                page.goto("https://kr.pinterest.com/pin-creation-tool/", wait_until="domcontentloaded")
                time.sleep(3.5)

            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=20000)
            file_input.set_input_files(video_path)

            title_input = page.locator('input[placeholder*="제목"], textarea[placeholder*="제목"], input[placeholder*="Title"], [data-test-id*="pin-draft-title"] input, input[id*="storyboard-selector-title"]').first
            title_input.wait_for(state="visible", timeout=45000)
            time.sleep(3)

            title_input.click(force=True); time.sleep(0.3)
            salon_name = get_salon_name(prof, fallback="헤어살롱")
            copy_to_clipboard(normalize_branch_name(title or f"{salon_name} 헤어스타일")[:95])
            page.keyboard.press(f"{MOD_KEY}+v"); time.sleep(0.6)

            desc_area = page.locator('div[aria-label*="설명"], div[contenteditable="true"], [data-test-id*="pin-draft-description"] div[contenteditable="true"]').first
            if desc_area.count() > 0:
                desc_area.click(force=True); time.sleep(0.3)
                copy_to_clipboard(normalize_branch_name(full_desc)[:480])
                page.keyboard.press(f"{MOD_KEY}+v"); time.sleep(0.6)

            if target_link:
                link_input = page.locator('input[placeholder*="링크"], input[placeholder*="link"], input[type="url"], [data-test-id*="pin-draft-link"] input').first
                if link_input.count() > 0:
                    link_input.click(force=True); time.sleep(0.3)
                    copy_to_clipboard(target_link)
                    page.keyboard.press(f"{MOD_KEY}+v"); time.sleep(0.6)

            try:
                board_btn = page.locator('[data-test-id="board-dropdown-select-button"], button:has-text("보드 선택"), button:has-text("Select a board")').first
                if board_btn.count() > 0 and board_btn.is_visible(timeout=2000):
                    board_btn.click(); time.sleep(1.5)
                    selected = False
                    if target_board_name:
                        target_board = page.locator(f'div[role="button"]:has-text("{target_board_name}"), li:has-text("{target_board_name}")').first
                        if target_board.count() > 0:
                            target_board.click()
                            selected = True
                    if not selected:
                        first_board = page.locator('[data-test-id*="board-row"]').first
                        if first_board.count() > 0: first_board.click()
                    time.sleep(1.0)
            except Exception: pass

            publish_btn = page.locator('[data-test-id="board-dropdown-save-button"], button:has-text("발행"), button:has-text("게시"), button:has-text("Publish")').last
            if publish_btn.count() > 0:
                for _ in range(40):
                    dis = publish_btn.get_attribute("disabled")
                    if dis is None and not publish_btn.is_disabled():
                        break
                    time.sleep(1.5)

            if test_mode:
                aim_res = verify_publish_button_aim(
                    publish_btn,
                    expected_keywords=["발행", "게시", "Publish"],
                    platform_name="핀터레스트",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 핀터레스트] 최종 발행 버튼 조준 검증 통과 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "핀터레스트")
                wait_for_test_mode_close(context, "핀터레스트", stop_checker=stop_checker)
                return "test_mode_ok"

            if publish_btn.count() > 0:
                time.sleep(1.5)
                publish_btn.click(force=True)
                time.sleep(8)

            save_storage_state_safely(context, COOKIES_PATH, "핀터레스트")
            pin_id = (prof.get("pinterest_id") or "").strip()
            return f"https://kr.pinterest.com/{pin_id}/" if pin_id else "https://kr.pinterest.com/"

        except InterruptedError as ie:
            record_platform_diagnostics("핀터레스트", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return None
        except Exception as e:
            record_platform_diagnostics("핀터레스트", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [핀터레스트] 오류: {str(e)}", flush=True)
            return None
        finally:
            finalize_playwright_session(
                "핀터레스트",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="핀터레스트",
            )
# === 파일 끝 ===