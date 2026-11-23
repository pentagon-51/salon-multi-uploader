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
    is_target_closed_error,
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


def _check_stopped(stop_checker):
    if stop_checker is None:
        return False
    try:
        return bool(stop_checker())
    except Exception:
        return False


def _find_x_post_button(page):
    """엑스(X) 작성 화면의 실제 게시 버튼(tweetButton 우선, 없으면 tweetButtonInline) 탐색"""
    for sel in ('button[data-testid="tweetButton"]', 'button[data-testid="tweetButtonInline"]'):
        try:
            loc = page.locator(sel)
            cnt = min(loc.count(), 5)
            for i in range(cnt):
                cand = loc.nth(i)
                if cand.is_visible(timeout=200):
                    return cand
        except Exception as e:
            if is_target_closed_error(e):
                raise
    return None


def _is_x_post_btn_enabled(btn):
    """엑스(X) [게시] 버튼이 disabled / aria-disabled / 비활성 스타일 없이 실제로 클릭 가능한지 엄격히 검증"""
    if btn is None:
        return False
    try:
        if not btn.is_visible(timeout=200):
            return False
        if btn.get_attribute("disabled") is not None:
            return False
        if (btn.get_attribute("aria-disabled") or "").strip().lower() == "true":
            return False
        if btn.is_disabled():
            return False
        return bool(
            btn.evaluate(
                """(el) => {
                    if (!el || el.disabled || el.hasAttribute('disabled')) return false;
                    if ((el.getAttribute('aria-disabled') || '').toLowerCase() === 'true') return false;
                    const cs = window.getComputedStyle(el);
                    if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || cs.pointerEvents === 'none') return false;
                    const op = parseFloat(cs.opacity || '1');
                    if (!isNaN(op) && op < 0.65) return false;
                    return true;
                }"""
            )
        )
    except Exception as e:
        if is_target_closed_error(e):
            raise
        return False


