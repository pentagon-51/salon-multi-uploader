import os
import re
import time
from difflib import SequenceMatcher
from playwright.sync_api import sync_playwright
from os_utils import (
    MOD_KEY,
    copy_to_clipboard,
    get_base_dir,
    get_auth_file,
    create_blackbox_record_dir,
    is_any_locator_visible,
    save_storage_state_safely,
    wait_for_manual_login,
    wait_for_test_mode_close,
    finalize_playwright_session,
    launch_persistent_chromium,
    record_platform_diagnostics,
    load_profile,
    get_salon_name,
    get_region_tags,
    get_effective_banned_tags,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("facebook_cookies.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_records")
os.makedirs(DEBUG_DIR, exist_ok=True)
nl = chr(10)


def _normalize_match_text(text):
    return re.sub(r"[\s·•\-_|/()\[\]{}]+", "", str(text or "")).strip().lower()


def _build_facebook_caption(body, tags, prof):
    banned_tags = get_effective_banned_tags(prof)
    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    filtered = [t for t in raw_list if t.lower() not in banned_tags]
    region_tags = get_region_tags(prof)
    region_set = set(region_tags)
    style_only = [t for t in dict.fromkeys(filtered) if t not in region_set]
    final_5 = (style_only[: max(1, 5 - len(region_tags))] + region_tags)[:5]
    tag_str = " ".join(["#" + t for t in dict.fromkeys(final_5)])
    return (clean_body + ((nl + nl + tag_str) if tag_str else "")).strip()


def dismiss_facebook_popups(page):
    """페이스북 / 메타 비즈니스 스위트 쿠키 배너 및 안내 팝업 안전 처리"""
    if page is None:
        return
    cookie_btns = [
        'button:has-text("모든 쿠키 허용")',
        'button:has-text("Allow all cookies")',
        'button:has-text("필수 및 선택적 쿠키 허용")',
        'button:has-text("Allow essential and optional cookies")',
        'div[role="button"]:text-is("모든 쿠키 허용")',
        'div[role="button"]:text-is("Allow all cookies")',
    ]
    for sel in cookie_btns:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=250):
                el.click(force=True)
                time.sleep(0.6)
        except Exception:
            pass

    url = (page.url or "").lower()
    if any(k in url for k in ("/login", "login.php", "checkpoint", "recover")):
        return
    if is_any_locator_visible(page, 'input[name="email"], input[name="pass"]'):
        return

    for txt in ("나중에 하기", "Not Now", "지금 안 함", "닫기"):
        try:
            btn = page.locator(f'div[role="dialog"] button:text-is("{txt}"), div[role="dialog"] div[role="button"]:text-is("{txt}")').first
            if btn.count() > 0 and btn.is_visible(timeout=250):
                btn.click(force=True)
                time.sleep(0.5)
        except Exception:
            pass


def _has_facebook_auth_cookies(page):
    """현재 브라우저 컨텍스트에 페이스북 실제 로그인 인증 쿠키(c_user + xs)가 모두 존재하는지 확인"""
    if page is None:
        return False
    try:
        cookies = page.context.cookies() or []
    except Exception:
        return False
    now_ts = time.time()
    has_cuser = False
    has_xs = False
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".").lower()
        if not (dom == "facebook.com" or dom.endswith(".facebook.com")):
            continue
        exp = c.get("expires")
        if exp is not None:
            try:
                if float(exp) > 0 and float(exp) < now_ts:
                    continue
            except Exception:
                pass
        cname = (c.get("name") or "").strip().lower()
        cval = str(c.get("value") or "").strip()
        if cname == "c_user" and cval.isdigit() and len(cval) >= 4:
            has_cuser = True
        elif cname == "xs" and len(cval) >= 10:
            has_xs = True
    return has_cuser and has_xs


