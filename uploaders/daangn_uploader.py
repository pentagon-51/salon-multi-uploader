import os, re, sys, json, time, shutil, difflib
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
    load_profile,
    get_salon_name,
)

BASE_DIR = get_base_dir()
COOKIES_PATH = get_auth_file("daangn_cookies.json")


def _is_daangn_manager_page(page):
    """이미 개별 매장 관리 화면(bizprofile.daangn.com / biz_accounts / 관리자 홈 등)에 진입해 있는지 판별"""
    url = (page.url or "").lower().strip()
    if "bizprofile.daangn.com" in url or "/biz_accounts/" in url or "/manager/" in url:
        return True
    manager_sels = (
        'a:text-is("관리자 홈"), button:text-is("관리자 홈"), '
        'a:text-is("업체 정보"), a:has-text("소식 작성"), button:has-text("소식 작성"), '
        'a:has-text("새 소식 작성"), button:has-text("새 소식 작성"), '
        'a:text-is("운영자 관리"), a:text-is("프로필 가기")'
    )
    return is_any_locator_visible(page, manager_sels)


def is_daangn_logged_in(page):
    """당근 비즈프로필 로그인/랜딩 화면을 벗어나 실제 당근비즈니스 홈 또는 매장 관리 대시보드에 진입했는지 판별.
    (최신 business.daangn.com/profile/login 로그인 후 홈 및 bizprofile.daangn.com 관리자 홈 모두 인식)"""
    url = (page.url or "").lower().strip()
    if not url:
        return False
    if "accounts.daangn.com" in url:
        return False

    is_biz_host = any(h in url for h in ("business.daangn.com", "bizprofile.daangn.com", "biz.daangn.com"))
    if not is_biz_host:
        return False
    if any(k in url for k in ("/signin", "/oauth", "/auth/callback", "/intro", "/welcome")):
        return False

    # 1) 이미 개별 매장 관리 화면(bizprofile.daangn.com 등)에 진입한 경우 즉시 로그인 완료
    if _is_daangn_manager_page(page):
        if not is_any_locator_visible(page, 'button:text-is("로그인"), a:text-is("로그인")'):
            return True

    # 2) 당근비즈니스 로그인 후 홈(URL이 business.daangn.com/profile/login 인 경우 포함) 긍정 요소 확인
    biz_home_logged_in_sels = (
        'button:has-text("새 비즈프로필"), a:has-text("새 비즈프로필"), '
        'button:has-text("새 광고계정"), a:has-text("새 광고계정"), '
        'a:has-text("소식 작성"), button:has-text("소식 작성"), '
        'a:has-text("새 소식 작성"), button:has-text("새 소식 작성"), '
        'a:has-text("소식 올리기"), button:has-text("소식 쓰기"), '
        'a:has-text("소식 관리"), a:text-is("소식"), button:text-is("소식"), '
        'a:has-text("내 비즈프로필"), a:has-text("비즈프로필 관리"), '
        'button:has-text("로그아웃"), a:has-text("로그아웃"), '
        'a:has-text("운영자 관리"), a:has-text("프로필 관리"), '
        'a[href*="bizprofile.daangn.com"], a[href*="/biz_accounts/"], '
        'a[href*="/posts"], a[href*="/profiles/"], a[href*="/biz-profiles/"]'
    )
    has_login_cta = is_any_locator_visible(
        page,
        'button:text-is("로그인"), a:text-is("로그인"), button:has-text("무료로 시작하기"), a:has-text("무료로 시작하기")',
    )
    if not has_login_cta and is_any_locator_visible(page, biz_home_logged_in_sels):
        return True

    # 순수 로그인 경로(/login)인데 로그인 후 홈 요소가 없으면 미로그인
    if "/login" in url:
        return False

    # 호스트 이후 경로 추출
    host_key = next((h for h in ("bizprofile.daangn.com", "business.daangn.com", "biz.daangn.com") if h in url), "business.daangn.com")
    path_part = url.split(host_key, 1)[-1].split("?", 1)[0].split("#", 1)[0].strip("/")

    # 홍보/랜딩 루트('/') 전용 비로그인 버튼 감지
    landing_logged_out_sels = (
        'button:text-is("로그인"), a:text-is("로그인"), '
        'button:has-text("비즈프로필 시작하기"), a:has-text("비즈프로필 시작하기"), '
        'button:has-text("무료로 시작하기"), a:has-text("무료로 시작하기"), '
        'button:text-is("시작하기"), a:text-is("시작하기")'
    )
    if not path_part and is_any_locator_visible(page, landing_logged_out_sels):
        return False

    # 비로그인 랜딩/고객센터/약관 경로가 아닌 실제 대시보드 하위 경로 판별
    public_non_dashboard = ("guide", "faq", "help", "privacy", "terms", "policy", "about", "cases", "blog", "education")
    if path_part and not any(path_part.startswith(px) for px in public_non_dashboard):
        dashboard_prefixes = (
            "biz_accounts", "profiles", "profile", "biz-profiles", "biz", "home", "manage",
            "manager", "dashboard", "posts", "post", "news", "feeds", "stores", "store",
            "center", "my", "ad", "ads", "coupons", "reviews", "chats",
            "reservations", "settings", "select", "owner", "shop", "shops",
        )
        if any(path_part == px or path_part.startswith(px + "/") for px in dashboard_prefixes):
            if not is_any_locator_visible(page, 'button:text-is("로그인"), a:text-is("로그인")'):
                return True
        if re.match(r"^\d+(?:/|$)", path_part):
            if not is_any_locator_visible(page, 'button:text-is("로그인"), a:text-is("로그인")'):
                return True

    return False


