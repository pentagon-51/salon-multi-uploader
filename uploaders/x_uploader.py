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
    launch_persistent_chromium,
    record_platform_diagnostics,
    verify_publish_button_aim,
    auto_extract_and_save_profile_id,
    load_profile,
    get_region_tags,
    get_effective_banned_tags,
)
BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("x_cookies.json")
nl = chr(10)

def truncate_clean_text(text, max_len):
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    # 줄바꿈 우선, 없으면 공백 어절 단위로 깔끔하게 자름
    last_nl = cut.rfind(nl)
    if last_nl >= max_len // 2:
        return cut[:last_nl].strip()
    last_sp = cut.rfind(" ")
    if last_sp >= max_len // 2:
        return cut[:last_sp].strip()
    return cut.strip()

def is_x_logged_in(page):
    """엑스(X) 로그인/인증 플로우를 벗어나 실제 계정 로그인 상태인지 엄격히 판별"""
    url = (page.url or "").lower()
    if any(k in url for k in ("login", "/i/flow", "logout", "signup")):
        return False
    if is_any_locator_visible(page, 'a[href="/login"], a[data-testid="loginButton"], button:text-is("로그인"), button:text-is("Sign in")'):
        return False
    logged_in_sels = (
        'div[data-testid="tweetTextarea_0"], '
        'a[data-testid="SideNav_NewTweet_Button"], '
        'button[data-testid="SideNav_AccountSwitcher_Button"], '
        'div[role="textbox"]'
    )
    return page.locator(logged_in_sels).count() > 0

def upload_to_x(body="", tags=None, video_path=None, login_only=False, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [엑스(X)] 업로드할 영상 파일이 없습니다.", flush=True)
        return False
    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)
    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    filtered = [t for t in raw_list if t.lower() not in banned_tags]
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_only = [t for t in dict.fromkeys(filtered) if t not in region_set]
    tail_tag = region_tags[-1:] if region_tags else []
    tags_2 = (style_only[: (2 - len(tail_tag))] + tail_tag)[:2]
    tag_str = " ".join([f"#{t}" for t in dict.fromkeys(tags_2)])
    max_body_len = max(30, 135 - len(tag_str))
    trimmed_body = truncate_clean_text(clean_body, max_body_len)
    full_post = f"{trimmed_body}{nl}{nl}{tag_str}".strip()

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("엑스", mode=record_mode)
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

            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45000)
            time.sleep(4.0)

            clicked_login = [False]
            def _on_x_tick(pg, tick_idx, elapsed):
                for c_txt in ["Accept all cookies", "모든 쿠키 수락", "모두 수락"]:
                    try:
                        cb = pg.locator(f'button:has-text("{c_txt}")').first
                        if cb.count() > 0 and cb.is_visible(timeout=300):
                            cb.click(force=True)
                            time.sleep(0.6)
                    except Exception:
                        pass
                cur_u = (pg.url or "").lower()
                if not clicked_login[0] and "/i/flow" not in cur_u and "login" not in cur_u:
                    for lsel in ['a[data-testid="loginButton"]', 'a[href="/login"]']:
                        try:
                            lb = pg.locator(lsel).first
                            if lb.count() > 0 and lb.is_visible(timeout=400):
                                lb.click(force=True)
                                clicked_login[0] = True
                                print("👆 [엑스(X)] 로그인 화면으로 자동 이동했습니다.", flush=True)
                                time.sleep(1.5)
                                break
                        except Exception:
                            pass

            if not is_x_logged_in(page):
                wait_for_manual_login(
                    page,
                    "엑스(X)",
                    is_x_logged_in,
                    timeout_sec=180,
                    on_tick=_on_x_tick,
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "엑스(X)")

            try:
                new_x = auto_extract_and_save_profile_id(page, "x")
                if new_x:
                    print(f"🔗 [프로필 자동 연동] 엑스(X) 아이디(@{new_x}) 자동 저장 완료!", flush=True)
            except Exception:
                pass

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "엑스(X)")
                print("✅ [엑스(X)] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            if "compose" not in page.url:
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
                time.sleep(3.0)
            f_in = page.locator('input[data-testid="fileInput"], input[type="file"]').first
            f_in.wait_for(state="attached", timeout=20000); f_in.set_input_files(video_path); time.sleep(3.0)
            tbox = page.locator('div[data-testid="tweetTextarea_0"], div[role="textbox"]').first
            tbox.wait_for(state="visible", timeout=15000); tbox.click(force=True); time.sleep(0.4)
            copy_to_clipboard(full_post); page.keyboard.press(f"{MOD_KEY}+v"); time.sleep(1.5)
            post_btn = page.locator('button[data-testid="tweetButton"], button[data-testid="tweetButtonInline"]').first
            for i in range(45):
                if post_btn.get_attribute("disabled") is None and post_btn.get_attribute("aria-disabled") != "true": break
                time.sleep(1.5)
            if test_mode:
                aim_res = verify_publish_button_aim(
                    post_btn,
                    expected_keywords=["게시", "Post", "트윗"],
                    platform_name="엑스",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 엑스(X)] 최종 게시 버튼 조준 검증 통과 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "엑스(X)")
                wait_for_test_mode_close(context, "엑스(X)", stop_checker=stop_checker)
                return True

            time.sleep(1.5); post_btn.click(force=True); time.sleep(5.0)
            save_storage_state_safely(context, COOKIES_PATH, "엑스(X)")
            return True
        except InterruptedError as ie:
            record_platform_diagnostics("엑스", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("엑스", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [엑스(X)] 오류: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "엑스",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="엑스(X)",
            )