def is_facebook_logged_in(page):
    """현재 URL(/login, checkpoint, recover) 및 로그인 폼(email/pass) 노출 여부를 검사해
    페이스북(개인 / 메타 비즈니스 스위트) 실제 로그인 완료 상태를 정밀 판별"""
    if page is None:
        return False
    try:
        url = (page.url or "").lower()
    except Exception:
        return False

    if not url or url == "about:blank":
        return False
    if "facebook.com" not in url:
        return False

    # 로그인·보안 확인·계정 복구 경로는 무조건 비로그인
    if any(k in url for k in ("/login", "login.php", "checkpoint", "recover", "two_step_verification", "/r.php", "/reg/")):
        return False

    # 로그인 입력 폼이 보이면 비로그인
    if is_any_locator_visible(page, 'input[name="email"], input[name="pass"], button[name="login"], form[action*="login"]'):
        return False

    # 메타 비즈니스 스위트 / 페이스북 로그인 전용 UI 신호 확인
    logged_in_sels = (
        'div[role="navigation"], '
        'a[aria-label="홈"], a[aria-label="Home"], '
        'div[aria-label="내 프로필"], div[aria-label="Your profile"], '
        'div[aria-label="계정"], div[aria-label="Account"], '
        'a[href*="/reels/create"], a[href*="/latest/"], '
        'button:has-text("릴스 만들기"), div[role="button"]:has-text("릴스 만들기"), '
        'button:has-text("Create reel"), div[role="button"]:has-text("Create reel"), '
        'input[type="file"], div[role="textbox"][contenteditable="true"]'
    )
    if _has_facebook_auth_cookies(page):
        return True
    try:
        return is_any_locator_visible(page, logged_in_sels)
    except Exception:
        return False


def _is_image_only_file_input(loc):
    """input[type='file']의 accept 속성이 이미지 전용(예: image/*)이고 video/mp4를 허용하지 않으면 True 반환"""
    try:
        acc = (loc.get_attribute("accept") or "").strip().lower()
    except Exception:
        return False
    if not acc:
        return False
    has_image = "image" in acc
    has_video = ("video" in acc) or ("mp4" in acc) or ("quicktime" in acc) or ("*/*" in acc)
    return has_image and not has_video


def _attach_video_to_composer(page, video_path):
    """원본 .mp4 파일을 페이스북/비즈니스 스위트 릴스 업로드 input에 안전 첨부.
    홈 화면의 숨겨진 사진 전용 input(accept='image/*')에 오첨부되어 Can't Read Files가 나는 버그를 원천 차단."""
    video_input_sels = [
        'input[type="file"][accept*="video"]',
        'input[type="file"][accept*="mp4"]',
    ]
    add_btn_sels = [
        'div[role="button"]:has-text("동영상 추가")',
        'button:has-text("동영상 추가")',
        'span:has-text("동영상 추가")',
        'div[role="button"]:has-text("Add video")',
        'button:has-text("Add video")',
        'span:has-text("Add video")',
        'div[role="button"]:has-text("영상 업로드")',
        'button:has-text("Upload video")',
    ]

    # 1순위: video / mp4 전용 input[type="file"] 탐색 (최대 ~6초 대기)
    for _ in range(8):
        for sel in video_input_sels:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for idx in range(cnt):
                    cand = loc.nth(idx)
                    if _is_image_only_file_input(cand):
                        continue
                    cand.set_input_files(video_path)
                    return True
            except Exception:
                pass
        time.sleep(0.7)

    # 2순위: '동영상 추가 / Add video' 버튼 클릭 후 file chooser로 안전 첨부
    for sel in add_btn_sels:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                with page.expect_file_chooser(timeout=8000) as fc_info:
                    btn.click(force=True)
                fc_info.value.set_files(video_path)
                return True
        except Exception:
            pass

    # 3순위 (엄격 조건부 폴백): 릴스 작성기 URL(reels_composer / /reels/create)이 확실히 확인되고,
    # accept 속성이 이미지 전용이 아닌 일반 input[type="file"]에만 제한적 허용
    cur_url = (page.url or "").lower()
    if "reels_composer" in cur_url or "/reels/create" in cur_url:
        try:
            loc = page.locator('input[type="file"]')
            cnt = loc.count()
            for idx in range(cnt):
                cand = loc.nth(idx)
                if _is_image_only_file_input(cand):
                    continue
                cand.set_input_files(video_path)
                return True
        except Exception:
            pass

    return False