def _normalize_store_cmp(text):
    return re.sub(r"[\s·•\-_/()\[\]]+", "", str(text or "")).lower()


def _extract_store_tokens(text):
    raw = re.sub(r"\bID\s*\d+\b", " ", str(text or ""), flags=re.IGNORECASE)
    parts = [p.strip().lower() for p in re.split(r"[\s·•\-_/()\[\],|]+", raw) if p.strip()]
    stop = {"점", "지점", "본점", "미용실", "헤어", "헤어샵", "헤어살롱", "비즈프로필", "관리", "운영중", "소유자", "운영자", "대표"}
    return [p for p in parts if len(p) >= 2 and p not in stop]


def choose_daangn_profile_card(candidates, target_salon):
    """사용자 설정 매장명(target_salon)을 기준으로 비즈프로필 카드 목록에서 3단계 우선순위 자동 매칭.
    반환: (chosen_candidate_dict, match_reason_str) 또는 (None, '')"""
    if not candidates:
        return None, ""

    target_raw = str(target_salon or "").strip()
    target_norm = _normalize_store_cmp(target_raw)
    target_tokens = _extract_store_tokens(target_raw)

    # 1순위: 정확 일치 (공백·구분자 제거·대소문자 무시 기준)
    if target_norm:
        for cand in candidates:
            cname = str(cand.get("name") or "").strip()
            cnorm = _normalize_store_cmp(cname)
            if cnorm and cnorm == target_norm:
                return cand, "1순위 정확 일치"

    # 2순위: 유사도·브랜드명 매칭 (공통 핵심 어절 / 포함 관계 / 글자 유사도 종합 최고점)
    if target_norm:
        best_cand = None
        best_score = 0.0
        brand_tok = target_tokens[0] if target_tokens else ""
        for cand in candidates:
            cname = str(cand.get("name") or "").strip()
            cnorm = _normalize_store_cmp(cname)
            if not cnorm:
                continue
            ctokens = _extract_store_tokens(cname)
            shared = len(set(target_tokens) & set(ctokens)) if target_tokens and ctokens else 0
            brand_hit = bool(brand_tok and len(brand_tok) >= 2 and brand_tok in cnorm)
            substr_hit = (target_norm in cnorm) or (cnorm in target_norm)
            seq_ratio = difflib.SequenceMatcher(None, target_norm, cnorm).ratio()

            if not (brand_hit or shared >= 1 or substr_hit or seq_ratio >= 0.45):
                continue

            score = seq_ratio
            if substr_hit:
                score += 1.0
            if brand_hit:
                score += 0.8
            score += shared * 0.5
            if score > best_score:
                best_score = score
                best_cand = cand
        if best_cand is not None:
            return best_cand, "2순위 브랜드명·유사도 매칭"

    # 3순위: 단독·첫 번째 카드 폴백
    return candidates[0], "3순위 첫 번째 비즈프로필 폴백"


