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
    sync_cookies_from_persistent_context,
    wait_for_manual_login,
    wait_for_test_mode_close,
    finalize_playwright_session,
    open_playwright_session,
    launch_persistent_chromium,
    record_platform_diagnostics,
    verify_publish_button_aim,
    normalize_branch_name,
    load_profile,
    get_effective_banned_tags,
    get_salon_name,
    get_salon_address,
    get_place_search_keyword,
    get_region_keyword,
    get_region_tags,
    get_blog_categories,
    recommend_blog_category,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("naver_cookies.json")

MEN_KEYWORDS = [
    "남자", "남성", "맨즈", "멘즈", "다운펌", "가르마", "애즈펌", "시스루댄디",
    "아이비리그", "리젠트", "가일", "포마드", "크롭", "쉐도우", "스핀스왈로",
    "페이드", "바버", "투블럭", "상고", "슬릭백", "드롭컷", "파일럿컷",
    "men", "mens", "male", "barber", "crop", "fade", "gentleman"
]

def classify_category(text):
    t_low = (text or "").lower()
    for kw in MEN_KEYWORDS:
        if kw in t_low:
            return "Men"
    return "Women"

def extract_hashtags_from_text(text, banned_tags=None):
    banned = banned_tags if banned_tags is not None else get_effective_banned_tags()
    return [t for t in re.findall(r"#([^\s#]+)", text or "") if t.lower() not in banned]

def clean_blog_title(raw_title, body_text="", tags=None, profile=None):
    prof = profile if isinstance(profile, dict) else load_profile()
    region_kw = get_region_keyword(prof)
    region_prefix = f"{region_kw} 미용실 " if region_kw else ""
    t = normalize_branch_name((raw_title or "").strip())
    broken = ("고", "서", "며", "면", "게", "어", "아", ",")
    if t.endswith(","):
        t = t[:-1].strip()
        return f"{t}, 깔끔하게 정돈된 스타일"
    if "," in t:
        p, s = t.split(",", 1)
        if not s.strip() or s.strip().endswith(broken) or any(w in s for w in p.split()):
            return f"{p.strip()}, 차르르 떨어지는 라인" if "차르르" in body_text else f"{p.strip()}, 깔끔하게 정돈된 스타일"
    elif t.endswith(broken):
        return f"{region_prefix}{t[:-1].strip()} 스타일".strip()
    return t