def _click_visible_button_by_texts(page, texts, timeout_ms=2500):
    """우선순위 텍스트 목록 중 현재 화면에 활성화된 버튼/role=button을 찾아 클릭"""
    for txt in texts:
        sels = [
            f'div[role="button"]:text-is("{txt}")',
            f'button:text-is("{txt}")',
            f'div[role="button"]:has-text("{txt}")',
            f'button:has-text("{txt}")',
        ]
        for sel in sels:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for idx in range(cnt - 1, -1, -1):
                    el = loc.nth(idx)
                    if not el.is_visible(timeout=min(timeout_ms, 600)):
                        continue
                    try:
                        if el.get_attribute("aria-disabled") == "true" or el.is_disabled():
                            continue
                    except Exception:
                        pass
                    el.scroll_into_view_if_needed()
                    el.click(force=True)
                    return txt
            except Exception:
                pass
    return ""


def _fill_caption_textbox(page, full_caption):
    """릴스 본문 + 해시태그 입력칸을 찾아 클립보드 붙여넣기(실패 시 insert_text)로 안전 입력"""
    cap_sels = [
        'div[aria-label*="릴스 설명"]',
        'div[aria-label*="Describe your reel"]',
        'div[aria-label*="설명을 입력"]',
        'div[aria-label*="문구를 입력"]',
        'div[aria-label*="Write a caption"]',
        'div[aria-label*="설명"]',
        'div[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea',
    ]
    target = None
    for _ in range(14):
        for sel in cap_sels:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    target = loc
                    break
            except Exception:
                pass
        if target is not None:
            break
        time.sleep(0.8)

    if target is None:
        return False

    target.scroll_into_view_if_needed()
    target.click(force=True)
    time.sleep(0.4)
    try:
        page.keyboard.press(f"{MOD_KEY}+a")
        page.keyboard.press("Backspace")
        time.sleep(0.2)
    except Exception:
        pass

    pasted = False
    if copy_to_clipboard(full_caption):
        try:
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.0)
            pasted = True
        except Exception:
            pasted = False
    if not pasted:
        page.keyboard.insert_text(full_caption)
        time.sleep(1.0)
    return True