def _discover_daangn_bizprofile_cards(page):
    """당근비즈니스 홈 화면에서 실제 비즈프로필 매장 카드 요소만 추출하고 임시 식별 속성(data-agy-biz-idx)을 부여"""
    js = """() => {
        // 이전 마킹 초기화
        document.querySelectorAll('[data-agy-biz-idx]').forEach(el => el.removeAttribute('data-agy-biz-idx'));

        const exactExclude = new Set([
            '당근비즈니스', '비즈프로필', '광고', '알림', '채팅', '공지사항', '이용방법 · 문의',
            '새 비즈프로필', '새 광고계정', '간편모드', '전문가모드', '전체', '전문가모드 광고',
            '네이버 블로그', '카카오 채널', '인스타그램', '유튜브', '개인정보처리방침', '이용약관',
            '광고 운영정책', '당근스토어 판매자 운영정책', '당근스토어 판매자 이용약관', '포장주문 이용약관',
            '로그인', '로그아웃', '시작하기', '무료로 시작하기', '닫기', '고객센터', '이용가이드',
            '이전 페이지네이션으로', '다음 페이지네이션으로', '관리자 홈', '더보기'
        ]);
        const substrExclude = [
            '새 비즈프로필', '새 광고계정', '페이지네이션', '유튜브', '운영정책', '이용약관',
            '개인정보처리방침', '성공 전략', '성공사례', '비즈프로필이란', '광고 3계명', '인기 광고',
            '자주 묻는 질문', '이용방법', '공지사항', '고객센터'
        ];

        const isVisible = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (!st || st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') === 0) return false;
            const r = el.getBoundingClientRect();
            return r && r.width >= 40 && r.height >= 24;
        };

        const cleanName = (raw) => {
            const lines = String(raw || '').split(/\\n+/).map(s => s.replace(/\\s+/g, ' ').trim()).filter(Boolean);
            for (const ln of lines) {
                const stripped = ln.replace(/\\bID\\s*\\d+\\b/ig, '').replace(/(?:운영중|관리하기|관리|소유자|운영자|대표)$/g, '').trim();
                if (!stripped || /^\\d+$/.test(stripped)) continue;
                if (exactExclude.has(stripped)) continue;
                if (substrExclude.some(ex => stripped.includes(ex))) continue;
                if (stripped.length >= 2 && stripped.length <= 45) return stripped;
            }
            return '';
        };

        const found = [];
        const seenEl = new Set();
        const seenNames = new Set();

        const tryAdd = (el, priority) => {
            if (!el || seenEl.has(el) || !isVisible(el)) return;
            // 헤더/푸터 내 단순 내비게이션 배제
            if (el.closest('header, footer')) return;
            const rawText = (el.innerText || el.textContent || '').trim();
            if (!rawText || rawText.length > 160) return;
            const name = cleanName(rawText);
            if (!name) return;
            if (exactExclude.has(name) || substrExclude.some(ex => name.includes(ex))) return;
            const key = name.replace(/\\s+/g, '').toLowerCase();
            if (seenNames.has(key)) return;
            seenEl.add(el);
            seenNames.add(key);
            const idx = found.length;
            el.setAttribute('data-agy-biz-idx', String(idx));
            found.push({
                idx,
                name,
                href: (el.getAttribute('href') || ''),
                priority
            });
        };

        // 1) 가장 정확: '새 비즈프로필' 버튼이 속한 비즈프로필 목록 섹션 내 형제 카드 탐색
        const allClickable = Array.from(document.querySelectorAll('a, button, [role="button"], li, div'));
        const newBizBtn = allClickable.find(el => {
            if (!isVisible(el)) return false;
            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            return t === '새 비즈프로필' || t === '+ 새 비즈프로필';
        });
        if (newBizBtn) {
            let container = newBizBtn.parentElement;
            for (let depth = 0; depth < 4 && container; depth++) {
                let kids = [];
                try {
                    kids = Array.from(container.querySelectorAll('a, button, [role="button"], [tabindex="0"], li, :scope > div')).filter(
                        el => el !== newBizBtn && !el.contains(newBizBtn) && !newBizBtn.contains(el)
                    );
                } catch (e) {
                    kids = Array.from(container.querySelectorAll('a, button, [role="button"], [tabindex="0"], li')).filter(
                        el => el !== newBizBtn && !el.contains(newBizBtn) && !newBizBtn.contains(el)
                    );
                }
                const beforeCount = found.length;
                for (const k of kids) {
                    const href = (k.getAttribute('href') || '').toLowerCase();
                    if (href.includes('youtube') || href.includes('naver') || href.includes('kakao') || href.includes('instagram')) continue;
                    const st = window.getComputedStyle(k);
                    const clickable = k.tagName === 'A' || k.tagName === 'BUTTON' || k.getAttribute('role') === 'button' || (st && st.cursor === 'pointer');
                    if (clickable) tryAdd(k, 1);
                }
                if (found.length > beforeCount) break;
                container = container.parentElement;
            }
        }

        // 2) 직접 링크/카드 클래스 기반 탐색 (bizprofile.daangn.com, /biz_accounts/, /profiles/ 등)
        const directSels = [
            'a[href*="bizprofile.daangn.com"]',
            'a[href*="/biz_accounts/"]',
            'a[href*="/profiles/"]',
            'a[href*="/manager"]',
            '[data-testid*="biz-profile" i]',
            '[data-testid*="profile-card" i]',
            '[class*="BizProfile" i] a, [class*="BizProfile" i] button, [class*="BizProfile" i] [role="button"]',
            '[class*="ProfileCard" i]'
        ];
        for (const sel of directSels) {
            try {
                document.querySelectorAll(sel).forEach(el => tryAdd(el, 2));
            } catch (e) {}
        }

        return found;
    }"""
    try:
        res = page.evaluate(js)
        return res if isinstance(res, list) else []
    except Exception:
        return []