def _get_x_upload_state(page):
    """엑스(X) 작성창 내부의 미디어 첨부 여부, 업로드/인코딩 진행 상태, 오류 알림을 한 번에 점검"""
    default_state = {
        "has_media": False,
        "is_uploading": False,
        "progress_text": "",
        "error_text": "",
    }
    try:
        res = page.evaluate(
            """() => {
                const isVis = (el) => {
                    if (!el) return false;
                    const cs = window.getComputedStyle(el);
                    if (!cs || cs.display === 'none' || cs.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const modal = Array.from(document.querySelectorAll('div[aria-modal="true"], div[role="dialog"]')).find(isVis);
                const scope = modal || document.querySelector('main') || document.body;

                const mediaSels = [
                    '[data-testid="attachments"]',
                    '[data-testid="videoComponent"]',
                    '[data-testid="videoPlayer"]',
                    'video',
                    'button[aria-label*="미디어 삭제"]',
                    'button[aria-label*="미디어 제거"]',
                    'button[aria-label*="동영상 삭제"]',
                    'button[aria-label*="Remove media" i]',
                    'button[aria-label*="Delete video" i]'
                ];
                let hasMedia = false;
                for (const sel of mediaSels) {
                    const els = scope.querySelectorAll(sel);
                    for (const el of els) {
                        if (isVis(el)) {
                            hasMedia = true;
                            break;
                        }
                    }
                    if (hasMedia) break;
                }

                let isUploading = false;
                let progressText = '';

                const attachRoot = scope.querySelector('[data-testid="attachments"]') || scope;
                const attachTxt = (attachRoot.innerText || '').replace(/\\s+/g, ' ').trim();
                const hasDonePhrase = /(?:^|\\b)(ready|uploaded|100%)(?:\\b|$)|(준비됨|업로드됨|업로드\\s*완료)/i.test(attachTxt);

                if (hasDonePhrase) {
                    // 'Ready' / '준비됨' / 'Uploaded' / '업로드됨' 문구가 보이면 업로드 완료로 즉시 인식
                    isUploading = false;
                    progressText = 'Ready';
                } else {
                    // 1) 진행바(progressbar) 감지 — 영상 플레이어 내부 재생바/탐색바는 제외
                    const progEls = scope.querySelectorAll('[role="progressbar"]');
                    for (const p of progEls) {
                        if (!isVis(p)) continue;
                        if (p.closest('[data-testid="videoPlayer"], [data-testid="videoComponent"], video, [data-testid="scrubber"]')) {
                            continue;
                        }
                        const pAria = (p.getAttribute('aria-label') || '').toLowerCase();
                        if (/(seek|timeline|slider|volume|재생|탐색|볼륨|시간)/i.test(pAria)) {
                            continue;
                        }
                        const vNow = p.getAttribute('aria-valuenow');
                        const vMax = p.getAttribute('aria-valuemax');
                        const numNow = vNow !== null && vNow !== '' ? parseFloat(vNow) : NaN;
                        const numMax = vMax !== null && vMax !== '' ? parseFloat(vMax) : 100;
                        if (isNaN(numNow) || numNow < numMax) {
                            isUploading = true;
                            progressText = isNaN(numNow) ? 'progressbar' : `${Math.round(numNow)}%`;
                            break;
                        }
                    }

                    // 2) 첨부 영역/모달 내 업로드·처리 중 텍스트 감지
                    if (!isUploading && attachTxt) {
                        const hasUploadingPhrase = /(업로드\\s*중|처리\\s*중|인코딩\\s*중|uploading|processing)/i.test(attachTxt);
                        const pctMatch = attachTxt.match(/\\b([0-9]|[1-9][0-9])%/);
                        if (hasUploadingPhrase) {
                            isUploading = true;
                            progressText = pctMatch ? `${pctMatch[0]}` : '업로드/처리 중';
                        } else if (pctMatch && hasMedia) {
                            isUploading = true;
                            progressText = pctMatch[0];
                        }
                    }
                }

                // 3) 미디어 업로드 실패/제한 오류 배너·토스트 감지
                const errPatterns = [
                    '업로드할 수 없', '처리할 수 없', '지원되지 않는', '지원하지 않는',
                    '너무 깁니다', '파일이 너무', '오류가 발생', '문제가 발생', '업로드 실패',
                    'could not be', "couldn't be", "can't be uploaded", 'failed to upload',
                    'too long', 'too large', 'unsupported', 'something went wrong', 'media failed'
                ];
                let errorText = '';
                const alertRoots = [
                    ...document.querySelectorAll('[data-testid="toast"], [role="alert"]'),
                    attachRoot
                ];
                for (const el of alertRoots) {
                    if (!el || !isVis(el)) continue;
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!t) continue;
                    const tl = t.toLowerCase();
                    if (errPatterns.some(pat => tl.includes(pat))) {
                        errorText = t.slice(0, 180);
                        break;
                    }
                }

                return {
                    has_media: hasMedia,
                    is_uploading: isUploading,
                    progress_text: progressText,
                    error_text: errorText,
                };
            }"""
        )
        if isinstance(res, dict):
            return {
                "has_media": bool(res.get("has_media")),
                "is_uploading": bool(res.get("is_uploading")),
                "progress_text": str(res.get("progress_text") or "").strip(),
                "error_text": str(res.get("error_text") or "").strip(),
            }
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return default_state


def _wait_x_media_and_post_btn_ready(page, timeout_sec=120.0, stop_checker=None):
    """영상 파일 첨부 후 업로드·인코딩 100% 완료 및 [게시] 버튼 실제 활성화까지 대기.
    실패/미첨부/비활성 고착 시 절대 그냥 넘어가지 않고 RuntimeError를 발생시킴."""
    start_ts = time.time()
    last_log_ts = 0.0
    saw_media = False
    saw_uploading = False
    stable_ready_ticks = 0
    last_state = {"has_media": False, "is_uploading": False, "progress_text": "", "error_text": ""}

    while (time.time() - start_ts) < timeout_sec:
        if _check_stopped(stop_checker):
            raise InterruptedError("엑스(X) 영상 업로드 대기 중 사용자 중지 요청")

        st = _get_x_upload_state(page)
        last_state = st
        if st["error_text"]:
            raise RuntimeError(f"엑스(X) 영상 업로드 오류 감지: {st['error_text']}")

        if st["has_media"]:
            saw_media = True
        if st["is_uploading"]:
            saw_uploading = True

        elapsed = time.time() - start_ts
        if not saw_media and not saw_uploading and elapsed >= 25.0:
            raise RuntimeError("엑스(X) 영상 파일이 첨부되지 않았습니다(25초 내 첨부 미리보기·진행바 미감지).")

        post_btn = _find_x_post_button(page)
        btn_enabled = _is_x_post_btn_enabled(post_btn)

        if saw_media and (not st["is_uploading"]) and (post_btn is not None) and btn_enabled:
            stable_ready_ticks += 1
            if stable_ready_ticks >= 2:
                print("✅ [엑스(X)] 영상 업로드·인코딩 완료 및 [게시] 버튼 활성화 확인!", flush=True)
                return post_btn
        else:
            stable_ready_ticks = 0
            if elapsed - last_log_ts >= 10.0:
                status_detail = st["progress_text"] or ("영상 인코딩/업로드 중" if st["is_uploading"] else ("버튼 활성화 대기" if saw_media else "첨부 확인 중"))
                print(f"⏳ [엑스(X)] 업로드 완료 대기 중 ({int(elapsed)}초 경과 · {status_detail})...", flush=True)
                last_log_ts = elapsed

        time.sleep(0.75)

    if last_state.get("is_uploading"):
        raise RuntimeError(f"엑스(X) 영상 업로드/인코딩이 {int(timeout_sec)}초 내에 100% 완료되지 않아 실패로 판정했습니다.")
    if not saw_media:
        raise RuntimeError("엑스(X) 영상 첨부 미리보기가 확인되지 않아 실패로 판정했습니다.")
    raise RuntimeError(f"엑스(X) [게시] 버튼이 {int(timeout_sec)}초 내에 활성화(disabled/aria-disabled 해제)되지 않아 실패로 판정했습니다.")


