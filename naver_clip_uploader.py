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
    save_storage_state_safely,
    wait_for_manual_login,
    finalize_playwright_session,
    launch_persistent_chromium,
    load_profile,
    get_place_search_keyword,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("naver_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
os.makedirs(DEBUG_DIR, exist_ok=True)
nl = chr(10)

def select_clip_category(page):
    """네이버 클립 필수 항목인 [카테고리 *]에서 '뷰티 > 헤어스타일, 헤어케어'를 자동 선택"""
    # 1순위: 화면에 바로 보이는 추천 카테고리 칩('뷰티 > 헤어스타일, 헤어케어') 클릭
    chip_sels = [
        'button:has-text("헤어스타일, 헤어케어")',
        'span:has-text("헤어스타일, 헤어케어")',
        'div[role="button"]:has-text("헤어스타일, 헤어케어")',
        'button:has-text("헤어스타일")',
        ':text-is("뷰티 > 헤어스타일, 헤어케어")'
    ]
    for sel in chip_sels:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                el.scroll_into_view_if_needed()
                el.click(force=True)
                print("✅ [네이버 클립] 추천 카테고리('뷰티 > 헤어스타일, 헤어케어') 원클릭 선택 완료!", flush=True)
                time.sleep(1.0)
                return True
        except Exception:
            pass

    # 2순위: 1차 카테고리 -> '뷰티', 2차 카테고리 -> '헤어스타일, 헤어케어' 드롭다운 직접 선택
    try:
        c1 = page.locator('button:has-text("1차 카테고리"), div:text-is("1차 카테고리")').first
        if c1.is_visible(timeout=2000):
            c1.scroll_into_view_if_needed()
            c1.click(force=True)
            time.sleep(0.8)
            opt1 = page.locator('li:has-text("뷰티"), button:has-text("뷰티"), div[role="option"]:has-text("뷰티"), span:text-is("뷰티")').first
            if opt1.is_visible(timeout=2000):
                opt1.click(force=True)
                time.sleep(0.8)

        c2 = page.locator('button:has-text("2차 카테고리"), div:text-is("2차 카테고리")').first
        if c2.is_visible(timeout=2000):
            c2.click(force=True)
            time.sleep(0.8)
            opt2 = page.locator('li:has-text("헤어"), button:has-text("헤어"), div[role="option"]:has-text("헤어"), span:has-text("헤어스타일")').first
            if opt2.is_visible(timeout=2000):
                opt2.click(force=True)
                print("✅ [네이버 클립] 1·2차 드롭다운 카테고리 선택 완료!", flush=True)
                time.sleep(0.8)
                return True
    except Exception as e:
        print(f"⚠️ [네이버 클립] 카테고리 선택 예외: {e}", flush=True)
    return False

def attach_clip_place(page, place_name):
    """네이버 클립 업로드 화면에서 장소 태그(스마트플레이스) 검색·선택·적용 (실패 시 안전 폴백)"""
    target_place = (place_name or get_place_search_keyword()).strip()
    if not target_place:
        return False
    try:
        print(f"📍 [네이버 클립] 장소 태그('{target_place}') 추가 시도 중...", flush=True)
        open_sels = [
            'button:has-text("장소 추가")',
            'button:has-text("장소 태그")',
            'button:has-text("위치 추가")',
            'div[role="button"]:has-text("장소 추가")',
            'div[role="button"]:has-text("장소 태그")',
            'span:has-text("장소 추가")',
            'span:has-text("장소 태그")',
            'button:has-text("장소")',
        ]
        opened = False
        for sel in open_sels:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible(timeout=1200):
                    el.scroll_into_view_if_needed()
                    el.click(force=True)
                    time.sleep(1.0)
                    opened = True
                    break
            except Exception:
                pass

        input_sels = [
            'input[placeholder*="장소"]',
            'input[placeholder*="위치"]',
            'input[placeholder*="검색"]',
            'div[role="dialog"] input[type="text"]',
            'div[role="dialog"] input[type="search"]',
        ]
        search_in = None
        for isel in input_sels:
            try:
                cand = page.locator(isel).first
                if cand.count() > 0 and cand.is_visible(timeout=1500):
                    search_in = cand
                    break
            except Exception:
                pass

        if search_in is None:
            if not opened:
                print("ℹ️ [네이버 클립] 장소 추가 UI를 찾지 못해 장소 태그를 건너뛰고 진행합니다.", flush=True)
            else:
                print("ℹ️ [네이버 클립] 장소 검색 입력창이 보이지 않아 건너뛰고 진행합니다.", flush=True)
            return False

        search_in.click(force=True)
        time.sleep(0.3)
        search_in.fill("")
        time.sleep(0.2)
        search_in.fill(target_place)
        time.sleep(0.4)
        search_in.press("Enter")
        time.sleep(2.0)

        keyword = target_place.split()[0]
        result_sels = [
            f'div[role="dialog"] li:has-text("{target_place}")',
            f'div[role="dialog"] button:has-text("{target_place}")',
            f'div[role="option"]:has-text("{keyword}")',
            f'li:has-text("{keyword}")',
            f'div[role="dialog"] div[role="button"]:has-text("{keyword}")',
        ]
        picked = False
        for rsel in result_sels:
            try:
                item = page.locator(rsel).first
                if item.count() > 0 and item.is_visible(timeout=1500):
                    item.click(force=True)
                    picked = True
                    time.sleep(1.0)
                    break
            except Exception:
                pass

        if picked:
            for csel in [
                'div[role="dialog"] button:has-text("적용")',
                'div[role="dialog"] button:has-text("확인")',
                'div[role="dialog"] button:has-text("완료")',
                'div[role="dialog"] button:has-text("선택")',
            ]:
                try:
                    cbtn = page.locator(csel).last
                    if cbtn.count() > 0 and cbtn.is_visible(timeout=1000):
                        cbtn.click(force=True)
                        time.sleep(0.8)
                        break
                except Exception:
                    pass
            print(f"✅ [네이버 클립] 장소 태그('{target_place}') 적용 완료!", flush=True)
            return True

        print("ℹ️ [네이버 클립] 장소 검색 결과를 찾지 못해 장소 태그 없이 계속 진행합니다.", flush=True)
        try:
            page.keyboard.press("Escape")
            time.sleep(0.4)
        except Exception:
            pass
    except Exception as e:
        print(f"ℹ️ [네이버 클립] 장소 태그 추가 건너뜀({e}) — 정상 발행을 계속합니다.", flush=True)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return False

def is_naver_clip_logged_in(page):
    """네이버 클립 크리에이터(clipcreators.naver.com)에 로그인 완료된 상태인지 엄격히 판별"""
    url = (page.url or "").lower()
    if "nid.naver.com" in url or "nidlogin" in url or "/login" in url:
        return False
    if is_any_locator_visible(page, 'a:text-is("로그인"), button:text-is("로그인"), a:has-text("로그인"), button:has-text("로그인")'):
        return False
    return "clipcreators.naver.com" in url

def upload_to_naver_clip(title="", body="", tags=None, video_path=None, place_name=None, login_only=False, stop_checker=None):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [네이버 클립] 업로드할 영상 파일이 없습니다:", video_path, flush=True)
        return False

    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    filtered = [t for t in raw_list if t.lower() not in banned_tags]
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_only = [t for t in dict.fromkeys(filtered) if t not in region_set]
    final_5 = (style_only[: max(1, 5 - len(region_tags))] + region_tags)[:5]
    tag_str = " ".join([f"#{t}" for t in final_5])

    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    full_desc = f"{clean_body}{nl}{nl}{tag_str}".strip() if tag_str else clean_body

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("네이버클립", mode=record_mode)
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

            page.goto("https://clipcreators.naver.com/web", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)

            clicked_login_btn = [False]
            def _on_clip_tick(pg, tick_idx, elapsed):
                if not clicked_login_btn[0] and "nid.naver.com" not in (pg.url or "").lower():
                    for lsel in ['button:has-text("로그인")', 'a:has-text("로그인")']:
                        try:
                            lbtn = pg.locator(lsel).first
                            if lbtn.count() > 0 and lbtn.is_visible(timeout=400):
                                lbtn.click(force=True)
                                clicked_login_btn[0] = True
                                print("👆 [네이버 클립] 로그인 버튼을 자동 클릭해 로그인 화면으로 이동했습니다.", flush=True)
                                time.sleep(1.5)
                                break
                        except Exception:
                            pass

            if not is_naver_clip_logged_in(page):
                wait_for_manual_login(
                    page,
                    "네이버 클립",
                    is_naver_clip_logged_in,
                    timeout_sec=180,
                    on_tick=_on_clip_tick,
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "네이버 클립")

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "네이버 클립")
                print("✅ [네이버 클립] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            # 1단계: 우측 상단 [+ 업로드] 클릭
            for up_sel in ['button:has-text("업로드")', 'a:has-text("업로드")', 'button:has-text("클립 만들기")', 'a:has-text("클립 만들기")']:
                try:
                    btn = page.locator(up_sel).first
                    if btn.is_visible(timeout=2500):
                        btn.click(force=True)
                        time.sleep(1.2)
                        break
                except Exception:
                    pass

            # 2단계: 드롭다운의 [동영상 업로드] 클릭
            file_attached = False
            sub_sels = [
                'button:has-text("동영상 업로드")',
                'a:has-text("동영상 업로드")',
                'li:has-text("동영상 업로드")',
                'div[role="menuitem"]:has-text("동영상 업로드")',
                'span:text-is("동영상 업로드")'
            ]
            for s_sel in sub_sels:
                try:
                    sub_el = page.locator(s_sel).first
                    if sub_el.is_visible(timeout=2000):
                        try:
                            with page.expect_file_chooser(timeout=3500) as fc_info:
                                sub_el.click(force=True)
                            fc_info.value.set_files(video_path)
                            file_attached = True
                        except Exception:
                            time.sleep(2.0)
                        break
                except Exception:
                    pass

            # 3단계: 업로드 페이지의 [파일 선택] 버튼 또는 input[type=file]에 영상 첨부
            if not file_attached:
                for page_btn_sel in ['button:has-text("파일 선택")', 'button:has-text("동영상 선택")', 'label:has-text("파일 선택")']:
                    try:
                        p_btn = page.locator(page_btn_sel).first
                        if p_btn.is_visible(timeout=2500):
                            with page.expect_file_chooser(timeout=5000) as fc_info:
                                p_btn.click(force=True)
                            fc_info.value.set_files(video_path)
                            file_attached = True
                            break
                    except Exception:
                        pass

            if not file_attached:
                f_in = page.locator('input[type="file"]').first
                f_in.wait_for(state="attached", timeout=15000)
                f_in.set_input_files(video_path)

            print("✅ [네이버 클립] 영상 파일 첨부 완료! 설명 입력창 대기 중...", flush=True)
            time.sleep(4.5)

            # 4단계: 설명(본문 + 해시태그) 입력
            b_in = page.locator('textarea[placeholder*="경험을 기록"], textarea[placeholder*="내용"], textarea[placeholder*="설명"], div[contenteditable="true"], textarea').first
            b_in.wait_for(state="visible", timeout=15000)
            b_in.click(force=True)
            time.sleep(0.4)
            copy_to_clipboard(full_desc)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.0)
            page.keyboard.press("Space")
            time.sleep(0.5)

            # 5단계: 필수 항목인 [카테고리 *] 선택 ('뷰티 > 헤어스타일, 헤어케어')
            select_clip_category(page)

            # 5-2단계: 장소 태그(스마트플레이스) 추가 시도 (안전 폴백)
            attach_clip_place(page, place_name)

            # 6단계: 동영상 인코딩 완료 대기 (최대 60초)
            print("⏳ [네이버 클립] 동영상 인코딩 완료 대기 중...", flush=True)
            for _ in range(40):
                enc = page.locator('text="인코딩 중", text="동영상 인코딩 진행 중"')
                if enc.count() == 0 or not enc.first.is_visible():
                    print("✅ [네이버 클립] 동영상 인코딩 완료!", flush=True)
                    break
                time.sleep(1.5)

            # 7단계: 하단 [등록] 버튼 클릭
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.0)
            sub_btn = page.locator('button:text-is("등록"), button:has-text("등록"), button:has-text("발행")').last
            if sub_btn.count() > 0:
                sub_btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                sub_btn.click(force=True)
                time.sleep(3.0)

                # 확인 모달이 한 번 더 뜰 경우 클릭
                confirm_modal = page.locator('div[role="dialog"] button:has-text("확인"), div[role="dialog"] button:has-text("등록")').last
                if confirm_modal.count() > 0 and confirm_modal.is_visible():
                    confirm_modal.click(force=True)
                time.sleep(4.0)

            save_storage_state_safely(context, COOKIES_PATH, "네이버 클립")
            return True
        except InterruptedError:
            return False
        except Exception as e:
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [네이버 클립] 오류: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "네이버클립",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="네이버 클립",
            )