def select_daangn_bizprofile_card(page, context, target_salon):
    """당근비즈니스 홈에서 사용자 설정 매장명 기반 3단계 자동 매칭으로 비즈프로필 카드를 클릭해 매장 관리 화면으로 진입"""
    if _is_daangn_manager_page(page):
        print("🥕 [당근 비즈프로필] 이미 매장 관리 화면에 진입해 있습니다.", flush=True)
        return page

    # 카드 목록 렌더링 대기 (최대 5초)
    candidates = []
    for _ in range(10):
        if _is_daangn_manager_page(page):
            return page
        candidates = _discover_daangn_bizprofile_cards(page)
        if candidates:
            break
        time.sleep(0.5)

    if not candidates:
        # 상단 '비즈프로필' 탭을 한 번 눌러 목록 새로고침 시도
        try:
            biz_tab = page.locator('a:text-is("비즈프로필"), button:text-is("비즈프로필")').first
            if biz_tab.count() > 0 and biz_tab.is_visible(timeout=1200):
                biz_tab.click(force=True)
                time.sleep(2.0)
                candidates = _discover_daangn_bizprofile_cards(page)
        except Exception:
            pass

    if not candidates:
        print("ℹ️ [당근 비즈프로필] 홈 화면에서 선택 가능한 비즈프로필 카드를 찾지 못해 현재 화면에서 계속 진행합니다.", flush=True)
        return page

    chosen, reason = choose_daangn_profile_card(candidates, target_salon)
    if not chosen:
        return page

    chosen_name = chosen.get("name") or "(매장)"
    chosen_idx = chosen.get("idx")
    print(f"🥕 [당근 비즈프로필] '{chosen_name}' 관리 화면으로 진입합니다... ({reason})", flush=True)

    prev_url = (page.url or "").strip()
    prev_pages = set(context.pages) if context is not None else set()

    try:
        card_loc = page.locator(f'[data-agy-biz-idx="{chosen_idx}"]').first
        card_loc.scroll_into_view_if_needed()
        time.sleep(0.3)
        card_loc.click(force=True)
    except Exception as click_err:
        print(f"⚠️ [당근 비즈프로필] 카드 클릭 재시도({click_err})...", flush=True)
        try:
            page.locator(f'a:has-text("{chosen_name}"), button:has-text("{chosen_name}"), div[role="button"]:has-text("{chosen_name}")').first.click(force=True)
        except Exception:
            pass

    # 매장 관리 화면 전환 대기 (새 탭 오픈 또는 현재 탭 URL/메뉴 로드, 최대 12초)
    active_page = page
    for _ in range(24):
        if context is not None:
            for pg in list(context.pages):
                if pg not in prev_pages and not pg.is_closed():
                    active_page = pg
                    break
        cur_url = (active_page.url or "").strip()
        if _is_daangn_manager_page(active_page):
            time.sleep(1.5)
            print(f"✅ [당근 비즈프로필] '{chosen_name}' 관리 화면 진입 완료! ({cur_url})", flush=True)
            return active_page
        if cur_url and cur_url != prev_url and ("bizprofile.daangn.com" in cur_url.lower() or "/biz_accounts/" in cur_url.lower() or "/manager" in cur_url.lower()):
            time.sleep(2.0)
            print(f"✅ [당근 비즈프로필] '{chosen_name}' 관리 화면 진입 완료! ({cur_url})", flush=True)
            return active_page
        time.sleep(0.5)

    return active_page