def _select_facebook_business_page(page, target_page=""):
    """메타 비즈니스 스위트에서 사용자 설정 매장명(target_page)과 일치하는 페이지를 3단계 우선순위로 선택.
    1순위: 정확 일치 -> 2순위: 유사도/부분 매칭 -> 3순위: 첫 번째 페이지 폴백(또는 현재 활성 페이지 유지)"""
    if page is None:
        return ""
    norm_target = _normalize_match_text(target_page)
    target_tokens = [tok for tok in re.split(r"[\s·•\-_|/]+", str(target_page or "").strip()) if len(tok) >= 2]

    # 현재 화면 상단/좌측 페이지 선택기에 이미 대상 매장명이 활성화되어 있으면 추가 전환 불필요
    if norm_target:
        try:
            header_txt = page.evaluate(
                """() => {
                    const nav = document.querySelector('div[role="navigation"], nav, header') || document.body;
                    return (nav.innerText || '').slice(0, 600);
                }"""
            )
            if norm_target in _normalize_match_text(header_txt):
                print(f"✅ [페이스북(매장)] 현재 선택된 페이지가 '{target_page}'와 일치합니다.", flush=True)
                return target_page
        except Exception:
            pass

    # 좌측 상단 페이지/비즈니스 계정 스위처 열기 시도
    switcher_sels = [
        'div[role="navigation"] div[role="button"][aria-haspopup="listbox"]',
        'div[role="navigation"] button[aria-haspopup="listbox"]',
        'div[role="navigation"] div[role="button"][aria-haspopup="menu"]',
        'div[role="navigation"] div[role="combobox"]',
        '[aria-label*="계정 전환"], [aria-label*="Switch account"], [aria-label*="비즈니스 계정"], [aria-label*="Business account"]',
    ]
    opened_switcher = False
    for sel in switcher_sels:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=1500):
                el.click(force=True)
                time.sleep(1.5)
                opened_switcher = True
                break
        except Exception:
            pass

    if not opened_switcher:
        return ""

    # 드롭다운/리스트박스에서 선택 가능한 페이지 항목 수집
    option_sels = 'div[role="listbox"] [role="option"], div[role="menu"] [role="menuitem"], div[role="dialog"] [role="option"], div[role="dialog"] [role="button"]'
    candidates = []
    try:
        loc = page.locator(option_sels)
        cnt = min(loc.count(), 40)
        for idx in range(cnt):
            cand = loc.nth(idx)
            try:
                if not cand.is_visible(timeout=200):
                    continue
                txt = re.sub(r"\s+", " ", (cand.inner_text() or "").strip())
                if not txt or len(txt) > 90:
                    continue
                low_t = txt.lower()
                if any(skip in low_t for skip in ("설정", "settings", "도움말", "help", "로그아웃", "log out", "만들기", "create")):
                    continue
                candidates.append((idx, txt, cand))
            except Exception:
                pass
    except Exception:
        pass

    if not candidates:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return ""

    chosen = None
    match_tier = ""

    # 1순위: 정확 일치
    if norm_target:
        for idx, txt, cand in candidates:
            if _normalize_match_text(txt) == norm_target:
                chosen = (txt, cand)
                match_tier = "1순위(정확 일치)"
                break

    # 2순위: 부분 일치 / 토큰 매칭 / 유사도 매칭
    if chosen is None and norm_target:
        best_score = 0.0
        best_item = None
        for idx, txt, cand in candidates:
            nt = _normalize_match_text(txt)
            if not nt:
                continue
            score = SequenceMatcher(None, norm_target, nt).ratio()
            if norm_target in nt or nt in norm_target:
                score = max(score, 0.85)
            elif target_tokens and any(_normalize_match_text(tok) in nt for tok in target_tokens):
                score = max(score, 0.65)
            if score > best_score:
                best_score = score
                best_item = (txt, cand)
        if best_item is not None and best_score >= 0.45:
            chosen = best_item
            match_tier = f"2순위(유사도 {best_score:.2f})"

    # 3순위: 첫 번째 페이지 폴백
    if chosen is None:
        chosen = (candidates[0][1], candidates[0][2])
        match_tier = "3순위(첫 번째 페이지 폴백)"

    chosen_txt, chosen_loc = chosen
    try:
        chosen_loc.scroll_into_view_if_needed()
        chosen_loc.click(force=True)
        print(f"🏪 [페이스북(매장)] 페이지 선택 완료 [{match_tier}]: {chosen_txt}", flush=True)
        time.sleep(2.5)
        return chosen_txt
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return ""


def _is_business_suite_reel_composer_ready(page):
    """현재 페이지가 메타 비즈니스 스위트 홈(/latest/home)이 아닌 실제 릴스 작성 화면인지 엄격 판별"""
    if page is None:
        return False
    try:
        cur_url = (page.url or "").lower()
    except Exception:
        return False
    if "reels_composer" in cur_url:
        return True
    if "/latest/home" in cur_url:
        return False
    try:
        if page.locator('input[type="file"][accept*="video"], input[type="file"][accept*="mp4"]').count() > 0:
            return True
    except Exception:
        pass
    try:
        if is_any_locator_visible(page, 'div[role="button"]:has-text("동영상 추가"), button:has-text("동영상 추가"), div[role="button"]:has-text("Add video"), button:has-text("Add video")'):
            return True
    except Exception:
        pass
    return False


