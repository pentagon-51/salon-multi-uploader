import os
import re
import sys
import json
import time
import queue
import shutil
import zipfile
import tempfile
import threading
import py_compile
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_JSON_FILE = os.path.join(BASE_DIR, "version.json")
INSTALLED_MARKER = os.path.join(BASE_DIR, ".installed_done")

PUBLIC_REPO_URL = "https://github.com/pentagon-51/salon-multi-uploader.git"

DEFAULT_RELEASE_INFO = {
    "version": "1.0.0",
    "updated_at": "",
    "changelog": "",
    "author": "",
    "repo_url": PUBLIC_REPO_URL,
    "notion_url": "#",
    "youtube_url": "#",
    "chat_url": "#",
    "instagram_url": "#",
}

ALLOWED_EXTENSIONS = {".py", ".html", ".bat", ".ico"}
ALLOWED_EXACT_NAMES = {
    "version.json",
    "LICENSE.txt",
    ".gitignore",
    "salon_uploader.ico",
    "favicon.png",
    "1_맥북_최초설치.command",
    "2_맥북_프로그램_실행.command",
}
INSTALL_ONLY_FILES = {"0_처음사용자_안내서.html", "1_최초설치_자동인스톨.bat"}
EXCLUDED_DEV_FILES = {
    "export_code.py",
    "total_code.txt",
    "check_personal_leak.py",
    "build_release.py",
    "배포판_만들기_및_업로드.command",
    "AI_RULES.md",
    "salon_uploader_icon.png",
    "planh_uploader.ico",
}
PROTECTED_EXACT_NAMES = {
    "user_profile.json",
    "daily_usage.json",
    "login_credentials.json",
    "updater_config.json",
    "client_secret.json",
    "youtube_token.json",
    "token.pickle",
    "token.json",
    "gemini_api_key.txt",
    ".env",
    "app.log",
    ".installed_done",
    ".DS_Store",
}
PROTECTED_DIRS = {
    "auth_data",
    "chrome_profile",
    "browser_profile",
    "user_data",
    "playwright_profile",
    "cookies",
    "sessions",
    "blackbox_login",
    "blackbox_upload",
    "blackbox",
    "upload",
    "done",
    "debug_records",
    "temp_thumbs",
    "_old_backup",
    "_설치완료_보관함",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
}
PROTECTED_MEDIA_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".gif", ".png", ".jpg", ".jpeg", ".log", ".pickle", ".lnk", ".command",
}


