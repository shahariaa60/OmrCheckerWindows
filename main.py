"""Byabodhan OMR Scanner v2.0 (Windows)
Modern PyWebView Desktop UI + Instant Splash Screen + Local Preview Server
+ Offline Streaming OpenCV/PyMuPDF Engine + Firebase REST + Multi-Tab Batch Sheet Sync."""

import csv
import json
import os
import sys
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List

try:
    import pyi_splash
    pyi_splash.update_text("Loading Byabodhan OMR Scanner...")
except Exception:
    pyi_splash = None

import webview

from cloud_client import (
    DEFAULT_GAS_URL,
    batch_sync_google_sheet,
    fetch_firestore_bundle,
    parse_offline_json,
)
from omr_engine import (
    count_total_pages,
    export_marked_pdf,
    process_image_to_disk,
    stream_file_pages,
)


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ByabodhanOMR_v2"
    base.mkdir(parents=True, exist_ok=True)
    return base


class LocalAssetHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, ui_dir: Path, assets_dir: Path, session_dir: Path, **kwargs):
        self._ui_dir = ui_dir
        self._assets_dir = assets_dir
        self._session_dir = session_dir
        super().__init__(*args, directory=str(ui_dir), **kwargs)

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0]
        if clean.startswith("/assets/"):
            return str(self._assets_dir / clean[len("/assets/"):])
        if clean.startswith("/preview/"):
            return str(self._session_dir / clean[len("/preview/"):])
        if clean in ("", "/"):
            return str(self._ui_dir / "index.html")
        return str(self._ui_dir / clean.lstrip("/"))

    def log_message(self, format, *args):
        pass  # keep console silent