def _build_business_suite_reels_composer_url(cur_url):
    """현재 비즈니스 스위트 URL의 asset_id / business_id 쿼리를 유지한 reels_composer 직행 URL 생성"""
    params = []
    for key in ("business_id", "asset_id"):
        m = re.search(rf"[?&]({key}=\d+)", str(cur_url or ""))
        if m:
            params.append(m.group(1))
    qs = ("?" + "&".join(params)) if params else ""
    return f"https://business.facebook.com/latest/reels_composer{qs}"


def _open_business_suite_reel_composer(page):
    """메타 비즈니스 스위트에서 릴스 만들기 화면으로 확실히 진입.
    1) [릴스 만들기] 클릭 후 최대 8초간 실제 reels_composer 전환 검증
    2) 홈 화면에 머물러 있으면 asset_id를 유지한 reels_composer 주소로 직접 이동해 100% 진입 보장"""
    if _is_business_suite_reel_composer_ready(page):
        return True

    reel_btn_sels = [
        'a[href*="reels_composer"]',
        'div[role="button"]:text-is("릴스 만들기")',
        'button:text-is("릴스 만들기")',
        'div[role="button"]:has-text("릴스 만들기")',
        'button:has-text("릴스 만들기")',
        'span:text-is("릴스 만들기")',
        'div[role="button"]:text-is("Create reel")',
        'button:text-is("Create reel")',
        'div[role="button"]:has-text("Create reel")',
        'button:has-text("Create reel")',
        'span:text-is("Create reel")',
    ]
    clicked_sel = ""
    for sel in reel_btn_sels:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible(timeout=1500):
                el.scroll_into_view_if_needed()
                el.click(force=True)
                clicked_sel = sel
                print(f"🎬 [페이스북(매장)] 릴스 만들기 버튼 클릭 ({sel}) — 작성기 전환 확인 중...", flush=True)
                break
        except Exception:
            pass

    if clicked_sel:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if _is_business_suite_reel_composer_ready(page):
                print("✅ [페이스북(매장)] 릴스 작성기(reels_composer) 전환 확인 완료!", flush=True)
                time.sleep(1.5)
                return True
            time.sleep(0.5)
        print("↪ [페이스북(매장)] 버튼 클릭 후 홈 화면 유지 감지 → reels_composer 주소로 직접 이동합니다.", flush=True)

    # asset_id / business_id를 유지한 채 reels_composer 직접 이동으로 100% 진입 보장
    composer_url = _build_business_suite_reels_composer_url(page.url or "")
    page.goto(composer_url, wait_until="domcontentloaded", timeout=45000)
    deadline2 = time.time() + 8.0
    while time.time() < deadline2:
        if _is_business_suite_reel_composer_ready(page):
            time.sleep(1.5)
            return True
        time.sleep(0.5)
    return _is_business_suite_reel_composer_ready(page)