def parse_version(v_str):
    parts = []
    for tok in (v_str or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in tok if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer_version(remote_v, local_v):
    r_clean = (remote_v or "").strip().lstrip("vV")
    l_clean = (local_v or "").strip().lstrip("vV")
    if not r_clean or r_clean == l_clean:
        return False
    return parse_version(r_clean) > parse_version(l_clean)


def parse_github_owner_repo(repo_url=None):
    url = (repo_url or PUBLIC_REPO_URL).strip()
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "pentagon-51", "salon-multi-uploader"


def load_release_info():
    """로컬 version.json을 읽어 버전·공식 채널 링크 정보 반환 (파일 부재/손상 시 안전한 기본값)"""
    info = dict(DEFAULT_RELEASE_INFO)
    if os.path.exists(VERSION_JSON_FILE):
        try:
            with open(VERSION_JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_RELEASE_INFO:
                    if k in data and data[k] is not None:
                        info[k] = str(data[k]).strip()
        except Exception:
            pass
    if not info.get("version"):
        info["version"] = "1.0.0"
    if not info.get("repo_url"):
        info["repo_url"] = PUBLIC_REPO_URL
    return info


def read_local_version():
    return load_release_info().get("version", "1.0.0")


def fetch_remote_release_info(repo_url=None, branch="main", timeout=4):
    """퍼블릭 GitHub main 브랜치의 raw version.json을 조회 (실패 시 예외 발생 -> 호출부에서 안전 처리)"""
    owner, repo = parse_github_owner_repo(repo_url)
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/version.json?t={int(time.time())}"
    req = urllib.request.Request(
        raw_url,
        headers={
            "User-Agent": "Salon-Multi-Uploader-Updater",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_text = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw_text)
    if not isinstance(data, dict) or not str(data.get("version") or "").strip():
        raise ValueError("원격 version.json 형식이 올바르지 않습니다.")
    return data


def check_public_update(branch="main", timeout=4):
    """원격 version.json과 로컬 version.json 비교 (오프라인/빈 저장소에서도 절대 앱 중단 없음)"""
    local_info = load_release_info()
    local_v = local_info.get("version", "1.0.0")
    repo_url = local_info.get("repo_url") or PUBLIC_REPO_URL
    try:
        remote_info = fetch_remote_release_info(repo_url=repo_url, branch=branch, timeout=timeout)
        remote_v = str(remote_info.get("version") or "").strip().lstrip("vV")
        has_update = is_newer_version(remote_v, local_v)
        return {
            "ok": True,
            "has_update": has_update,
            "local_version": local_v,
            "remote_version": remote_v,
            "changelog": str(remote_info.get("changelog") or "").strip(),
            "updated_at": str(remote_info.get("updated_at") or "").strip(),
            "remote_info": remote_info,
            "local_info": local_info,
        }
    except Exception as e:
        return {
            "ok": False,
            "has_update": False,
            "local_version": local_v,
            "remote_version": "",
            "changelog": "",
            "updated_at": "",
            "local_info": local_info,
            "error": str(e),
        }


def is_allowed_code_file(rel_path, skip_install_files=False):
    """업데이트 시 덮어쓸 수 있는 안전한 순수 코드·배포 파일만 허용 (개인 데이터·프로필 100% 보호)"""
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm or norm.endswith("/") or ".." in norm.split("/"):
        return False
    parts = norm.split("/")
    # 보호 폴더(개인 데이터, 크롬 프로필, 미디어, .git 등) 하위 파일 일체 차단
    if any(p in PROTECTED_DIRS or p.lower() in PROTECTED_DIRS for p in parts[:-1]):
        return False
    fname = parts[-1]
    low_fname = fname.lower()
    if (
        fname in EXCLUDED_DEV_FILES
        or fname in PROTECTED_EXACT_NAMES
        or low_fname in PROTECTED_EXACT_NAMES
        or low_fname.endswith("_cookies.json")
        or low_fname.startswith(".env")
        or (low_fname.startswith("total_code") and low_fname.endswith(".txt"))
    ):
        return False
    if skip_install_files and fname in INSTALL_ONLY_FILES:
        return False
    if fname in ALLOWED_EXACT_NAMES:
        return True
    _, ext = os.path.splitext(low_fname)
    if ext in PROTECTED_MEDIA_EXTS:
        return False
    return ext in ALLOWED_EXTENSIONS


def apply_public_update(branch="main", report=None, target_dir=None, timeout=30):
    """GitHub main 브랜치 ZIP을 임시 폴더에 받아 문법 검사 후 코드 파일만 안전 덮어쓰기 (개인 데이터 100% 보호)"""
    def _report(pct, msg):
        if callable(report):
            try:
                report(pct, msg)
            except Exception:
                pass

    dest_base = target_dir or BASE_DIR
    local_info = load_release_info()
    owner, repo = parse_github_owner_repo(local_info.get("repo_url") or PUBLIC_REPO_URL)
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"

    tmp_dir = tempfile.mkdtemp(prefix="salon_update_")
    try:
        _report(15, "최신 버전 패키지 다운로드 준비 중...")
        req = urllib.request.Request(
            zip_url,
            headers={
                "User-Agent": "Salon-Multi-Uploader-Updater",
                "Cache-Control": "no-cache",
            },
        )
        zip_path = os.path.join(tmp_dir, "release_main.zip")
        _report(25, "GitHub에서 최신 코드 ZIP 다운로드 중...")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total_len = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as out_f:
                while True:
                    chunk = resp.read(32768)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_len > 0:
                        pct = 25 + int(min(1.0, downloaded / total_len) * 35)
                    else:
                        pct = min(60, 25 + int((downloaded / (1024 * 1024)) * 20))
                    _report(pct, f"최신 버전 다운로드 중... ({downloaded // 1024} KB)")

        _report(65, "개인 데이터 보호 필터 적용 및 순수 코드 추출 중...")
        extract_root = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_root, exist_ok=True)
        extracted_files = []
        skip_install_files = os.path.exists(os.path.join(dest_base, ".installed_done"))
        new_version_from_zip = ""

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                parts = member.filename.replace("\\", "/").split("/")
                if len(parts) < 2:
                    continue
                rel_path = "/".join(parts[1:])
                if not is_allowed_code_file(rel_path, skip_install_files=skip_install_files):
                    continue
                dst_path = os.path.join(extract_root, *rel_path.split("/"))
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                with zf.open(member, "r") as src_f, open(dst_path, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                extracted_files.append((rel_path, dst_path))
                if rel_path == "version.json":
                    try:
                        with open(dst_path, "r", encoding="utf-8") as vf:
                            vj = json.load(vf)
                        new_version_from_zip = str(vj.get("version") or "").strip()
                    except Exception:
                        pass

        if not extracted_files:
            return {"ok": False, "msg": "다운로드된 패키지에서 반영할 코드 파일을 찾지 못했습니다."}

        py_files = [dst for _, dst in extracted_files if dst.lower().endswith(".py")]
        _report(80, f"파이썬 코드 문법 안전 검사 중 ({len(py_files)}개)...")
        for py_path in py_files:
            py_compile.compile(py_path, doraise=True)

        _report(92, "검증 완료! 개인 데이터를 유지한 채 코드 파일 반영 중...")
        updated_count = 0
        for rel_path, src_path in extracted_files:
            final_dst = os.path.join(dest_base, *rel_path.split("/"))
            os.makedirs(os.path.dirname(final_dst), exist_ok=True)
            try:
                with open(src_path, "rb") as sf:
                    new_bytes = sf.read()
                if os.path.exists(final_dst):
                    try:
                        with open(final_dst, "rb") as df:
                            if df.read() == new_bytes:
                                continue
                    except Exception:
                        pass
                if final_dst.lower().endswith(".bat"):
                    tmp_new = final_dst + ".new"
                    try:
                        with open(tmp_new, "wb") as nf:
                            nf.write(new_bytes)
                        os.replace(tmp_new, final_dst)
                        updated_count += 1
                    except PermissionError:
                        try:
                            if os.path.exists(tmp_new):
                                os.remove(tmp_new)
                        except Exception:
                            pass
                        continue
                else:
                    shutil.copy2(src_path, final_dst)
                    if final_dst.lower().endswith(".command"):
                        try:
                            os.chmod(final_dst, 0o755)
                        except Exception:
                            pass
                    updated_count += 1
            except PermissionError:
                continue

        final_ver = new_version_from_zip or read_local_version()
        _report(100, f"업데이트 완료! (v{final_ver})")
        return {
            "ok": True,
            "new_version": final_ver,
            "updated_count": updated_count,
            "total_files": len(extracted_files),
            "msg": f"v{final_ver} 업데이트 완료! 프로그램을 다시 실행해 주세요. (Mac: 2_맥북_프로그램_실행.command / Windows: 2_프로그램_실행.bat)",
        }
    except Exception as e:
        return {"ok": False, "msg": f"업데이트 중 오류가 발생했습니다 (기존 버전은 안전하게 유지됨): {e}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_with_tk_popup(remote_version):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("미용실 멀티업로더 자동 업데이트")
    root.resizable(False, False)

    win_w, win_h = 420, 150
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    pos_x = max(0, (screen_w - win_w) // 2)
    pos_y = max(0, (screen_h - win_h) // 2)
    root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)

    title_lbl = ttk.Label(frame, text="미용실 멀티업로더 자동 업데이트", font=("Malgun Gothic", 11, "bold"))
    title_lbl.pack(anchor="w", pady=(0, 6))

    status_var = tk.StringVar(value=f"새 버전 확인 완료 (v{remote_version}) — 다운로드 준비 중")
    status_lbl = ttk.Label(frame, textvariable=status_var, font=("Malgun Gothic", 9))
    status_lbl.pack(anchor="w", pady=(0, 10))

    bar = ttk.Progressbar(frame, orient="horizontal", length=380, mode="determinate", maximum=100)
    bar.pack(fill="x", pady=(0, 6))
    bar["value"] = 15

    pct_var = tk.StringVar(value="15%")
    pct_lbl = ttk.Label(frame, textvariable=pct_var, font=("Malgun Gothic", 9, "bold"))
    pct_lbl.pack(anchor="e")

    msg_queue = queue.Queue()

    def report(pct, text):
        msg_queue.put(("progress", pct, text))

    def worker():
        apply_public_update(report=report)
        time.sleep(0.5)
        msg_queue.put(("done", 100, ""))

    def poll():
        try:
            while True:
                kind, pct, text = msg_queue.get_nowait()
                if kind == "progress":
                    bar["value"] = pct
                    pct_var.set(f"{pct}%")
                    status_var.set(text)
                elif kind == "done":
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(50, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(50, poll)
    root.mainloop()


def run_with_console_fallback(remote_version):
    def report(pct, text):
        print(f"[자동 업데이트 {pct}%] {text}", flush=True)

    apply_public_update(report=report)


def main():
    # 마스터 개발 PC(.git 폴더 존재) 보호: 시작 시 자동 덮어쓰기 방지
    if os.path.exists(os.path.join(BASE_DIR, ".git")):
        sys.exit(0)

    res = check_public_update(timeout=3)
    if not res.get("ok") or not res.get("has_update"):
        sys.exit(0)

    remote_version = res.get("remote_version") or ""
    try:
        run_with_tk_popup(remote_version)
    except Exception:
        run_with_console_fallback(remote_version)


if __name__ == "__main__":
    main()