def dismiss_daangn_draft_popup(page, max_wait_sec=2.5):
    """당근 소식 작성 화면 진입 직후 「작성 중인 소식이 있어요」 임시 저장 안내 팝업을 감지하면
    [이어서 쓰기]가 아닌 [새로 쓰기]를 클릭해 초기화하고 모달이 사라질 때까지 대기.
    팝업이 없는 정상 케이스에서는 0.8초 내외에 빠르게 통과."""
    draft_text_sels = ':text("작성 중인 소식이 있어요"), :text("작성중인 소식이 있어요"), :text("임시 저장된 소식"), :text("임시저장된 소식")'
    new_write_sels = [
        'div[role="dialog"] button:text-is("새로 쓰기")',
        'div[role="dialog"] button:has-text("새로 쓰기")',
        'button:text-is("새로 쓰기")',
        'button:has-text("새로 쓰기")',
        'a:has-text("새로 쓰기")',
        '[role="button"]:has-text("새로 쓰기")',
        'text=새로 쓰기',
    ]
    confirm_2nd_sels = [
        'div[role="dialog"] button:has-text("새로 쓰기")',
        'div[role="dialog"] button:has-text("삭제")',
        'div[role="dialog"] button:text-is("확인")',
        'div[role="dialog"] button:has-text("확인")',
    ]
    editor_sels = 'textarea[placeholder*="소식"], textarea[placeholder*="내용"], div[contenteditable="true"], textarea'

    def _find_visible_btn(selectors):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
                for idx in range(cnt - 1, -1, -1):
                    cand = loc.nth(idx)
                    if cand.is_visible(timeout=200):
                        return cand
            except Exception:
                pass
        return None

    start_ts = time.time()
    clicked = False
    while (time.time() - start_ts) < max_wait_sec:
        has_draft_text = is_any_locator_visible(page, draft_text_sels)
        new_btn = _find_visible_btn(new_write_sels)

        if has_draft_text or new_btn is not None:
            if new_btn is None:
                time.sleep(0.25)
                new_btn = _find_visible_btn(new_write_sels)
            if new_btn is not None:
                try:
                    new_btn.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    new_btn.click(force=True)
                    clicked = True
                except Exception:
                    time.sleep(0.25)
                    retry_btn = _find_visible_btn(new_write_sels)
                    if retry_btn is not None:
                        try:
                            retry_btn.click(force=True)
                            clicked = True
                        except Exception:
                            pass

            if clicked:
                # 모달이 완전히 사라질 때까지 짧게 대기 (혹시 2차 확인 팝업이 뜨면 함께 처리)
                wait_end = time.time() + 1.5
                while time.time() < wait_end:
                    time.sleep(0.25)
                    still_draft = is_any_locator_visible(page, draft_text_sels)
                    still_new_btn = _find_visible_btn(new_write_sels)
                    if not still_draft and still_new_btn is None:
                        # 2차 확인 다이얼로그가 떠 있는 경우만 추가 클릭
                        sec_btn = _find_visible_btn(confirm_2nd_sels)
                        if sec_btn is not None:
                            try:
                                sec_btn.click(force=True)
                                time.sleep(0.5)
                            except Exception:
                                pass
                        break
                print("🧹 [당근 비즈] 임시 저장 팝업 감지 → [새로 쓰기] 클릭 완료", flush=True)
                time.sleep(0.5)
                return True

        # 정상 케이스(팝업 없음): 에디터가 이미 표시되고 다이얼로그가 없으면 불필요한 대기 없이 즉시 통과
        elapsed = time.time() - start_ts
        if elapsed >= 0.75 and is_any_locator_visible(page, editor_sels) and not is_any_locator_visible(page, 'div[role="dialog"]'):
            return False
        time.sleep(0.25)

    return False