def _inspect_x_publish_feedback(page):
    """게시 버튼 클릭 후 화면의 성공/실패 토스트·알림 및 작성창 잔존 여부를 반환"""
    try:
        res = page.evaluate(
            """() => {
                const isVis = (el) => {
                    if (!el) return false;
                    const cs = window.getComputedStyle(el);
                    if (!cs || cs.display === 'none' || cs.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const errKeywords = [
                    '실패', '오류', '문제가 발생', '중복', '이미 전송', '전송할 수 없', '게시할 수 없',
                    'error', 'failed', 'something went wrong', 'already said', 'try again', 'could not be sent', 'limit'
                ];
                const okKeywords = [
                    '게시물이 전송', '게시되었습니다', '트윗이 전송', '전송되었습니다',
                    'post was sent', 'tweet was sent', 'your post was sent'
                ];
                let errorToast = '';
                let successToast = '';
                const toasts = document.querySelectorAll('[data-testid="toast"], [role="alert"]');
                for (const el of toasts) {
                    if (!isVis(el)) continue;
                    const txt = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!txt) continue;
                    const low = txt.toLowerCase();
                    if (!errorToast && errKeywords.some(k => low.includes(k))) {
                        errorToast = txt.slice(0, 180);
                    }
                    if (!successToast && okKeywords.some(k => low.includes(k))) {
                        successToast = txt.slice(0, 180);
                    }
                }
                const modal = Array.from(document.querySelectorAll('div[aria-modal="true"], div[role="dialog"]')).find(isVis);
                const modalHasComposer = Boolean(
                    modal && (
                        Array.from(modal.querySelectorAll('[data-testid="tweetTextarea_0"], [data-testid="tweetButton"]')).some(isVis)
                    )
                );
                const anyTweetBtnVisible = Array.from(document.querySelectorAll('button[data-testid="tweetButton"]')).some(isVis);
                return {
                    error_toast: errorToast,
                    success_toast: successToast,
                    modal_has_composer: modalHasComposer,
                    any_tweet_btn_visible: anyTweetBtnVisible,
                };
            }"""
        )
        if isinstance(res, dict):
            return res
    except Exception as e:
        if is_target_closed_error(e):
            raise
    return {
        "error_toast": "",
        "success_toast": "",
        "modal_has_composer": True,
        "any_tweet_btn_visible": True,
    }


