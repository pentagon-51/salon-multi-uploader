import os, json, time, shutil
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
    finalize_playwright_session,
    launch_persistent_chromium,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("daangn_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
os.makedirs(DEBUG_DIR, exist_ok=True)

def is_daangn_logged_in(page):
    """당근 비즈프로필 로그인/시작하기 화면을 벗어나 관리 대시보드에 진입했는지 엄격히 판별"""
    url = (page.url or "").lower()
    if any(k in url for k in ("login", "signin", "/auth", "account")):
        return False
    logged_out_sels = (
        'button:text-is("로그인"), a:text-is("로그인"), '
        'button:has-text("로그인"), a:has-text("로그인"), '
        'button:has-text("비즈프로필 시작하기"), a:has-text("비즈프로필 시작하기"), '
        'button:text-is("시작하기"), a:text-is("시작하기")'
    )
    if is_any_locator_visible(page, logged_out_sels):
        return False
    return "business.daangn.com" in url


def upload_to_daangn(title="", body="", video_path=None, login_only=False, selected_images=None, stop_checker=None):
    valid_images = [p for p in (selected_images or []) if p and os.path.exists(p)][:4]
    has_video = bool(video_path and os.path.exists(video_path))
    if not login_only and not valid_images and not has_video:
        print("⚠️ [당근 비즈] 업로드할 사진 또는 영상 파일이 없습니다.", flush=True)
        return False

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("당근", mode=record_mode)
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
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            page.goto("https://business.daangn.com/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)

            clicked_login = [False]
            def _on_daangn_tick(pg, tick_idx, elapsed):
                if not clicked_login[0] and "login" not in (pg.url or "").lower():
                    for lsel in ['button:has-text("로그인")', 'a:has-text("로그인")', 'button:has-text("비즈프로필 시작하기")', 'a:has-text("비즈프로필 시작하기")', 'button:text-is("시작하기")', 'a:text-is("시작하기")']:
                        try:
                            lb = pg.locator(lsel).first
                            if lb.count() > 0 and lb.is_visible(timeout=400):
                                lb.click(force=True)
                                clicked_login[0] = True
                                print("👆 [당근 비즈] 로그인/시작하기 버튼을 자동 클릭했습니다.", flush=True)
                                time.sleep(1.5)
                                break
                        except Exception:
                            pass

            if not is_daangn_logged_in(page):
                wait_for_manual_login(
                    page,
                    "당근 비즈",
                    is_daangn_logged_in,
                    timeout_sec=180,
                    on_tick=_on_daangn_tick,
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈")

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈")
                print("✅ [당근 비즈] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            for nav_sel in ['a:has-text("소식 작성")', 'button:has-text("소식 작성")', 'a:has-text("소식 올리기")', 'a:has-text("소식")', 'button:has-text("소식 쓰기")']:
                try:
                    el = page.locator(nav_sel).first
                    if el.is_visible(timeout=2500):
                        el.click()
                        time.sleep(2.5)
                        break
                except Exception:
                    pass

            media_attached = False
            if valid_images:
                try:
                    f_in = page.locator('input[type="file"]').first
                    if f_in.count() > 0:
                        f_in.set_input_files(valid_images)
                        media_attached = True
                        print(f"🖼️ [당근 비즈] 고화질 사진 {len(valid_images)}장 첨부 완료!", flush=True)
                        time.sleep(3.5)
                except Exception as img_err:
                    print(f"⚠️ [당근 비즈] 사진 첨부 실패({img_err}) → 영상 첨부로 폴백합니다.", flush=True)

            if not media_attached and has_video:
                try:
                    f_in = page.locator('input[type="file"]').first
                    if f_in.count() > 0:
                        f_in.set_input_files(video_path)
                        time.sleep(3.5)
                except Exception:
                    pass

            try:
                t_in = page.locator('input[placeholder*="제목"], input[name*="title"]').first
                if t_in.is_visible(timeout=4000):
                    t_in.click(force=True)
                    time.sleep(0.3)
                    copy_to_clipboard(title[:50])
                    page.keyboard.press(f"{MOD_KEY}+v")
                    time.sleep(0.5)
            except Exception:
                pass

            b_in = page.locator('textarea[placeholder*="소식"], textarea[placeholder*="내용"], div[contenteditable="true"], textarea').first
            b_in.wait_for(state="visible", timeout=15000)
            b_in.click(force=True)
            time.sleep(0.3)
            copy_to_clipboard(body)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.5)

            sub_btn = page.locator('button:has-text("발행"), button:has-text("등록"), button:has-text("완료"), button[type="submit"]').last
            if sub_btn.count() > 0:
                sub_btn.click(force=True)
                time.sleep(5.0)

            save_storage_state_safely(context, COOKIES_PATH, "당근 비즈")
            return True
        except InterruptedError:
            return False
        except Exception as e:
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [당근 비즈] 오류: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "당근",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="당근 비즈",
            )