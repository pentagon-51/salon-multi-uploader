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

            def _get_active_ig_dialog():
                dlgs = page.locator('div[role="dialog"]')
                try:
                    if dlgs.count() > 0:
                        return dlgs.last
                except Exception:
                    pass
                return page

            def _is_back_or_close_control(el):
                """모달 좌상단 뒤로가기(←)·닫기(×) 오클릭 원천 차단 판별"""
                try:
                    al = (el.get_attribute("aria-label") or "").strip().lower()
                    if any(k in al for k in ("뒤로", "back", "닫기", "close", "이전", "previous")):
                        return True
                    if el.locator('svg[aria-label*="뒤로"], svg[aria-label*="Back"], svg[aria-label*="닫기"], svg[aria-label*="Close"]').count() > 0:
                        return True
                except Exception:
                    pass
                return False

            def _safe_scroll_into_view(el, timeout_ms=2000):
                """Windows 125%/150% 디스플레이 배율에서도 요소가 뷰포트 밖으로 밀리지 않도록 안전 스크롤"""
                try:
                    el.scroll_into_view_if_needed(timeout=timeout_ms)
                except Exception:
                    pass

            def _is_settings_or_toggle_row(el):
                """하단 고급 설정·Facebook/Threads 공유 토글 영역의 버튼인지 DOM 구조로 판별"""
                try:
                    return bool(el.evaluate(
                        """(node) => {
                            let cur = node;
                            for (let i = 0; i < 4 && cur; i++) {
                                if (cur.querySelector && cur.querySelector('input[type="checkbox"], input[role="switch"], [role="switch"]')) {
                                    return true;
                                }
                                cur = cur.parentElement;
                            }
                            return false;
                        }"""
                    ))
                except Exception:
                    return False

            def _find_modal_header_action_btn(exact_labels, timeout_ms=2500):
                """인스타그램 작성 모달 우측 상단 헤더 액션 버튼(다음/공유하기)만 정확 텍스트(:text-is)와
                DOM 컨테이너 + 비율 기반 위치(DPI 125%/150% 무관)로 안전 탐색.
                하단 'Facebook에 공유하기' 토글이나 좌상단 뒤로가기(←) 버튼 오클릭을 엄격히 차단."""
                dlg = _get_active_ig_dialog()
                dlg_box = None
                try:
                    if dlg is not page:
                        dlg_box = dlg.bounding_box()
                except Exception:
                    dlg_box = None

                for lbl in exact_labels:
                    sel = f'div[role="button"]:text-is("{lbl}"), button:text-is("{lbl}")'
                    for scope in ([dlg, page] if dlg is not page else [page]):
                        try:
                            pool = scope.locator(sel)
                            cnt = min(pool.count(), 6)
                        except Exception:
                            cnt = 0
                        for idx in range(cnt):
                            cand = pool.nth(idx)
                            try:
                                if not cand.is_visible(timeout=min(timeout_ms, 1200)):
                                    continue
                                if _is_back_or_close_control(cand) or _is_settings_or_toggle_row(cand):
                                    continue
                                if dlg_box and dlg_box.get("width", 0) > 100 and dlg_box.get("height", 0) > 100:
                                    cbox = cand.bounding_box()
                                    if cbox:
                                        cx = cbox["x"] + cbox["width"] / 2.0
                                        cy = cbox["y"] + cbox["height"] / 2.0
                                        # 고정 픽셀 대신 모달 비율(DPI 배율 무관)로 좌측 절반(← 뒤로가기) 및 하단 본문/고급 설정 제외
                                        if cx < dlg_box["x"] + dlg_box["width"] * 0.5:
                                            continue
                                        if cy > dlg_box["y"] + dlg_box["height"] * 0.25:
                                            continue
                                return cand
                            except Exception:
                                continue
                return None

            for step_i in range(2):
                n_btn = _find_modal_header_action_btn(["다음", "Next"], timeout_ms=2500)
                if n_btn is not None:
                    try:
                        _safe_scroll_into_view(n_btn)
                        n_btn.click(timeout=3500)
                        time.sleep(1.8)
                    except Exception as nxt_err:
                        print(f"ℹ️ [인스타그램] 다음 단계 클릭 재시도({step_i + 1}): {nxt_err}", flush=True)

            dlg_cap = _get_active_ig_dialog()
            cap_box = dlg_cap.locator('div[aria-label="문구를 입력하세요..."], div[aria-label="Write a caption..."], div[role="textbox"]').first
            cap_box.wait_for(state="visible", timeout=10000)
            _safe_scroll_into_view(cap_box)
            cap_box.click(timeout=3000); time.sleep(0.5)
            copy_to_clipboard(full_caption)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.2)
            print("✍️ [인스타그램] 캡션·해시태그 입력 완료", flush=True)

            # 위치 추가: 모달 내부 위치 입력칸·드롭다운만 짧은 타임아웃(2.5초) + scroll_into_view_if_needed로 처리
            try:
                if target_place:
                    dlg_loc = _get_active_ig_dialog()
                    loc_input = dlg_loc.locator('input[placeholder*="위치"], input[placeholder*="location"], input[name="queryBox"], input[name="creation-location-input"]').first
                    if loc_input.is_visible(timeout=2500):
                        _safe_scroll_into_view(loc_input)
                        loc_input.click(timeout=2500)
                        time.sleep(0.4)
                        loc_input.fill(target_place, timeout=2500)
                        time.sleep(1.8)
                        loc_box = None
                        try:
                            loc_box = loc_input.bounding_box()
                        except Exception:
                            loc_box = None
                        loc_kw = target_place.split()[0]
                        loc_pool = dlg_loc.locator(f'div[role="button"]:has-text("{loc_kw}"), button:has-text("{loc_kw}")')
                        loc_clicked = False
                        for li in range(min(loc_pool.count(), 8)):
                            cand_loc = loc_pool.nth(li)
                            try:
                                if not cand_loc.is_visible(timeout=600):
                                    continue
                                if _is_back_or_close_control(cand_loc) or _is_settings_or_toggle_row(cand_loc):
                                    continue
                                ctxt = re.sub(r"\s+", " ", (cand_loc.inner_text(timeout=500) or "")).strip()
                                if not ctxt or len(ctxt) > 120:
                                    continue
                                if any(bad in ctxt for bad in ("공유하기", "Share", "고급 설정", "Advanced settings", "다음", "Next")):
                                    continue
                                if loc_box:
                                    cbox = cand_loc.bounding_box()
                                    if cbox and cbox["y"] < loc_box["y"] + loc_box["height"] - 4:
                                        continue
                                _safe_scroll_into_view(cand_loc)
                                cand_loc.click(timeout=2500)
                                loc_clicked = True
                                time.sleep(0.8)
                                print(f"📍 [인스타그램] 위치 선택 완료: {ctxt[:40]}", flush=True)
                                break
                            except Exception:
                                continue
                        if not loc_clicked:
                            print("ℹ️ [인스타그램] 위치 드롭다운 후보 미감지 — 위치 선택을 건너뛰고 공유 단계로 진행합니다.", flush=True)
            except Exception as loc_err:
                print(f"ℹ️ [인스타그램] 위치 추가 건너뜀: {loc_err}", flush=True)

            # 최종 [공유하기] 버튼: 모달 우측 상단 헤더의 정확 일치(:text-is) 버튼만 탐색 (하단 공유 토글·좌상단 ← 뒤로가기 차단)
            share_btn = _find_modal_header_action_btn(["공유하기", "Share"], timeout_ms=2500)
            if share_btn is not None:
                _safe_scroll_into_view(share_btn)
                print("🎯 [인스타그램] 모달 우측 상단 [공유하기] 버튼 탐색 완료", flush=True)

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
                share_btn.click(timeout=5000)
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