def _wait_x_publish_complete(page, timeout_sec=40.0, stop_checker=None):
    """[게시] 클릭 후 /compose/post 작성창 닫힘·URL 전환 또는 게시 완료 토스트를 반드시 검증.
    작성 화면(/compose/post)에 그대로 남아 있거나 실패 알림이 뜨면 RuntimeError를 발생시켜 거짓 성공을 원천 차단."""
    start_ts = time.time()
    closed_ticks = 0

    while (time.time() - start_ts) < timeout_sec:
        if _check_stopped(stop_checker):
            raise InterruptedError("엑스(X) 게시 완료 확인 대기 중 사용자 중지 요청")

        fb = _inspect_x_publish_feedback(page)
        if fb.get("error_toast"):
            raise RuntimeError(f"엑스(X) 게시 실패 알림 감지: {fb['error_toast']}")

        if fb.get("success_toast"):
            print(f"✅ [엑스(X)] 게시 완료 토스트 확인: {fb['success_toast']}", flush=True)
            time.sleep(1.0)
            return True

        cur_u = (page.url or "").lower()
        on_compose_url = ("/compose/post" in cur_u) or ("/compose/tweet" in cur_u)
        modal_open = bool(fb.get("modal_has_composer"))
        btn_open = bool(fb.get("any_tweet_btn_visible"))

        if (not on_compose_url) and (not modal_open):
            closed_ticks += 1
            if closed_ticks >= 2:
                print(f"✅ [엑스(X)] 작성 화면 종료 및 페이지 전환 확인({page.url}) — 게시 완료!", flush=True)
                return True
        elif (not modal_open) and (not btn_open):
            closed_ticks += 1
            if closed_ticks >= 2:
                print("✅ [엑스(X)] 작성 모달 닫힘 확인 — 게시 완료!", flush=True)
                return True
        else:
            closed_ticks = 0

        time.sleep(0.6)

    final_url = page.url or ""
    raise RuntimeError(
        f"엑스(X) 게시 버튼 클릭 후 {int(timeout_sec)}초가 지나도 작성 화면({final_url})이 닫히지 않고 "
        "게시 완료 토스트도 확인되지 않아 실패로 판정했습니다."
    )

def upload_to_x(body="", tags=None, video_path=None, login_only=False, stop_checker=None, test_mode=False):
    if not login_only and (not video_path or not os.path.exists(video_path)):
        print("⚠️ [엑스(X)] 업로드할 영상 파일이 없습니다.", flush=True)
        return False
    clean_body = re.sub(r"#[^\s#]+", "", (body or "")).strip()
    raw_list = [t.replace("#", "").strip() for t in (tags or []) if t.replace("#", "").strip()]
    tags_2 = list(dict.fromkeys(raw_list))[:2]
    tag_str = " ".join([f"#{t}" for t in tags_2])
    max_body_len = max(30, 135 - len(tag_str))
    trimmed_body = truncate_clean_text(clean_body, max_body_len)
    full_post = f"{trimmed_body}{nl}{nl}{tag_str}".strip() if tag_str else trimmed_body

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

            if _check_stopped(stop_checker):
                return False
            if "compose" not in (page.url or "").lower():
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3.0)

            print("📎 [엑스(X)] 영상 파일 첨부 중...", flush=True)
            f_in = page.locator('input[data-testid="fileInput"], input[type="file"][accept*="video"], input[type="file"]').first
            f_in.wait_for(state="attached", timeout=20000)
            f_in.set_input_files(video_path)
            time.sleep(1.5)

            print("📝 [엑스(X)] 본문 및 해시태그 입력 중...", flush=True)
            tbox = page.locator('div[data-testid="tweetTextarea_0"], div[role="textbox"]').first
            tbox.wait_for(state="visible", timeout=15000)
            tbox.click(force=True)
            time.sleep(0.4)
            copy_to_clipboard(full_post)
            page.keyboard.press(f"{MOD_KEY}+v")
            time.sleep(1.2)

            print("🚀 [엑스(X)] 영상 업로드·인코딩 완료 및 [게시] 버튼 활성화 대기 중 (최대 120초)...", flush=True)
            post_btn = _wait_x_media_and_post_btn_ready(page, timeout_sec=120.0, stop_checker=stop_checker)

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

            if _check_stopped(stop_checker):
                return False
            if not _is_x_post_btn_enabled(post_btn) or _get_x_upload_state(page).get("is_uploading"):
                post_btn = _wait_x_media_and_post_btn_ready(page, timeout_sec=25.0, stop_checker=stop_checker)

            try:
                post_btn.scroll_into_view_if_needed()
                post_btn.click(timeout=5000)
            except Exception as click_err:
                if is_target_closed_error(click_err):
                    raise
                if not _is_x_post_btn_enabled(post_btn):
                    raise RuntimeError("엑스(X) [게시] 버튼이 비활성 상태여서 클릭하지 않았습니다.") from click_err
                post_btn.click(force=True)

            print("⏳ [엑스(X)] 실제 게시 완료 확인 대기 중 (최대 40초)...", flush=True)
            _wait_x_publish_complete(page, timeout_sec=40.0, stop_checker=stop_checker)
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