def upload_to_facebook_personal(body="", tags=None, video_path=None, login_only=False, stop_checker=None, test_mode=False):
    """페이스북(개인) 릴스 자동 업로드 — https://www.facebook.com/reels/create"""
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [페이스북(개인)] 업로드할 영상 파일이 없습니다:", video_path, flush=True)
        return None

    prof = load_profile()
    full_caption = _build_facebook_caption(body, tags, prof)

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("페이스북개인", mode=record_mode)
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
                locale="ko-KR",
                record_video_dir=record_dir,
                record_video_size={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            start_url = "https://www.facebook.com/" if login_only else "https://www.facebook.com/reels/create"
            page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.0)
            dismiss_facebook_popups(page)

            if not is_facebook_logged_in(page):
                wait_for_manual_login(
                    page,
                    "페이스북(개인)",
                    is_facebook_logged_in,
                    timeout_sec=180,
                    on_tick=lambda pg, idx, el: dismiss_facebook_popups(pg),
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(개인)", mark_verified=True)
            else:
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(개인)", mark_verified=True)

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(개인)", mark_verified=True)
                print("✅ [페이스북(개인)] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            if "/reels/create" not in (page.url or "").lower():
                page.goto("https://www.facebook.com/reels/create", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3.0)
                dismiss_facebook_popups(page)

            if not _attach_video_to_composer(page, video_path):
                raise RuntimeError("페이스북(개인) 릴스 영상 첨부 입력칸을 찾지 못했습니다.")
            print("📹 [페이스북(개인)] 원본 영상 첨부 완료! 인코딩·단계 전환 대기 중...", flush=True)
            time.sleep(4.0)

            # 편집/자르기 단계가 있으면 '다음(Next)' 버튼 클릭 (최대 2단계)
            for _ in range(2):
                clicked_next = _click_visible_button_by_texts(page, ["다음", "Next"], timeout_ms=3000)
                if clicked_next:
                    time.sleep(2.5)
                else:
                    break

            if not _fill_caption_textbox(page, full_caption):
                raise RuntimeError("페이스북(개인) 릴스 본문 입력칸을 찾지 못했습니다.")
            print("✍️ [페이스북(개인)] 본문 및 해시태그 입력 완료!", flush=True)
            time.sleep(1.5)

            if test_mode:
                print("🧪 [테스트 모드 - 페이스북(개인)] 최종 게시 직전까지 입력·설정 확인 완료 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(개인)", mark_verified=True)
                wait_for_test_mode_close(context, "페이스북(개인)", stop_checker=stop_checker)
                return "test_mode_ok"

            clicked_pub = _click_visible_button_by_texts(page, ["게시", "공유", "Publish", "Share", "Post"], timeout_ms=4000)
            if not clicked_pub:
                raise RuntimeError("페이스북(개인) 최종 게시(공유) 버튼을 찾지 못했습니다.")

            for _ in range(20):
                if stop_checker and stop_checker():
                    raise InterruptedError("페이스북(개인) 작업 중지 요청")
                time.sleep(1.5)
                if "/reels/create" not in (page.url or "").lower():
                    break
                if is_any_locator_visible(page, ':text("릴스가 게시"), :text("Your reel is being processed"), :text("릴스가 공유"), :text("게시되었습니다")'):
                    break

            save_storage_state_safely(context, COOKIES_PATH, "페이스북(개인)", mark_verified=True)
            return page.url or "https://www.facebook.com/"

        except InterruptedError as ie:
            record_platform_diagnostics("페이스북개인", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return None
        except Exception as e:
            record_platform_diagnostics("페이스북개인", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [페이스북(개인)] 오류: {e}", flush=True)
            return None
        finally:
            finalize_playwright_session(
                "페이스북개인",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="페이스북(개인)",
            )


def upload_to_facebook_page(body="", tags=None, video_path=None, page_name="", login_only=False, stop_checker=None, test_mode=False):
    """페이스북(매장 페이지) 릴스 자동 업로드 — 메타 비즈니스 스위트(https://business.facebook.com/) 경유"""
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [페이스북(매장)] 업로드할 영상 파일이 없습니다:", video_path, flush=True)
        return None

    prof = load_profile()
    target_page = (page_name or get_salon_name(prof, fallback="")).strip()
    full_caption = _build_facebook_caption(body, tags, prof)

    record_mode = "login" if login_only else "upload"
    record_dir = create_blackbox_record_dir("페이스북매장", mode=record_mode)
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
                locale="ko-KR",
                record_video_dir=record_dir,
                record_video_size={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            video_obj = page.video

            page.goto("https://business.facebook.com/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.5)
            dismiss_facebook_popups(page)

            if not is_facebook_logged_in(page):
                wait_for_manual_login(
                    page,
                    "페이스북(매장)",
                    is_facebook_logged_in,
                    timeout_sec=180,
                    on_tick=lambda pg, idx, el: dismiss_facebook_popups(pg),
                    stop_checker=stop_checker,
                )
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(매장)", mark_verified=True)
            else:
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(매장)", mark_verified=True)

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(매장)", mark_verified=True)
                print("✅ [페이스북(매장)] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            # 1단계: 사용자 매장명(page_name) 기반 3단계 페이지 자동 매칭 (정확 일치 -> 유사도 -> 첫 번째 페이지 폴백)
            _select_facebook_business_page(page, target_page)
            dismiss_facebook_popups(page)

            # 2단계: 메타 비즈니스 스위트 릴스 만들기 화면 진입
            _open_business_suite_reel_composer(page)
            dismiss_facebook_popups(page)

            # 3단계: 원본 .mp4 영상 첨부
            if not _attach_video_to_composer(page, video_path):
                raise RuntimeError("페이스북(매장) 메타 비즈니스 스위트 릴스 영상 첨부 입력칸을 찾지 못했습니다.")
            print("📹 [페이스북(매장)] 원본 영상 첨부 완료! 업로드 처리 대기 중...", flush=True)
            time.sleep(4.0)

            # 4단계: 본문 + 해시태그 입력 (화면 순서에 따라 입력칸이 먼저 나오거나 '다음' 클릭 후 나오는 경우 모두 대응)
            caption_filled = _fill_caption_textbox(page, full_caption)

            for _ in range(2):
                clicked_next = _click_visible_button_by_texts(page, ["다음", "Next"], timeout_ms=3000)
                if clicked_next:
                    time.sleep(2.5)
                    if not caption_filled:
                        caption_filled = _fill_caption_textbox(page, full_caption)
                else:
                    break

            if not caption_filled:
                caption_filled = _fill_caption_textbox(page, full_caption)
            if not caption_filled:
                raise RuntimeError("페이스북(매장) 릴스 본문 입력칸을 찾지 못했습니다.")
            print("✍️ [페이스북(매장)] 본문 및 해시태그 입력 완료!", flush=True)
            time.sleep(1.5)

            if test_mode:
                print("🧪 [테스트 모드 - 페이스북(매장)] 최종 게시 직전까지 입력·설정 확인 완료 (실제 게시 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "페이스북(매장)", mark_verified=True)
                wait_for_test_mode_close(context, "페이스북(매장)", stop_checker=stop_checker)
                return "test_mode_ok"

            clicked_pub = _click_visible_button_by_texts(page, ["공유", "게시", "지금 공유", "Share", "Publish", "Share now"], timeout_ms=4000)
            if not clicked_pub:
                raise RuntimeError("페이스북(매장) 최종 게시(공유) 버튼을 찾지 못했습니다.")

            for _ in range(20):
                if stop_checker and stop_checker():
                    raise InterruptedError("페이스북(매장) 작업 중지 요청")
                time.sleep(1.5)
                if "reels_composer" not in (page.url or "").lower():
                    break
                if is_any_locator_visible(page, ':text("릴스가 공유"), :text("릴스가 게시"), :text("Your reel has been shared"), :text("게시되었습니다")'):
                    break

            save_storage_state_safely(context, COOKIES_PATH, "페이스북(매장)", mark_verified=True)
            return page.url or "https://business.facebook.com/"

        except InterruptedError as ie:
            record_platform_diagnostics("페이스북매장", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return None
        except Exception as e:
            record_platform_diagnostics("페이스북매장", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [페이스북(매장)] 오류: {e}", flush=True)
            return None
        finally:
            finalize_playwright_session(
                "페이스북매장",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="페이스북(매장)",
            )