def _wait_daangn_composer_ready(page, timeout_sec=10.0):
    """[새로 쓰기] 클릭 후 DOM 리렌더링을 포함해 새 소식 작성 폼(본문/제목/사진 영역)이 상호작용 가능해질 때까지 안전 대기"""
    editor_sels = 'textarea[placeholder*="소식"], textarea[placeholder*="내용"], div[contenteditable="true"], textarea'
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # 혹시 지연되어 뜬 임시 저장 팝업이 남아 있으면 한 번 더 안전 처리
        if is_any_locator_visible(page, ':text("작성 중인 소식이 있어요"), button:text-is("새로 쓰기")'):
            dismiss_daangn_draft_popup(page, max_wait_sec=2.0)
        try:
            b_loc = page.locator(editor_sels).first
            if b_loc.count() > 0 and b_loc.is_visible(timeout=300):
                try:
                    if not b_loc.is_disabled():
                        return True
                except Exception:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return is_any_locator_visible(page, editor_sels)


def open_daangn_news_composer(page):
    """당근 비즈프로필 매장 관리 화면에서 '소식' 메뉴 → '소식 작성'(또는 새 소식 작성/글쓰기) 작성 폼으로 안전 진입"""
    # 초기 안내 모달이 떠 있을 경우에만 모달 내부 닫기 클릭 (임시 저장 팝업은 전용 핸들러로 처리)
    try:
        if not is_any_locator_visible(page, ':text("작성 중인 소식이 있어요"), button:text-is("새로 쓰기")'):
            modal_close = page.locator('div[role="dialog"] button:text-is("닫기"), div[role="dialog"] button[aria-label="닫기"]').first
            if modal_close.count() > 0 and modal_close.is_visible(timeout=800):
                modal_close.click(force=True)
                time.sleep(0.6)
    except Exception:
        pass

    editor_sels = 'textarea[placeholder*="소식"], textarea[placeholder*="내용"], div[contenteditable="true"], textarea'
    if is_any_locator_visible(page, editor_sels):
        dismiss_daangn_draft_popup(page, max_wait_sec=2.5)
        _wait_daangn_composer_ready(page, timeout_sec=8.0)
        return True

    compose_btn_sels = [
        'button:has-text("소식 작성")',
        'a:has-text("소식 작성")',
        'button:has-text("새 소식 작성")',
        'a:has-text("새 소식 작성")',
        'button:has-text("소식 쓰기")',
        'a:has-text("소식 쓰기")',
        'button:has-text("소식 올리기")',
        'a:has-text("소식 올리기")',
        'button:text-is("글쓰기")',
        'a:text-is("글쓰기")',
        'button:has-text("글쓰기")',
        'a:has-text("글쓰기")',
        'a[href*="/posts/new"]',
        'a[href*="/news/new"]',
        'a[href*="/write"]',
    ]

    def _try_click_compose():
        for sel in compose_btn_sels:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible(timeout=1200):
                    el.scroll_into_view_if_needed()
                    el.click(force=True)
                    print(f"✍️ [당근 비즈프로필] 소식 작성 버튼 클릭 완료 ({sel})", flush=True)
                    time.sleep(1.2)
                    dismiss_daangn_draft_popup(page, max_wait_sec=2.5)
                    _wait_daangn_composer_ready(page, timeout_sec=10.0)
                    return True
            except Exception:
                pass
        return False

    # 1) 현재 화면에 이미 소식 작성 버튼이 보이면 바로 클릭
    if _try_click_compose():
        return True

    # 2) 좌측/상단 관리 메뉴에서 '소식' 메뉴 먼저 클릭
    news_menu_sels = [
        'nav a:text-is("소식")',
        'aside a:text-is("소식")',
        'a:text-is("소식")',
        'button:text-is("소식")',
        'div[role="button"]:text-is("소식")',
        'a[href*="/posts"]',
        'a[href*="/news"]',
        'a[href*="/feeds"]',
        'a:has-text("소식 관리")',
    ]
    for menu_sel in news_menu_sels:
        try:
            m_el = page.locator(menu_sel).first
            if m_el.count() > 0 and m_el.is_visible(timeout=1500):
                m_el.scroll_into_view_if_needed()
                m_el.click(force=True)
                print("📂 [당근 비즈프로필] '소식' 메뉴로 이동했습니다.", flush=True)
                time.sleep(2.5)
                break
        except Exception:
            pass

    if is_any_locator_visible(page, editor_sels):
        dismiss_daangn_draft_popup(page, max_wait_sec=2.5)
        _wait_daangn_composer_ready(page, timeout_sec=8.0)
        return True

    # 3) 소식 관리 화면 로드 후 소식 작성 버튼 탐색 (최대 5초 재시도)
    for _ in range(4):
        if _try_click_compose():
            return True
        if is_any_locator_visible(page, editor_sels):
            dismiss_daangn_draft_popup(page, max_wait_sec=2.5)
            _wait_daangn_composer_ready(page, timeout_sec=8.0)
            return True
        time.sleep(1.0)
    return False