def make_safe_video_title(raw_title, limit=29, profile=None):
    salon_name = get_salon_name(profile, fallback="헤어살롱")
    t = normalize_branch_name(raw_title or f"{salon_name} 헤어스타일").strip()
    if len(t) <= limit:
        return t
    if "," in t:
        front = t.split(",", 1)[0].strip()
        if 5 <= len(front) <= limit:
            return front
    cut = t[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(",")
    return cut

def build_full_body(body, profile=None):
    nl = chr(10)
    prof = profile if isinstance(profile, dict) else load_profile()
    salon_name = get_salon_name(prof)
    salon_addr = get_salon_address(prof)
    parking = (prof.get("parking_guide") or "").strip()
    booking_url = (prof.get("booking_url") or "").strip()
    clean_text = nl.join([l for l in (body or "").splitlines() if not l.strip().startswith("#")]).strip()
    clean_text = normalize_branch_name(clean_text)

    footer_parts = []
    if booking_url:
        footer_parts.append(f"[[📌 1:1 우선예약 및 상담]]{nl}{booking_url}")
    info_lines = []
    if salon_name:
        info_lines.append(f"🔹 **매장명:** {salon_name}")
    if salon_addr:
        info_lines.append(f"🔹 **주소:** {salon_addr}")
    if parking:
        info_lines.append(f"🔹 **주차:** {parking}")
    if info_lines:
        footer_parts.append(nl.join(info_lines))

    if not footer_parts:
        return clean_text
    return clean_text + f"{nl}{nl}{nl}━━━━━━━━━━{nl}{nl}" + f"{nl}{nl}".join(footer_parts)

def find_in_both(page, editor, selector):
    loc_ed = editor.locator(selector)
    return loc_ed if loc_ed.count() > 0 else page.locator(selector)

def set_editor_color(page, editor, use_blue=True):
    try:
        c_btn = find_in_both(page, editor, 'button[data-name="font-color"], .se-font-color-toolbar-button')
        if c_btn.count() == 0 or not c_btn.first.is_visible():
            return False
        c_btn.first.click()
        time.sleep(0.15)
        if use_blue:
            blue_sels = [
                'button[data-color="#0078cb"]',
                'button[data-color="#2563eb"]',
                'button[data-color="#0055ff"]',
                '.se-color-palette button[style*="0, 120, 203"]',
                '.se-color-palette button[style*="37, 99, 235"]'
            ]
            for sel in blue_sels:
                chip = find_in_both(page, editor, sel)
                if chip.count() > 0 and chip.first.is_visible():
                    chip.first.click()
                    time.sleep(0.1)
                    return True
        else:
            black_sels = [
                'button[data-color="#000000"]',
                'button[data-color="#1e1e1e"]',
                'button[data-color="#333333"]',
                '.se-color-palette-list li:first-child button'
            ]
            for sel in black_sels:
                chip = find_in_both(page, editor, sel)
                if chip.count() > 0 and chip.first.is_visible():
                    chip.first.click()
                    time.sleep(0.1)
                    return True
        c_btn.first.click()
        time.sleep(0.1)
    except Exception:
        pass
    return False


def _read_toolbar_font_size(btn_loc):
    """글자 크기 툴바 버튼의 현재 활성 크기(정수 px, 예: 15/19)를 판독. 판독 불가 시 None 반환."""
    try:
        target = btn_loc.first
        candidates = []
        for attr in ("data-value", "aria-label", "title"):
            val = target.get_attribute(attr)
            if val:
                candidates.append(val)
        try:
            label_el = target.locator('.se-toolbar-label').first
            if label_el.count() > 0:
                ltxt = (label_el.inner_text(timeout=300) or "").strip()
                if ltxt:
                    candidates.append(ltxt)
        except Exception:
            pass
        try:
            btxt = (target.inner_text(timeout=300) or "").strip()
            if btxt:
                candidates.append(btxt)
        except Exception:
            pass
        for raw in candidates:
            m = re.search(r'(?:fs)?\b(1[1-9]|2[0-9]|3[0-8])\b(?:px)?', str(raw), re.IGNORECASE)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def set_editor_font_size(page, editor, size_px=15):
    """스마트에디터 글자 크기 툴바에서 지정 크기(예: 소제목 19px / 본문 복원 15px)를 선택.
    - 이미 원하는 크기면 즉시 True 반환(불필요한 클릭 지연 최소화)
    - 드롭다운에서 15 / 15px / fs15 매칭 후 실제 활성 크기를 검증하고 미반영 시 1회 재시도
    - 툴바 미발견·팝업 미오픈 시 불필요한 대기 없이 안전하게 False 반환"""
    target_px = int(size_px)
    code = f"fs{target_px}"
    px_str = str(target_px)
    px_unit = f"{target_px}px"
    try:
        btn_sels = (
            'button[data-name="font-size"], '
            'button.se-font-size-code-toolbar-button, '
            '.se-font-size-code-toolbar-button, '
            '.se-toolbar-item-font-size-code button'
        )
        f_btn = find_in_both(page, editor, btn_sels)
        if f_btn.count() == 0 or not f_btn.first.is_visible():
            return False

        # 이미 목표 크기(예: 15px)로 설정되어 있으면 클릭 없이 즉시 통과
        cur_px = _read_toolbar_font_size(f_btn)
        if cur_px == target_px:
            return True

        opt_sels = [
            f'button[data-value="{code}"]',
            f'button[data-value="{px_str}"]',
            f'button[data-value="{px_unit}"]',
            f'button.se-toolbar-option-font-size-code-{code}-button',
            f'.se-toolbar-option-font-size-code button:text-is("{px_str}")',
            f'.se-toolbar-option-font-size-code button:text-is("{px_unit}")',
            f'.se-popup-font-size-code button:text-is("{px_str}")',
            f'.se-popup-font-size-code button:text-is("{px_unit}")',
            f'.se-toolbar-option-list button:text-is("{px_str}")',
            f'.se-toolbar-option-list button:text-is("{px_unit}")',
            f'li button[data-value="{code}"]',
            f'li button[data-value="{px_str}"]',
            f'li button[data-value="{px_unit}"]',
        ]
        popup_btn_sel = (
            '.se-popup-font-size-code button, '
            '.se-toolbar-option-font-size-code button, '
            '.se-toolbar-option-list button[data-value^="fs"], '
            '.se-toolbar-option-list button'
        )

        def _click_matching_option():
            for sel in opt_sels:
                opt = find_in_both(page, editor, sel)
                if opt.count() > 0 and opt.first.is_visible():
                    opt.first.click()
                    return True
            # 텍스트/속성 정규화 폴백 스캔 (15 / 15px / fs15)
            pool = find_in_both(page, editor, popup_btn_sel)
            cnt = min(pool.count(), 24)
            for i in range(cnt):
                cand = pool.nth(i)
                try:
                    if not cand.is_visible():
                        continue
                    dv = (cand.get_attribute("data-value") or "").strip().lower()
                    cls = (cand.get_attribute("class") or "").strip().lower()
                    txt = re.sub(r'\s+', '', (cand.inner_text(timeout=200) or "")).lower()
                    if (
                        dv in (code, px_str, px_unit)
                        or f"-{code}-" in cls
                        or txt in (px_str, px_unit, code)
                    ):
                        cand.click()
                        return True
                except Exception:
                    pass
            return False

        for attempt in range(2):
            f_btn.first.click()
            time.sleep(0.14)

            if not _click_matching_option():
                # 툴바 드롭다운이 열리지 않았거나 옵션을 못 찾은 경우 불필요한 재시도 지연 최소화
                try:
                    if (f_btn.first.get_attribute("aria-expanded") or "").lower() == "true":
                        f_btn.first.click()
                        time.sleep(0.08)
                except Exception:
                    pass
                if attempt == 0 and target_px == 15 and _read_toolbar_font_size(f_btn) not in (None, target_px):
                    # 19px 상태에서 15px 복원 드롭다운만 1회 짧게 재시도
                    time.sleep(0.1)
                    continue
                return False

            time.sleep(0.12)
            after_px = _read_toolbar_font_size(f_btn)
            if after_px == target_px or after_px is None:
                return True
            # 선택 후 툴바 라벨이 목표 크기로 바뀌지 않았으면 1회 재시도
            time.sleep(0.1)
    except Exception:
        pass
    return False

def _is_align_center_active(btn):
    """정렬 툴바 버튼의 속성/클래스/라벨로 현재 가운데 정렬 활성 여부를 판별."""
    try:
        dname = (btn.get_attribute("data-name") or "").lower()
        dv = (btn.get_attribute("data-value") or "").lower()
        cls = (btn.get_attribute("class") or "").lower()
        al = ((btn.get_attribute("aria-label") or "") + " " + (btn.get_attribute("title") or "")).lower()
        meta = f"{dname} {dv} {cls} {al}"
        if any(k in meta for k in ("align-left", "align-right", "align-justify", "왼쪽", "오른쪽", "양쪽")):
            return False
        if dv == "center" or "align-center" in dname or "align-center" in cls or "가운데" in al:
            return True
    except Exception:
        pass
    return False


def ensure_editor_center_align(page, editor):
    """스마트에디터 본문 텍스트 전체(도입부·소제목·문단·마무리 인사)를 가운데 정렬로 유지.
    - 이미 가운데 정렬이 활성화되어 있으면 클릭 없이 즉시 True 반환(지연 0)
    - 단일 드롭다운 툴바와 분리형 정렬 버튼 UI 모두 대응하며, 미반영 시 1회 재시도"""
    try:
        # 1) 분리형 정렬 버튼(왼쪽/가운데 버튼이 동시에 노출된 경우) 우선 확인
        sep_left = find_in_both(page, editor, '.se-toolbar button[data-name="align-left"], .se-toolbar button.se-align-left-toolbar-button')
        sep_center = find_in_both(page, editor, '.se-toolbar button[data-name="align-center"], .se-toolbar button.se-align-center-toolbar-button')
        if sep_left.count() > 0 and sep_center.count() > 0 and sep_left.first.is_visible() and sep_center.first.is_visible():
            cbtn = sep_center.first
            c_cls = (cbtn.get_attribute("class") or "").lower()
            c_pressed = (cbtn.get_attribute("aria-pressed") or "").lower()
            if "se-is-selected" in c_cls or "active" in c_cls or c_pressed == "true":
                return True
            cbtn.click()
            time.sleep(0.1)
            return True

        # 2) 스마트에디터 ONE 표준 정렬 드롭다운 버튼 탐색
        align_btn_sels = (
            'button[data-name="align-center"], '
            'button[data-name="align-left"], '
            'button[data-name="align-right"], '
            'button[data-name="align-justify"], '
            'button[data-name="align"], '
            'button.se-align-center-toolbar-button, '
            'button.se-align-left-toolbar-button, '
            '.se-toolbar-item-align button, '
            'button.se-align-toolbar-button'
        )
        a_btn = find_in_both(page, editor, align_btn_sels)
        if a_btn.count() == 0 or not a_btn.first.is_visible():
            return False

        if _is_align_center_active(a_btn.first):
            return True

        center_opt_sels = [
            'button[data-value="center"]',
            'button.se-toolbar-option-align-center-button',
            '.se-toolbar-option-align button[data-value="center"]',
            '.se-popup-align button[data-value="center"]',
            '.se-toolbar-option-list button[data-value="center"]',
            '.se-toolbar-option-list button[data-name="align-center"]',
            '.se-popup-align button[data-name="align-center"]',
            'button[aria-label*="가운데 정렬"]',
            'button[title*="가운데 정렬"]',
            '.se-popup-align button:has-text("가운데")',
            '.se-toolbar-option-list button:has-text("가운데")',
        ]

        for attempt in range(2):
            cur_btn = find_in_both(page, editor, align_btn_sels)
            if cur_btn.count() == 0 or not cur_btn.first.is_visible():
                return False
            if _is_align_center_active(cur_btn.first):
                return True

            cur_btn.first.click()
            time.sleep(0.12)

            clicked_opt = False
            for sel in center_opt_sels:
                opt = find_in_both(page, editor, sel)
                if opt.count() > 0 and opt.first.is_visible():
                    opt.first.click()
                    clicked_opt = True
                    time.sleep(0.1)
                    break

            if not clicked_opt:
                try:
                    if (cur_btn.first.get_attribute("aria-expanded") or "").lower() == "true":
                        cur_btn.first.click()
                        time.sleep(0.06)
                except Exception:
                    pass
                return False

            # 선택 후 실제 가운데 정렬 반영 여부 확인 (미반영 시 1회 재시도)
            chk_btn = find_in_both(page, editor, align_btn_sels)
            if chk_btn.count() == 0 or _is_align_center_active(chk_btn.first):
                return True
            if attempt == 0:
                time.sleep(0.08)
    except Exception:
        pass
    return False


def _move_cursor_below_inserted_image(page, editor):
    """이미지 삽입 후 캡션창(.se-caption)이나 툴바 포커스를 피해 안전하게 사진 아래 새 텍스트 줄로 커서 이동"""
    try:
        # 1) 사진 삽입 직후 뜬 이미지 편집 툴바/캡션 포커스 해제
        page.keyboard.press("Escape")
        time.sleep(0.15)

        # 2) 에디터 본문 마지막 컴포넌트가 이미 빈 텍스트 문단(캡션 제외)이면 직접 클릭
        last_comp = find_in_both(page, editor, '.se-components-wrap > .se-component').last
        if last_comp.count() > 0:
            cls = (last_comp.get_attribute("class") or "")
            if "se-text" in cls and "se-caption" not in cls:
                para = last_comp.locator('.se-text-paragraph').last
                if para.count() > 0 and para.is_visible() and not (para.inner_text() or "").strip():
                    para.scroll_into_view_if_needed()
                    para.click(force=True)
                    time.sleep(0.25)
                    return

        # 3) 방금 삽입된 마지막 이미지 블록에 포커스를 맞춘 뒤 Enter 1회로 아래 새 줄 생성
        last_img = find_in_both(page, editor, '.se-component.se-image .se-image-resource, .se-component.se-image').last
        if last_img.count() > 0 and last_img.is_visible():
            last_img.scroll_into_view_if_needed()
            last_img.click(force=True)
            time.sleep(0.2)
            page.keyboard.press("Enter")
            time.sleep(0.3)
            return
    except Exception:
        pass

    # 4) 최후 폴백: 에디터 본문 영역에 포커스가 있을 때만 Enter 1회
    try:
        page.keyboard.press("Enter")
        time.sleep(0.3)
    except Exception:
        pass

def attach_image_in_editor(page, editor, img_path):
    """스마트에디터 현재 커서 위치에 선택한 캡처 사진 또는 GIF를 삽입"""
    if not img_path or not os.path.exists(img_path):
        return False
    is_gif = img_path.lower().endswith(".gif")
    media_label = "GIF" if is_gif else "사진"
    media_icon = "🎞️" if is_gif else "🖼️"
    try:
        img_sel = '.se-image-resource, img.se-image-resource'
        before_cnt = find_in_both(page, editor, img_sel).count()
        attached = False
        method = "button"

        ibtn = find_in_both(page, editor, 'button[data-name="image"]')
        if ibtn.count() > 0 and ibtn.first.is_visible():
            try:
                with page.expect_file_chooser(timeout=6000) as fc_info:
                    ibtn.first.click(force=True)
                fc_info.value.set_files(img_path)
                attached = True
            except Exception:
                pass

        if not attached:
            for ctx in [editor, page]:
                f_inputs = ctx.locator('input[type="file"][accept*="image"], input[type="file"][accept*="gif"]')
                if f_inputs.count() > 0:
                    f_inputs.first.set_input_files(img_path)
                    attached = True
                    method = "input"
                    break

        if not attached:
            return False

        # 스마트에디터에 실제 이미지/GIF 요소가 렌더링될 때까지 대기 (GIF는 용량이 커 최대 12초 대기)
        max_ticks = 24 if is_gif else 12
        settle_wait = 1.1 if is_gif else 0.6
        for _ in range(max_ticks):
            time.sleep(0.5)
            if find_in_both(page, editor, img_sel).count() > before_cnt:
                time.sleep(settle_wait)
                break
        else:
            time.sleep(1.5 if is_gif else 1.0)

        _move_cursor_below_inserted_image(page, editor)
        prefix = "(input) " if method == "input" else ""
        print(f"{media_icon} [네이버 블로그] {prefix}본문 {media_label} 삽입 완료: {os.path.basename(img_path)}", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ [네이버 블로그] {media_label} 삽입 중 예외: {e}", flush=True)
    return False

def paste_formatted_body(page, editor, text, selected_images=None):
    # 사용자가 본문 중간에 [미디어 자료N]/[사진 N]/[GIF]를 문장과 같은 줄에 붙였더라도 단독 줄로 안전 분리
    inline_marker_re = re.compile(
        r'\[(?:미디어\s*자료\s*\d+|사진\s*\d+(?:\s*[·•\-]?\s*GIF)?|GIF(?:\s*\d+)?)\]',
        re.IGNORECASE,
    )
    normalized_text = inline_marker_re.sub(lambda m: f"\n{m.group(0)}\n", text or "")
    lines = normalized_text.splitlines()
    sel_imgs = selected_images or []
    used_indices = set()
    media_slot_pattern = re.compile(
        r'^\[(?:미디어\s*자료|사진)\s*(\d+)(?:\s*[·•\-]?\s*GIF)?\]$',
        re.IGNORECASE,
    )
    gif_pattern = re.compile(r'^\[GIF(?:\s*(\d+))?\]$', re.IGNORECASE)
    url_pattern = re.compile(r'^https?://\S+$')

    referenced_slot_indices = set()
    has_gif_placeholders = False
    for raw_l in lines:
        s_l = raw_l.strip()
        mp = media_slot_pattern.match(s_l)
        if mp:
            referenced_slot_indices.add(int(mp.group(1)) - 1)
        elif gif_pattern.match(s_l):
            has_gif_placeholders = True
    has_media_placeholders = bool(referenced_slot_indices) or has_gif_placeholders
    flushed_remaining = False
    need_text_center_align = True

    def _flush_unplaced_active_media():
        nonlocal flushed_remaining
        if flushed_remaining or has_media_placeholders:
            return
        flushed_remaining = True
        for rem_idx in range(len(sel_imgs)):
            if rem_idx in used_indices:
                continue
            path_cand = sel_imgs[rem_idx]
            if not path_cand or not os.path.exists(path_cand):
                continue
            attach_image_in_editor(page, editor, path_cand)
            used_indices.add(rem_idx)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            page.keyboard.press("Enter")
            time.sleep(0.04)
            continue

        # 본문에 미디어 마커([미디어 자료N]/[사진 N]/[GIF])가 전혀 없을 때만 하단 구분선(━━━) 직전에 활성 미디어 삽입
        if stripped.startswith("━━━") and (not has_media_placeholders) and len(used_indices) < len(sel_imgs):
            _flush_unplaced_active_media()
            need_text_center_align = True

        # [미디어 자료1]~[미디어 자료10] 또는 [사진 1]~[사진 10] 태그 감지 시 해당 슬롯의 사진/GIF 삽입 (제외 슬롯은 당기지 않고 빈자리로 생략)
        m_slot = media_slot_pattern.match(stripped)
        if m_slot:
            p_idx = int(m_slot.group(1)) - 1
            if 0 <= p_idx < len(sel_imgs) and sel_imgs[p_idx] and os.path.exists(sel_imgs[p_idx]):
                attach_image_in_editor(page, editor, sel_imgs[p_idx])
                used_indices.add(p_idx)
                need_text_center_align = True
            continue

        # [GIF] 또는 [GIF N] 단독 태그 감지 시 선택된 GIF 파일 삽입 (다른 슬롯 마커에 배정되지 않은 GIF 우선)
        m_gif = gif_pattern.match(stripped)
        if m_gif:
            unclaimed_gifs = [
                (i, p) for i, p in enumerate(sel_imgs)
                if i not in used_indices and i not in referenced_slot_indices and p and p.lower().endswith(".gif") and os.path.exists(p)
            ]
            gif_candidates = unclaimed_gifs or [
                (i, p) for i, p in enumerate(sel_imgs)
                if i not in used_indices and p and p.lower().endswith(".gif") and os.path.exists(p)
            ]
            if gif_candidates:
                target_g = gif_candidates[0]
                if m_gif.group(1):
                    g_num = int(m_gif.group(1)) - 1
                    if 0 <= g_num < len(gif_candidates):
                        target_g = gif_candidates[g_num]
                attach_image_in_editor(page, editor, target_g[1])
                used_indices.add(target_g[0])
                need_text_center_align = True
            continue

        # 본문 텍스트·URL 입력 직전에 가운데 정렬 유지 확인 (이미 가운데면 클릭 없이 즉시 통과)
        if need_text_center_align:
            ensure_editor_center_align(page, editor)
            need_text_center_align = False

        # 단독 URL 줄은 붙여넣기 직후 Enter + 0.8초 대기로 네이버 OG 링크 카드 자동 변환 유도
        if url_pattern.match(stripped):
            copy_to_clipboard(stripped)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(0.15)
            page.keyboard.press("Enter")
            time.sleep(0.8)
            need_text_center_align = True
            continue

        tokens = re.split(r'(\[\[[^\]]+\]\]|\*\*[^*]+\*\*)', line)
        line_had_resized_heading = False
        for tok in tokens:
            if not tok:
                continue
            if tok.startswith("[[") and tok.endswith("]]") and len(tok) > 4:
                inner = tok[2:-2]
                resized = set_editor_font_size(page, editor, size_px=19)
                colored = set_editor_color(page, editor, use_blue=True)
                page.keyboard.press(f"{MOD_KEY}+b")
                time.sleep(0.05)
                copy_to_clipboard(inner)
                page.keyboard.press(f"{MOD_KEY}+v")
                time.sleep(0.08)
                page.keyboard.press(f"{MOD_KEY}+b")
                time.sleep(0.05)
                if colored:
                    set_editor_color(page, editor, use_blue=False)
                if resized:
                    line_had_resized_heading = True
                    set_editor_font_size(page, editor, size_px=15)
            elif tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
                inner = tok[2:-2]
                page.keyboard.press(f"{MOD_KEY}+b")
                time.sleep(0.05)
                copy_to_clipboard(inner)
                page.keyboard.press(f"{MOD_KEY}+v")
                time.sleep(0.08)
                page.keyboard.press(f"{MOD_KEY}+b")
                time.sleep(0.05)
            else:
                copy_to_clipboard(tok)
                page.keyboard.press(f"{MOD_KEY}+v")
                time.sleep(0.08)

        if idx < len(lines) - 1:
            page.keyboard.press("Enter")
            time.sleep(0.04)
            # 스마트에디터에서 19px 소제목 줄 직후 Enter 시 19px가 상속되는 경우 즉시 15px 복원 확인
            if line_had_resized_heading:
                set_editor_font_size(page, editor, size_px=15)

    # 구분선(━━━)이 없는 본문에서도 본문에 미배치된 활성 캡처/GIF가 누락되지 않도록 말미에 보충
    if len(used_indices) < len(sel_imgs):
        _flush_unplaced_active_media()

def focus_video_tag_input(page, editor, title_input=None):
    tag_add_sels = (
        'input[placeholder*="태그"], '
        'button:has-text("태그추가"), '
        'span:has-text("태그추가"), '
        'div:has-text("태그추가"), '
        ':text-is("태그추가")'
    )
    add_el = find_in_both(page, editor, tag_add_sels)
    for i in range(add_el.count() - 1, -1, -1):
        cand = add_el.nth(i)
        try:
            if cand.is_visible():
                cand.click(force=True)
                time.sleep(0.25)
                return True
        except Exception:
            pass

    lbl = find_in_both(page, editor, ':text-is("태그 편집")')
    for i in range(lbl.count() - 1, -1, -1):
        cand = lbl.nth(i)
        try:
            if cand.is_visible():
                cand.click(position={"x": 85, "y": 12}, force=True)
                time.sleep(0.25)
                return True
        except Exception:
            pass

    if title_input is not None:
        try:
            title_input.click(force=True)
            time.sleep(0.2)
            page.keyboard.press("Tab")
            time.sleep(0.2)
            page.keyboard.press("Tab")
            time.sleep(0.25)
            return True
        except Exception:
            pass
    return False

def enter_video_modal_tags(page, editor, tags_list, title_input=None):
    if not tags_list:
        return False
    try:
        print("🔎 [진단] 동영상 정보창 태그 입력 시도 중...", flush=True)
        focus_video_tag_input(page, editor, title_input)

        entered = []
        for raw_t in tags_list[:10]:
            c_tag = raw_t.replace("#", "").strip()
            if not c_tag:
                continue

            add_btn = find_in_both(page, editor, ':text-is("태그추가"), button:has-text("태그추가"), input[placeholder*="태그추가"]')
            for i in range(add_btn.count() - 1, -1, -1):
                if add_btn.nth(i).is_visible():
                    try:
                        add_btn.nth(i).click(force=True)
                        time.sleep(0.2)
                        break
                    except Exception:
                        pass

            copy_to_clipboard(c_tag)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(0.22)
            page.keyboard.press("Enter")
            time.sleep(0.35)
            entered.append(f"#{c_tag}")

        if entered:
            print(f"✅ [네이버 블로그] 동영상 정보창 태그 입력 완료({len(entered)}개): {', '.join(entered)}", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ [네이버 블로그] 동영상 정보창 태그 입력 예외: {e}", flush=True)
        return False

def enter_publish_drawer_tags(page, editor, tags_list):
    """최종 발행 서랍장의 '#태그 입력 (최대 30개)' 입력칸에 처음 1회만 포커스 후 태그 10개 연속 입력"""
    if not tags_list:
        return False
    try:
        print("🔎 [진단] 최종 발행 서랍장 태그 입력 상자 탐색 중...", flush=True)
        focused = False

        # 1순위: 실제 텍스트를 받는 input / textarea 요소만 정확히 타겟팅
        exact_input_sels = (
            'input[placeholder*="태그"], '
            'textarea[placeholder*="태그"], '
            'input[placeholder*="최대 30개"], '
            'textarea[placeholder*="최대 30개"], '
            'input#tag-input, '
            'textarea#tag-input, '
            'div[class*="tag"] input, '
            'div[class*="tag"] textarea'
        )
        t_box = find_in_both(page, editor, exact_input_sels)
        for i in range(t_box.count() - 1, -1, -1):
            cand = t_box.nth(i)
            try:
                if cand.is_visible():
                    cand.click(force=True)
                    focused = True
                    print("✅ [진단] 1순위 태그 전용 input 포커스 성공!", flush=True)
                    break
            except Exception:
                pass

        # 2순위: '태그 편집' 라벨 우측 150px 지점 최초 1회만 클릭
        if not focused:
            lbl = find_in_both(page, editor, ':text-is("태그 편집")')
            for i in range(lbl.count() - 1, -1, -1):
                if lbl.nth(i).is_visible():
                    try:
                        lbl.nth(i).click(position={"x": 150, "y": 18}, force=True)
                        focused = True
                        print("✅ [진단] 2순위('태그 편집' 우측 150px) 최초 1회 포커스 성공!", flush=True)
                        break
                    except Exception:
                        pass

        time.sleep(0.35)
        entered = []
        # 반복문 안에서는 다시 클릭하지 않고 Meta+v 와 Enter만 연속 입력
        for raw_t in tags_list[:10]:
            c_tag = raw_t.replace("#", "").strip()
            if not c_tag:
                continue

            copy_to_clipboard(c_tag)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(0.22)
            page.keyboard.press("Enter")
            time.sleep(0.32)
            entered.append(f"#{c_tag}")

        if entered:
            print(f"✅ [네이버 블로그] 최종 발행창 태그 등록 완료({len(entered)}개): {', '.join(entered)}", flush=True)
        return True
    except Exception as e:
        print(f"⚠️ [네이버 블로그] 최종 발행창 태그 입력 예외: {e}", flush=True)
        return False

def attach_video_in_modal(page, editor, video_path, video_title, video_tags=None):
    vbtn = find_in_both(page, editor, 'button[data-name="video"]')
    if vbtn.count() == 0:
        raise Exception("상단 '동영상' 아이콘 없음")
    print("🎬 [네이버 블로그] 상단 [동영상] 아이콘 클릭 → 동영상 모달 탐색 중...", flush=True)
    vbtn.first.click()

    add_btn_sels = [
        '.se-video-uploader-layer button:text-is("동영상 추가")',
        '.se-video-uploader-layer button:has-text("동영상 추가")',
        'button:text-is("동영상 추가")',
        'button:has-text("동영상 추가")',
        'label:has-text("동영상 추가")',
        'div[role="button"]:has-text("동영상 추가")',
        'a:has-text("동영상 추가")',
        'span:text-is("동영상 추가")',
        'div[class*="video"] button:has-text("동영상 추가")',
    ]
    video_input_sel = (
        'input[type="file"][accept*="video"], '
        'input[type="file"][accept*="mp4"], '
        '.se-video-uploader-layer input[type="file"], '
        '.se-popup-video input[type="file"]'
    )

    attached = False
    logged_click_try = False
    modal_deadline = time.time() + 10.0

    # 불필요한 고정 대기 없이 모달의 [동영상 추가] 버튼(또는 비디오 input)이 뜨는 즉시 클릭·첨부
    while not attached and time.time() < modal_deadline:
        for sel in add_btn_sels:
            if attached:
                break
            try:
                btn = find_in_both(page, editor, sel)
                if btn.count() > 0 and btn.first.is_visible():
                    if not logged_click_try:
                        print(f"📎 [네이버 블로그] [동영상 추가] 버튼 클릭 시도 ({sel})...", flush=True)
                        logged_click_try = True
                    with page.expect_file_chooser(timeout=5000) as fc_info:
                        btn.first.click(force=True)
                    fc_info.value.set_files(video_path)
                    attached = True
                    print(f"✅ [네이버 블로그] 원본 동영상 파일 첨부 완료! ({os.path.basename(video_path)})", flush=True)
                    break
            except Exception:
                pass

        if attached:
            break

        # 버튼 클릭이 file chooser를 직접 열지 못했거나 input이 먼저 준비된 경우 즉시 input 폴백
        for ctx in (editor, page):
            try:
                vis_inputs = ctx.locator(video_input_sel)
                if vis_inputs.count() > 0:
                    if not logged_click_try:
                        print("📎 [네이버 블로그] 동영상 파일 input 직접 첨부 시도 중...", flush=True)
                        logged_click_try = True
                    vis_inputs.first.set_input_files(video_path)
                    attached = True
                    print(f"✅ [네이버 블로그] 비디오 전용 input 첨부 완료! ({os.path.basename(video_path)})", flush=True)
                    break
            except Exception:
                pass

        if not attached:
            time.sleep(0.15)

    if not attached:
        raise Exception("동영상 추가 버튼 클릭 및 파일 첨부 실패")

    print("⏳ [네이버 블로그] 동영상 업로드·인코딩 및 제목 입력칸 활성화 대기 중...", flush=True)
    safe_v_title = make_safe_video_title(video_title, limit=29)
    title_Selectors = 'input[placeholder*="제목을 입력"], input[placeholder*="동영상 제목"], input[placeholder*="제목"], .se-video-uploader-layer input[type="text"]'

    # '업로드 완료' 텍스트가 감지되거나 제목 입력칸이 표시·활성화되면 즉시 다음 단계로 진행 (최대 60초)
    wait_start = time.time()
    last_progress_log = wait_start
    ready_title_in = None
    while time.time() - wait_start < 60.0:
        try:
            t_in_chk = find_in_both(page, editor, title_Selectors)
            if t_in_chk.count() > 0:
                cand_in = t_in_chk.first
                if cand_in.is_visible() and cand_in.get_attribute("disabled") is None:
                    ready_title_in = cand_in
                    break
            if find_in_both(page, editor, 'text="업로드 완료"').count() > 0:
                break
        except Exception:
            pass
        now_t = time.time()
        if now_t - last_progress_log >= 10.0:
            print(f"⏳ [네이버 블로그] 동영상 업로드·인코딩 진행 중... ({int(now_t - wait_start)}초 경과)", flush=True)
            last_progress_log = now_t
        time.sleep(0.3)

    print(f"✅ [네이버 블로그] 동영상 업로드 준비 완료 ({time.time() - wait_start:.1f}초) → 제목·태그 입력 진행", flush=True)

    title_filled = False
    active_title_in = None
    for attempt in range(25):
        target_in = ready_title_in if (attempt == 0 and ready_title_in is not None) else None
        if target_in is None:
            t_in = find_in_both(page, editor, title_Selectors)
            if t_in.count() > 0:
                target_in = t_in.first
        if target_in is not None:
            try:
                if target_in.is_visible() and target_in.get_attribute("disabled") is None:
                    active_title_in = target_in
                    target_in.click(force=True)
                    time.sleep(0.12)
                    target_in.fill(safe_v_title)
                    time.sleep(0.15)
                    if not (target_in.input_value() or "").strip():
                        copy_to_clipboard(safe_v_title)
                        page.keyboard.press(f"{MOD_KEY}+v")
                        time.sleep(0.2)
                    page.keyboard.press("End")
                    page.keyboard.press("Space")
                    page.keyboard.press("Backspace")
                    time.sleep(0.15)
                    title_filled = True
                    print(f"✅ [네이버 블로그] 동영상 필수 제목(30자 이내) 입력 완료: {safe_v_title}", flush=True)
                    break
            except Exception:
                pass
        time.sleep(0.3)

    if not title_filled:
        copy_to_clipboard(safe_v_title)
        page.keyboard.press(f"{MOD_KEY}+v")
        time.sleep(0.25)

    if video_tags:
        enter_video_modal_tags(page, editor, video_tags, title_input=active_title_in)
        time.sleep(0.25)

    done_btn = find_in_both(page, editor, 'button.se-video-uploader-layer-button-submit, button:has-text("완료"), .se-video-uploader-layer button:has-text("완료")')
    done_btn.last.wait_for(state="visible", timeout=30000)

    for _ in range(90):
        btn_el = done_btn.last
        if btn_el.get_attribute("disabled") is None and btn_el.get_attribute("aria-disabled") != "true" and "disabled" not in (btn_el.get_attribute("class") or "").lower():
            break
        time.sleep(0.3)

    time.sleep(0.15)
    done_btn.last.click(force=True)
    print("✅ [네이버 블로그] 동영상 모달창 [완료] 클릭 성공!", flush=True)
    time.sleep(1.0)
    return True

def attach_place_map(page, editor, place_name):
    target_place = (place_name or get_place_search_keyword()).strip()
    if not target_place:
        return False
    print(f"🗺️ [네이버 블로그] 지도 장소('{target_place}') 첨부 시작...")
    try:
        pb = find_in_both(page, editor, 'button[data-name="place"], button:has-text("장소")')
        if pb.count() == 0:
            return False
        pb.first.click(force=True)
        time.sleep(2.5)

        si = find_in_both(page, editor, 'input[placeholder*="장소"], input.se-place-search-input, .se-popup-placesMap input[type="text"], input[type="search"]').first
        si.wait_for(state="visible", timeout=10000)
        si.click(force=True)
        time.sleep(0.4)
        si.fill("")
        time.sleep(0.2)
        si.fill(target_place)
        time.sleep(0.4)
        si.press("Enter")
        time.sleep(1.0)

        search_btn = find_in_both(page, editor, 'button.se-place-search-button, .se-popup-placesMap button[type="submit"], button[aria-label*="검색"]')
        if search_btn.count() > 0 and search_btn.first.is_visible():
            search_btn.first.click(force=True)
        time.sleep(2.5)

        place_kw = target_place.split()[0]
        items = find_in_both(page, editor, f'li.se-place-map-search-result-item, ul.se-place-map-search-result-list li, .se-popup-placesMap li:has-text("{place_kw}")')
        added_ok = False
        if items.count() > 0:
            first_item = items.first
            first_item.scroll_into_view_if_needed()
            first_item.hover()
            time.sleep(0.8)

            inner_add = first_item.locator('button.se-place-add-button, button:has-text("추가"), button')
            if inner_add.count() > 0:
                inner_add.first.click(force=True)
                added_ok = True
                print("✅ [네이버 블로그] 검색 항목 내부 [+ 추가] 버튼 클릭 완료!")
                time.sleep(1.2)

        if not added_ok:
            popup_adds = find_in_both(page, editor, '.se-popup-placesMap button.se-place-add-button, .se-popup-placesMap button:has-text("추가"), button.se-place-add-button')
            for i in range(popup_adds.count()):
                btn = popup_adds.nth(i)
                if btn.is_visible():
                    btn.click(force=True)
                    print("✅ [네이버 블로그] 팝업 내 [+ 추가] 버튼 클릭 완료!")
                    time.sleep(1.2)
                    break

        cpb = find_in_both(page, editor, '.se-popup-placesMap button.se-popup-button-confirm, button.se-popup-button-confirm, .se-popup-placesMap button:has-text("확인")')
        if cpb.count() > 0:
            target_confirm = cpb.last
            for _ in range(10):
                if target_confirm.get_attribute("disabled") is None and target_confirm.get_attribute("aria-disabled") != "true":
                    break
                time.sleep(0.5)
            target_confirm.click(force=True)
            print("✅ [네이버 블로그] 스마트플레이스 지도 본문 삽입 완료!")
            time.sleep(2.5)
            return True
    except Exception as e:
        print(f"⚠️ [네이버 블로그] 장소 첨부 중 오류: {e}")
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass
    return False

def select_blog_category(page, editor, cat):
    print(f"📂 [네이버 블로그] 카테고리 '{cat}' 선택 시도 중...")
    try:
        cat_btns = find_in_both(page, editor, 'div[class*="category"] button[class*="selectbox_button"], button[aria-label*="카테고리"], button[class*="selectbox_button"]')
        for i in range(cat_btns.count()):
            btn = cat_btns.nth(i)
            if not btn.is_visible():
                continue
            btn.click(force=True)
            time.sleep(0.8)
            for opt_sel in [f'label:text-is("{cat}")', f'span:text-is("{cat}")', f'li:text-is("{cat}")', f'div[role="option"]:has-text("{cat}")', f'label:has-text("{cat}")', f'li:has-text("{cat}")']:
                opt = find_in_both(page, editor, opt_sel)
                if opt.count() > 0 and opt.first.is_visible():
                    opt.first.click(force=True)
                    print(f"✅ [네이버 블로그] 카테고리 '{cat}' 선택 완료!")
                    time.sleep(0.6)
                    return True
    except Exception as e:
        print(f"⚠️ [네이버 블로그] 카테고리 선택 중 예외: {e}")
    return False

def is_naver_blog_logged_in(page):
    """네이버 로그인 화면(nid.naver.com)을 벗어나 블로그 에디터/블로그 영역에 진입했는지 판별"""
    url = (page.url or "").lower()
    if "nid.naver.com" in url or "nidlogin" in url:
        return False
    if is_any_locator_visible(page, 'input#id, input#pw'):
        return False
    if "blog.naver.com" in url and ("postwrite" in url or "redirect=write" in url or "goblogwrite" not in url):
        return True
    return False

def split_blog_intro_and_rest(body_text):
    """블로그 본문에서 첫 번째 [[소제목]] 또는 첫 번째 [미디어 자료1]([사진 1]) 직전까지의
    도입부와 나머지 본문을 분리. 해당 마커가 전혀 없으면 ('', body_text, False) 반환."""
    raw = (body_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()
    first_media_re = re.compile(
        r'^\[(?:미디어\s*자료|사진)\s*1(?:\s*[·•\-]?\s*GIF)?\]$',
        re.IGNORECASE,
    )
    split_idx = None
    for idx, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if (s.startswith("[[") and s.endswith("]]") and len(s) > 4) or first_media_re.match(s):
            split_idx = idx
            break
    if split_idx is None:
        return "", raw, False
    intro = "\n".join(lines[:split_idx]).strip()
    rest = "\n".join(lines[split_idx:]).strip()
    return intro, rest, True


def split_blog_body_for_closing_video(full_body):
    """동영상을 '본문 마무리 인사·예약 링크·매장 정보' 직전에 배치하기 위해
    전체 블로그 본문을 (main_before_video, closing_and_footer) 2영역으로 분리.
    - 대표사진 → 도입부 → 본문 + [미디어 자료1~5] → [완성 동영상] → 마무리 인사·예약 링크·스마트플레이스 지도"""
    raw = (full_body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return "", ""

    # 1) 하단 공식 구분선(━━━) 기준 본문 영역과 예약 링크·매장 정보 푸터 영역 분리
    div_m = re.search(r'\n*━{3,}', raw)
    if div_m:
        body_part = raw[:div_m.start()].strip()
        footer_part = raw[div_m.start():].lstrip("\n").strip()
    else:
        body_part = raw
        footer_part = ""

    # 2) 본문 영역 끝부분의 짧은 마무리 인사 단락(미디어/소제목 제외)이 있으면 동영상 아래로 자연 이동
    closing_cue_re = re.compile(
        r'(?:감사합니다|감사드립니다|고맙습니다|문의|방문해|기다리|연락|도와드리|이상으로|지금까지|오늘도|좋은\s*하루|행복한\s*하루|찾아주세요|예약|편하게\s*말씀|언제든)'
    )
    media_any_re = re.compile(
        r'\[(?:미디어\s*자료\s*\d+|사진\s*\d+(?:\s*[·•\-]?\s*GIF)?|GIF(?:\s*\d+)?)\]',
        re.IGNORECASE,
    )
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', body_part) if p.strip()]
    closing_para = ""
    if len(paragraphs) >= 2:
        last_p = paragraphs[-1]
        if (
            len(last_p) <= 160
            and "[[" not in last_p
            and not media_any_re.search(last_p)
            and closing_cue_re.search(last_p)
        ):
            closing_para = last_p
            body_part = "\n\n".join(paragraphs[:-1]).strip()

    tail_chunks = [c for c in (closing_para, footer_part) if c]
    closing_and_footer = "\n\n".join(tail_chunks).strip()
    return body_part, closing_and_footer


def upload_to_naver_blog(title, body="", tags=None, video_path=None, category=None, place_name=None, login_only=False, selected_images=None, rep_image=None, stop_checker=None, test_mode=False, shared_context=None, keep_context_open=False, shared_record_dir=None):
    prof = load_profile()
    banned_tags = get_effective_banned_tags(prof)

    clean_body = body.strip() if body and body.strip() else title.strip()
    final_title = clean_blog_title(title, clean_body, tags, profile=prof)

    user_cats = get_blog_categories(prof)
    req_cat = str(category or "").strip()
    if req_cat and (req_cat in user_cats or not user_cats):
        target_cat = req_cat
    elif user_cats:
        target_cat = recommend_blog_category(f"{title} {clean_body}", user_cats, men_keywords=MEN_KEYWORDS)
    else:
        target_cat = ""

    if not login_only:
        print(f"📌 [네이버 블로그] 게시판: '{target_cat or '(기본 게시판)'}'", flush=True)

    resolved_place = (place_name if place_name is not None else get_place_search_keyword(prof)).strip()
    f_body = build_full_body(clean_body, profile=prof)
    raw_tags_clean = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    f_tags = list(dict.fromkeys(raw_tags_clean))[:10]

    record_mode = "login" if login_only else "upload"
    uses_external_session = shared_context is not None
    record_dir = shared_record_dir if uses_external_session else create_blackbox_record_dir("네이버블로그", mode=record_mode)
    browser = None
    context = shared_context
    page = None
    video_obj = None

    def _run_blog_flow(active_ctx):
        nonlocal page, video_obj
        page = active_ctx.pages[0] if active_ctx.pages else active_ctx.new_page()
        video_obj = page.video

        page.goto("https://blog.naver.com/GoBlogWrite.naver", wait_until="domcontentloaded", timeout=45000)
        time.sleep(3.0)

        device_hint_shown = [False]
        def _on_naver_tick(pg, tick_idx, elapsed):
            cur_u = (pg.url or "").lower()
            if "deviceconfirm" in cur_u and not device_hint_shown[0]:
                device_hint_shown[0] = True
                print("ℹ️ [네이버 블로그] 새로운 환경 기기 등록 화면이 보이면 [등록] 또는 [등록안함]을 선택해 주세요.", flush=True)

        if not is_naver_blog_logged_in(page):
            wait_for_manual_login(
                page,
                "네이버 블로그",
                is_naver_blog_logged_in,
                timeout_sec=180,
                on_tick=_on_naver_tick,
                stop_checker=stop_checker,
            )
            save_storage_state_safely(active_ctx, COOKIES_PATH, "네이버 블로그")

        if login_only:
            save_storage_state_safely(active_ctx, COOKIES_PATH, "네이버 블로그")
            print("✅ [네이버 블로그] 로그인 점검 및 쿠키 저장 완료!", flush=True)
            return True

        editor = page.frame_locator("#mainFrame") if page.locator("#mainFrame").count() > 0 else page

        cb = find_in_both(page, editor, ".se-popup-button-cancel")
        if cb.count() > 0:
            cb.first.click()
            time.sleep(1)
        hc = find_in_both(page, editor, ".se-help-panel-close-button")
        if hc.count() > 0:
            hc.first.click()
            time.sleep(0.5)

        ta = find_in_both(page, editor, ".se-title-text")
        ta.first.click()
        time.sleep(0.3)
        copy_to_clipboard(final_title)
        page.keyboard.press(f"{MOD_KEY}+v")
        time.sleep(0.5)
        page.keyboard.press("Enter")
        time.sleep(0.5)

        # 본문 전체 가운데 정렬 적용 (대표사진·본문·동영상·지도 중심축 통일)
        ensure_editor_center_align(page, editor)

        # 최종 배치: 대표사진 → 도입부 → 본문 + [미디어 자료1~5] → 완성 동영상 → 마무리 인사·예약 링크·스마트플레이스 지도
        if rep_image and os.path.exists(rep_image):
            print(f"⭐ [네이버 블로그] 최상단 대표사진을 삽입합니다: {os.path.basename(rep_image)}", flush=True)
            attach_image_in_editor(page, editor, rep_image)
            time.sleep(0.5)
            ensure_editor_center_align(page, editor)

        main_before_video, closing_and_footer = split_blog_body_for_closing_video(f_body)
        if main_before_video:
            paste_formatted_body(page, editor, main_before_video, selected_images=selected_images)
            time.sleep(0.4)
            page.keyboard.press("Enter")
            time.sleep(0.3)

        if video_path and os.path.exists(video_path):
            print("🎬 [네이버 블로그] 본문 마무리 직전에 완성 동영상을 삽입합니다...", flush=True)
            attach_video_in_modal(page, editor, video_path, final_title, f_tags)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            time.sleep(0.6)
            ensure_editor_center_align(page, editor)

        if closing_and_footer:
            paste_formatted_body(page, editor, closing_and_footer, selected_images=[])
        time.sleep(1.0)
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")

        if resolved_place:
            attach_place_map(page, editor, resolved_place)
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")

        pub_btn = find_in_both(page, editor, 'button[class*="publish_btn"]')
        pub_btn.first.click()
        time.sleep(1.8)

        if target_cat:
            select_blog_category(page, editor, target_cat)
        else:
            print("ℹ️ [네이버 블로그] 게시판명이 비어 있어 카테고리 선택을 건너뛰고 기본 게시판에 발행합니다.", flush=True)
        enter_publish_drawer_tags(page, editor, f_tags)

        time.sleep(0.8)

        confirm_btn = find_in_both(page, editor, 'button[class*="confirm_btn"]')
        if test_mode:
            aim_res = verify_publish_button_aim(
                confirm_btn.first if confirm_btn.count() > 0 else confirm_btn,
                expected_keywords=["발행", "확인", "등록"],
                platform_name="네이버블로그",
            )
            if not aim_res["ok"]:
                raise RuntimeError(aim_res["message"])
            save_storage_state_safely(active_ctx, COOKIES_PATH, "네이버 블로그")
            if keep_context_open:
                print("🧪 [테스트 모드 - 네이버 블로그] 최종 발행 버튼 조준 검증 통과 → 동일 브라우저 새 탭으로 네이버 클립 테스트를 이어갑니다.", flush=True)
                return "test_mode_ok"
            print("🧪 [테스트 모드 - 네이버 블로그] 최종 발행 버튼 조준 검증 통과 (실제 발행 안 함)", flush=True)
            wait_for_test_mode_close(active_ctx, "네이버 블로그", stop_checker=stop_checker)
            return "test_mode_ok"

        confirm_btn.first.click()
        time.sleep(4)

        save_storage_state_safely(active_ctx, COOKIES_PATH, "네이버 블로그")
        blog_id = (prof.get("naver_blog_id") or "").strip()
        default_blog_url = f"https://blog.naver.com/{blog_id}" if blog_id else "https://blog.naver.com/"
        post_url = page.url if ("GoBlogWrite" not in page.url and "postwrite" not in page.url) else default_blog_url
        return post_url

    if uses_external_session:
        try:
            return _run_blog_flow(context)
        except InterruptedError as ie:
            record_platform_diagnostics("네이버블로그", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("네이버블로그", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [네이버 블로그] 멈춤 또는 오류 발생: {e}", flush=True)
            return False
        finally:
            try:
                if page is not None and not page.is_closed():
                    record_platform_diagnostics("네이버블로그", page=page)
            except Exception:
                pass
            try:
                if context is not None:
                    sync_cookies_from_persistent_context(context)
            except Exception:
                pass
            if not keep_context_open:
                finalize_playwright_session(
                    "네이버블로그",
                    mode=record_mode,
                    page=page,
                    context=context,
                    browser=browser,
                    video_obj=video_obj,
                    record_dir=record_dir,
                    platform_label="네이버 블로그",
                )

    with open_playwright_session() as p:
        try:
            context = launch_persistent_chromium(
                p,
                headless=False,
                viewport={"width": 1360, "height": 920},
                record_video_dir=record_dir,
                record_video_size={"width": 1360, "height": 920},
            )
            return _run_blog_flow(context)
        except InterruptedError as ie:
            record_platform_diagnostics("네이버블로그", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("네이버블로그", page=page, exc=e)
            if not (stop_checker and stop_checker()):
                print(f"⚠️ [네이버 블로그] 멈춤 또는 오류 발생: {e}", flush=True)
            return False
        finally:
            finalize_playwright_session(
                "네이버블로그",
                mode=record_mode,
                page=page,
                context=context,
                browser=browser,
                video_obj=video_obj,
                record_dir=record_dir,
                platform_label="네이버 블로그",
            )