class OmrDesktopApi:
    def __init__(self):
        self.window = None
        self.data_dir = app_data_dir()
        self.cache_path = self.data_dir / "firestore_cache.json"
        self.cfg_path = self.data_dir / "settings.json"
        self.session_dir = self.data_dir / "sessions" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.settings = self._load_settings()
        self.bundle = {"classes": [], "exam_types": [], "exams": [], "source": "offline"}
        self.active_exam = {
            "title": "অফলাইন পরীক্ষা",
            "totalQuestions": 50,
            "positiveMark": 1.0,
            "negativeMark": 0.0,
            "answerKeys": {},
        }
        self.results: List[dict] = []
        self.stop_flag = False
        self.scanning = False

    def _load_settings(self) -> dict:
        if self.cfg_path.exists():
            try:
                return json.loads(self.cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"gas_url": DEFAULT_GAS_URL, "auto_sync": True, "sheet_tab": "", "sheet_col": ""}

    def _persist_settings(self):
        try:
            self.cfg_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _eval_js(self, code: str):
        if self.window:
            try:
                self.window.evaluate_js(code)
            except Exception:
                pass

    def get_initial_state(self) -> dict:
        if self.cache_path.exists():
            try:
                self.bundle = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self.bundle["source"] = "cache"
            except Exception:
                pass
        return {"settings": self.settings, "bundle": self.bundle}

    def refresh_firebase(self) -> dict:
        self.bundle = fetch_firestore_bundle(self.cache_path)
        return self.bundle

    def set_active_exam(self, exam: dict) -> dict:
        self.active_exam = dict(exam)
        return {"ok": True}

    def save_settings(self, cfg: dict) -> dict:
        self.settings["gas_url"] = cfg.get("gas_url", DEFAULT_GAS_URL)
        self.settings["auto_sync"] = bool(cfg.get("auto_sync", True))
        self.settings["sheet_tab"] = cfg.get("sheet_tab", "")
        self.settings["sheet_col"] = cfg.get("sheet_col", "")
        self.active_exam["totalQuestions"] = max(1, min(50, int(cfg.get("total_q", 50))))
        self.active_exam["positiveMark"] = float(cfg.get("pos", 1.0))
        self.active_exam["negativeMark"] = float(cfg.get("neg", 0.0))
        self._persist_settings()
        return {"ok": True}

    def pick_offline_json(self) -> dict:
        res = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("JSON Files (*.json)", "All files (*.*)"),
        )
        if not res:
            return {"cancelled": True}
        path = res[0] if isinstance(res, (list, tuple)) else res
        try:
            exam = parse_offline_json(Path(path).read_text(encoding="utf-8"))
            self.active_exam = exam
            return {"exam": exam}
        except Exception as err:
            return {"error": f"JSON সঠিক নয়: {err}"}

    def start_scan_files(self) -> dict:
        if self.scanning:
            return {"error": "আগের স্ক্যান এখনো চলছে"}
        res = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=("OMR Files (*.jpg;*.jpeg;*.png;*.pdf)", "All files (*.*)"),
        )
        if not res:
            return {"cancelled": True}
        paths = [Path(p) for p in (res if isinstance(res, (list, tuple)) else [res])]
        self._launch_scan(paths)
        return {"ok": True}

    def start_scan_folder(self) -> dict:
        if self.scanning:
            return {"error": "আগের স্ক্যান এখনো চলছে"}
        res = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return {"cancelled": True}
        folder = Path(res[0] if isinstance(res, (list, tuple)) else res)
        exts = {".jpg", ".jpeg", ".png", ".pdf"}
        paths = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts)
        if not paths:
            return {"error": "এই ফোল্ডারে কোনো JPG, PNG বা PDF পাওয়া যায়নি"}
        self._launch_scan(paths)
        return {"ok": True}

    def stop_scan(self):
        self.stop_flag = True

    def _autosave(self):
        try:
            (self.session_dir / "results.json").write_text(
                json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _launch_scan(self, file_paths: List[Path]):
        self.scanning = True
        self.stop_flag = False
        keys = self.active_exam.get("answerKeys", {})
        total_q = int(self.active_exam.get("totalQuestions", 50))
        pos = float(self.active_exam.get("positiveMark", 1.0))
        neg = float(self.active_exam.get("negativeMark", 0.0))

        def worker():
            total_pages = count_total_pages(file_paths)
            done = 0
            pending_batch = []

            for fp in file_paths:
                if self.stop_flag:
                    break
                try:
                    for page_num, bgr in stream_file_pages(fp):
                        if self.stop_flag:
                            break
                        done += 1
                        self._eval_js(f"window.onScanProgress({done}, {total_pages}, {json.dumps(fp.name)})")
                        fname = f"sheet_{int(time.time() * 1000)}_{done}.jpg"
                        prev_path = self.session_dir / fname
                        try:
                            r = process_image_to_disk(bgr, keys, total_q, pos, neg, prev_path)
                            r["file"] = fp.name
                            r["page"] = page_num
                            r["preview_url"] = f"/preview/{fname}"
                        except Exception as e:
                            r = {
                                "file": fp.name, "page": page_num, "roll": "", "roll_valid": False,
                                "set_code": "", "set_latin": "-", "correct": 0, "wrong": 0, "unanswered": 0,
                                "total_marks": None, "max_marks": total_q * pos, "scored": False,
                                "warnings": [str(e)], "preview_path": "", "preview_url": "",
                                "sync": "error", "student_name": "", "sync_message": str(e),
                            }
                        idx = len(self.results)
                        self.results.append(r)
                        self._autosave()
                        self._eval_js(f"window.onResultAdded({idx}, {json.dumps(r, ensure_ascii=False)})")

                        if self.settings.get("auto_sync") and r.get("roll_valid") and r.get("scored"):
                            pending_batch.append(idx)
                            if len(pending_batch) >= 25:
                                self._sync_indices(pending_batch)
                                pending_batch = []
                except Exception as err:
                    idx = len(self.results)
                    r = {
                        "file": fp.name, "page": 1, "roll": "", "roll_valid": False, "set_code": "", "set_latin": "-",
                        "correct": 0, "wrong": 0, "unanswered": 0, "total_marks": None, "max_marks": total_q * pos,
                        "scored": False, "warnings": [str(err)], "preview_path": "", "preview_url": "",
                        "sync": "error", "student_name": "", "sync_message": str(err),
                    }
                    self.results.append(r)
                    self._eval_js(f"window.onResultAdded({idx}, {json.dumps(r, ensure_ascii=False)})")

            if pending_batch and not self.stop_flag:
                self._sync_indices(pending_batch)

            self.scanning = False
            self._eval_js(f"window.onScanDone({json.dumps(f'সম্পন্ন — মোট {done}টি খাতা প্রসেস হয়েছে')})")

        threading.Thread(target=worker, daemon=True).start()

    def _sync_indices(self, indices: List[int]):
        gas_url = self.settings.get("gas_url", "").strip()
        cls_tab = self.settings.get("sheet_tab", "").strip()
        subj_col = self.settings.get("sheet_col", "").strip()
        if not gas_url or not subj_col:
            return
        items = [
            {"roll": self.results[i]["roll"], "marks": self.results[i]["total_marks"]}
            for i in indices
            if i < len(self.results) and self.results[i].get("scored") and self.results[i].get("total_marks") is not None
        ]
        if not items:
            return
        try:
            outcomes = batch_sync_google_sheet(gas_url, cls_tab, subj_col, items)
            for i in indices:
                if i >= len(self.results):
                    continue
                r = self.results[i]
                o = outcomes.get(str(r["roll"]))
                if o:
                    r["sync"] = o["status"]
                    r["student_name"] = o.get("student_name", "")
                    r["sync_message"] = o.get("message", "")
                    self._eval_js(f"window.onResultUpdated({i}, {json.dumps(r, ensure_ascii=False)})")
            self._autosave()
        except Exception as err:
            for i in indices:
                if i < len(self.results):
                    self.results[i]["sync"] = "error"
                    self.results[i]["sync_message"] = str(err)[:80]
                    self._eval_js(f"window.onResultUpdated({i}, {json.dumps(self.results[i], ensure_ascii=False)})")

    def sync_all_pending(self) -> dict:
        if not self.settings.get("sheet_col", "").strip():
            return {"error": "Sheet Sync ট্যাবে কলামের নাম (যেমন: Bangla) দিন"}
        pending = [
            i for i, r in enumerate(self.results)
            if r.get("scored") and r.get("total_marks") is not None and r.get("sync") != "synced" and str(r.get("roll", "")).isdigit()
        ]
        if not pending:
            return {"message": "সিঙ্ক করার মতো কোনো পেন্ডিং খাতা নেই"}
        threading.Thread(target=lambda: self._sync_indices(pending), daemon=True).start()
        return {"message": f"{len(pending)}টি খাতা Google Sheet-এ ব্যাচ সিঙ্ক হচ্ছে..."}

    def resync_single(self, idx: int, new_roll: str) -> dict:
        if idx < 0 or idx >= len(self.results):
            return {"error": "খাতা পাওয়া যায়নি"}
        if not new_roll.isdigit():
            return {"error": "রোল নম্বর শুধু সংখ্যায় দিন"}
        self.results[idx]["roll"] = new_roll
        self.results[idx]["roll_valid"] = len(new_roll) == 6
        self.results[idx]["sync"] = "pending"
        self._sync_indices([idx])
        return {"ok": True}

    def delete_result(self, idx: int) -> dict:
        if 0 <= idx < len(self.results):
            self.results.pop(idx)
            self._autosave()
        return {"ok": True}

    def clear_results(self) -> dict:
        self.results.clear()
        self._autosave()
        return {"ok": True}

    def export_pdf(self, indices: List[int]) -> dict:
        previews = [self.results[i].get("preview_path", "") for i in indices if 0 <= i < len(self.results)]
        if not previews:
            return {"error": "কোনো মার্কড প্রিভিউ পাওয়া যায়নি"}
        res = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"Byabodhan_Marked_OMR_{len(previews)}.pdf",
            file_types=("PDF Files (*.pdf)",),
        )
        if not res:
            return {"cancelled": True}
        out = Path(res[0] if isinstance(res, (list, tuple)) else res)
        if out.suffix.lower() != ".pdf":
            out = out.with_suffix(".pdf")
        try:
            cnt = export_marked_pdf(previews, out)
            return {"saved": True, "count": cnt}
        except Exception as err:
            return {"error": str(err)}

    def export_csv(self) -> dict:
        if not self.results:
            return {"error": "কোনো ফলাফল নেই"}
        res = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"OMR_Results_{self.settings.get('sheet_tab') or 'All'}.csv",
            file_types=("CSV Files (*.csv)",),
        )
        if not res:
            return {"cancelled": True}
        out = Path(res[0] if isinstance(res, (list, tuple)) else res)
        if out.suffix.lower() != ".csv":
            out = out.with_suffix(".csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["#", "File", "Page", "Roll", "Set", "Correct", "Wrong", "Unanswered", "Marks", "Sync", "Student"])
            for i, r in enumerate(self.results, 1):
                w.writerow([
                    i, r.get("file", ""), r.get("page", 1), r.get("roll", ""),
                    r.get("set_latin", ""), r.get("correct", 0), r.get("wrong", 0),
                    r.get("unanswered", 0), r.get("total_marks", ""), r.get("sync", ""), r.get("student_name", ""),
                ])
        return {"saved": True}


def main():
    api = OmrDesktopApi()
    handler = partial(
        LocalAssetHandler,
        ui_dir=resource_path("ui"),
        assets_dir=resource_path("assets"),
        session_dir=api.session_dir,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    window = webview.create_window(
        "Byabodhan OMR — OMR Exam Scanner",
        url=f"http://127.0.0.1:{port}/",
        js_api=api,
        width=1260,
        height=780,
        min_size=(1020, 660),
        background_color="#EEF1F6",
    )
    api.window = window

    def on_loaded():
        if pyi_splash:
            try:
                pyi_splash.close()
            except Exception:
                pass

    window.events.loaded += on_loaded
    webview.start(debug=False)
    server.shutdown()


if __name__ == "__main__":
    main()