def upload_to_daangn(title="", body="", video_path=None, login_only=False, selected_images=None, stop_checker=None, test_mode=False):
    valid_images = [p for p in (selected_images or []) if p and os.path.exists(p)][:4]
    has_video = bool(video_path and os.path.exists(video_path))
    if not login_only and not valid_images and not has_video:
        print("⚠️ [당근 비즈] 업로드할 사진 또는 영상 파일이 없습니다.", flush=True)
        return False

    prof = load_profile()
    target_salon = get_salon_name(prof, fallback="")

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

            active_page_holder = [page]
            def _is_daangn_logged_in_any_tab(pg):
                if is_daangn_logged_in(pg):
                    active_page_holder[0] = pg
                    return True
                try:
                    for other_pg in list(context.pages):
                        if other_pg is not pg and not other_pg.is_closed() and is_daangn_logged_in(other_pg):
                            active_page_holder[0] = other_pg
                            return True
                except Exception:
                    pass
                return False

            clicked_login = [False]
            def _on_daangn_tick(pg, tick_idx, elapsed):
                if not clicked_login[0] and "accounts.daangn.com" not in (pg.url or "").lower():
                    for lsel in ['button:text-is("시작하기")', 'a:text-is("시작하기")', 'button:has-text("비즈프로필 시작하기")', 'a:has-text("비즈프로필 시작하기")', 'button:has-text("무료로 시작하기")', 'a:has-text("무료로 시작하기")', 'button:text-is("로그인")', 'a:text-is("로그인")']:
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

            if not _is_daangn_logged_in_any_tab(page):
                wait_for_manual_login(
                    page,
                    "당근 비즈",
                    _is_daangn_logged_in_any_tab,
                    timeout_sec=180,
                    on_tick=_on_daangn_tick,
                    stop_checker=stop_checker,
                )
                page = active_page_holder[0]
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)
            else:
                page = active_page_holder[0]
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)

            if login_only:
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)
                print("✅ [당근 비즈] 로그인 점검 및 쿠키 저장 완료!", flush=True)
                return True

            # 1단계: 당근비즈니스 홈 → 사용자 설정 매장명 기반 3단계 자동 매칭으로 비즈프로필 매장 관리 화면 진입
            page = select_daangn_bizprofile_card(page, context, target_salon)
            save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)

            # 2단계: 매장 관리 화면 → '소식' 메뉴 → '소식 작성' 폼 진입 (+ 임시 저장 팝업 [새로 쓰기] 자동 처리)
            open_daangn_news_composer(page)
            dismiss_daangn_draft_popup(page, max_wait_sec=2.0)
            _wait_daangn_composer_ready(page, timeout_sec=10.0)

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
            if test_mode:
                aim_res = verify_publish_button_aim(
                    sub_btn,
                    expected_keywords=["발행", "등록", "완료"],
                    platform_name="당근",
                )
                if not aim_res["ok"]:
                    raise RuntimeError(aim_res["message"])
                print("🧪 [테스트 모드 - 당근 비즈] 최종 발행 버튼 조준 검증 통과 (실제 발행 안 함)", flush=True)
                save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)
                wait_for_test_mode_close(context, "당근 비즈", stop_checker=stop_checker)
                return True

            if sub_btn.count() > 0:
                sub_btn.click(force=True)
                time.sleep(5.0)

            save_storage_state_safely(context, COOKIES_PATH, "당근 비즈", mark_verified=True)
            return True
        except InterruptedError as ie:
            record_platform_diagnostics("당근", page=page, exc=ie, reason="사용자 요청으로 중단/건너뜀")
            return False
        except Exception as e:
            record_platform_diagnostics("당근", page=page, exc=e)
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