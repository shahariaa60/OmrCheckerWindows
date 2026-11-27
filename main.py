"""ব্যবধান শিক্ষালয় — OMR Scanner Windows Desktop App
Features:
- Coaching Logo header + Bangla UI
- Firebase Firestore Exam Selector (with local offline cache) + Offline JSON Upload
- Crash-proof streaming batch scan (unlimited JPG/PNG/Multi-page PDF/Folders)
- Multi-tab per Class Batch Google Sheet Sync (Code.gs)
- Interactive Results Table with Row Checkboxes
- Full-size Marked Sheet Preview + Roll Edit & Re-Sync
- Export ALL or SELECTED Checked Sheets as a single Marked PDF (exact preview look) + CSV Export
"""

import csv
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

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

BRAND_RED = "#C62828"
BG_LIGHT = "#F5F6F8"


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def app_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ByabodhanOMR_Windows"
    base.mkdir(parents=True, exist_ok=True)
    return base


class ByabodhanWindowsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ব্যবধান শিক্ষালয় — OMR Scanner (Windows)")
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(bg=BG_LIGHT)

        ico = resource_path("assets/logo.ico")
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

        self.data_dir = app_data_dir()
        self.cache_path = self.data_dir / "firestore_cache.json"
        self.cfg_path = self.data_dir / "settings.json"
        self.session_dir = self.data_dir / "sessions" / datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.settings = self._load_settings()
        self.fb_bundle = {"classes": [], "exam_types": [], "exams": []}
        self.filtered_exams = []
        self.active_exam = {
            "title": "উত্তরমালা নির্বাচন করা হয়নি",
            "className": "",
            "examType": "",
            "subject": "",
            "totalQuestions": 50,
            "positiveMark": 1.0,
            "negativeMark": 0.0,
            "answerKeys": {},
        }
        self.results = []
        self.checked_indices = set()
        self.stop_flag = False
        self.scanning = False

        self._setup_style()
        self._build_ui()
        self.refresh_firestore_async()

    def _load_settings(self) -> dict:
        if self.cfg_path.exists():
            try:
                return json.loads(self.cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"gas_url": DEFAULT_GAS_URL, "auto_sync": True}

    def _save_settings(self):
        self.settings["gas_url"] = self.var_gas_url.get().strip()
        self.settings["auto_sync"] = bool(self.var_auto_sync.get())
        try:
            self.cfg_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG_LIGHT)
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("TLabel", background=BG_LIGHT, font=("Nirmala UI", 10))
        style.configure("Card.TLabel", background="#FFFFFF", font=("Nirmala UI", 10))
        style.configure("Title.TLabel", background="#FFFFFF", foreground=BRAND_RED, font=("Nirmala UI", 14, "bold"))
        style.configure("Sub.TLabel", background="#FFFFFF", foreground="#555555", font=("Nirmala UI", 9))
        style.configure("Accent.TButton", background=BRAND_RED, foreground="#FFFFFF", font=("Nirmala UI", 10, "bold"), padding=(12, 6))
        style.map("Accent.TButton", background=[("active", "#A11B1B")])
        style.configure("TButton", font=("Nirmala UI", 9), padding=(10, 5))
        style.configure("Treeview", font=("Nirmala UI", 10), rowheight=28)
        style.configure("Treeview.Heading", font=("Nirmala UI", 10, "bold"))

    def _build_ui(self):
        # ---------------- Top Header with Coaching Logo ----------------
        header = ttk.Frame(self, style="Card.TFrame", padding=(16, 10))
        header.pack(fill="x", side="top")

        logo_file = resource_path("assets/logo.jpeg")
        if logo_file.exists():
            im = Image.open(logo_file).convert("RGB")
            im.thumbnail((170, 56), Image.Resampling.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(im)
            tk.Label(header, image=self._logo_img, bg="#FFFFFF").pack(side="left", padx=(0, 14))

        title_box = ttk.Frame(header, style="Card.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="ব্যবধান শিক্ষালয় — OMR স্ক্যানার (Windows)", style="Title.TLabel").pack(anchor="w")
        self.lbl_status_top = ttk.Label(title_box, text="Firebase থেকে পরীক্ষার তালিকা লোড হচ্ছে...", style="Sub.TLabel")
        self.lbl_status_top.pack(anchor="w")

        ttk.Button(header, text="সার্ভার রিফ্রেশ", command=self.refresh_firestore_async).pack(side="right", padx=4)

        # ---------------- Setup & Sync Panel ----------------
        top_pane = ttk.Frame(self, padding=(12, 8))
        top_pane.pack(fill="x")

        # Left Card: Exam Selection (Firebase + Offline JSON)
        exam_card = ttk.Frame(top_pane, style="Card.TFrame", padding=12)
        exam_card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        r1 = ttk.Frame(exam_card, style="Card.TFrame")
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="শ্রেণি:", style="Card.TLabel", width=11).pack(side="left")
        self.var_class = tk.StringVar()
        self.cb_class = ttk.Combobox(r1, textvariable=self.var_class, state="readonly", width=20)
        self.cb_class.pack(side="left", padx=4)
        self.cb_class.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_changed())

        ttk.Label(r1, text="পরীক্ষার ধরন:", style="Card.TLabel").pack(side="left", padx=(10, 4))
        self.var_type = tk.StringVar()
        self.cb_type = ttk.Combobox(r1, textvariable=self.var_type, state="readonly", width=22)
        self.cb_type.pack(side="left", padx=4)
        self.cb_type.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_changed())

        r2 = ttk.Frame(exam_card, style="Card.TFrame")
        r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="পরীক্ষা:", style="Card.TLabel", width=11).pack(side="left")
        self.var_exam = tk.StringVar()
        self.cb_exam = ttk.Combobox(r2, textvariable=self.var_exam, state="readonly", width=36)
        self.cb_exam.pack(side="left", padx=4)
        self.cb_exam.bind("<<ComboboxSelected>>", lambda _e: self._on_exam_selected())

        ttk.Button(r2, text="অফলাইন JSON লোড", command=self.load_offline_json).pack(side="left", padx=6)

        r3 = ttk.Frame(exam_card, style="Card.TFrame")
        r3.pack(fill="x", pady=2)
        ttk.Label(r3, text="মোট প্রশ্ন:", style="Card.TLabel").pack(side="left")
        self.var_total_q = tk.StringVar(value="50")
        ttk.Entry(r3, textvariable=self.var_total_q, width=5).pack(side="left", padx=(4, 10))
        ttk.Label(r3, text="সঠিক (+):", style="Card.TLabel").pack(side="left")
        self.var_pos = tk.StringVar(value="1.0")
        ttk.Entry(r3, textvariable=self.var_pos, width=5).pack(side="left", padx=(4, 10))
        ttk.Label(r3, text="ভুল (-):", style="Card.TLabel").pack(side="left")
        self.var_neg = tk.StringVar(value="0.0")
        ttk.Entry(r3, textvariable=self.var_neg, width=5).pack(side="left", padx=(4, 10))
        self.lbl_sets = ttk.Label(r3, text="সেট: নেই", style="Sub.TLabel")
        self.lbl_sets.pack(side="left", padx=6)

        # Right Card: Multi-Tab Google Sheet Sync Settings
        sheet_card = ttk.Frame(top_pane, style="Card.TFrame", padding=12)
        sheet_card.pack(side="left", fill="both", expand=True, padx=(6, 0))

        sr1 = ttk.Frame(sheet_card, style="Card.TFrame")
        sr1.pack(fill="x", pady=2)
        ttk.Label(sr1, text="Sheet ট্যাব (ক্লাস):", style="Card.TLabel", width=16).pack(side="left")
        self.var_sheet_tab = tk.StringVar()
        ttk.Entry(sr1, textvariable=self.var_sheet_tab, width=18).pack(side="left", padx=4)
        ttk.Label(sr1, text="কলাম নাম:", style="Card.TLabel").pack(side="left", padx=(8, 4))
        self.var_sheet_col = tk.StringVar()
        ttk.Entry(sr1, textvariable=self.var_sheet_col, width=18).pack(side="left", padx=4)

        sr2 = ttk.Frame(sheet_card, style="Card.TFrame")
        sr2.pack(fill="x", pady=4)
        ttk.Label(sr2, text="Apps Script URL:", style="Card.TLabel", width=16).pack(side="left")
        self.var_gas_url = tk.StringVar(value=self.settings.get("gas_url", DEFAULT_GAS_URL))
        ttk.Entry(sr2, textvariable=self.var_gas_url, width=42).pack(side="left", fill="x", expand=True, padx=4)

        sr3 = ttk.Frame(sheet_card, style="Card.TFrame")
        sr3.pack(fill="x", pady=2)
        self.var_auto_sync = tk.BooleanVar(value=self.settings.get("auto_sync", True))
        ttk.Checkbutton(sr3, text="স্ক্যান শেষে অটো Google Sheet সিঙ্ক (ব্যাচ মোড)", variable=self.var_auto_sync).pack(side="left")

        # ---------------- Scan Action Bar ----------------
        bar = ttk.Frame(self, padding=(12, 4))
        bar.pack(fill="x")

        self.btn_pick_files = ttk.Button(bar, text="ছবি / PDF নির্বাচন করে স্ক্যান করুন", style="Accent.TButton", command=self.scan_files)
        self.btn_pick_files.pack(side="left", padx=(0, 6))
        self.btn_pick_folder = ttk.Button(bar, text="পুরো ফোল্ডার স্ক্যান করুন", command=self.scan_folder)
        self.btn_pick_folder.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(bar, text="থামান", command=self.stop_scan, state="disabled")
        self.btn_stop.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self.progress.pack(side="left", padx=12)
        self.lbl_prog = ttk.Label(bar, text="প্রস্তুত (১০০% অফলাইন ইঞ্জিন)")
        self.lbl_prog.pack(side="left")

        # ---------------- Results Table ----------------
        table_wrap = ttk.Frame(self, padding=(12, 4))
        table_wrap.pack(fill="both", expand=True)

        cols = ("chk", "idx", "file", "roll", "set", "ok", "wrong", "blank", "marks", "sync", "warn")
        self.tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="browse")
        headers = [
            ("chk", "☑", 42), ("idx", "#", 42), ("file", "ফাইল / পেজ", 170), ("roll", "রোল", 90),
            ("set", "সেট", 55), ("ok", "সঠিক", 58), ("wrong", "ভুল", 55), ("blank", "ফাঁকা", 58),
            ("marks", "নম্বর", 72), ("sync", "Google Sheet সিঙ্ক", 150), ("warn", "সতর্কতা", 240)
        ]
        for c, txt, w in headers:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="center" if c not in ("file", "warn", "sync") else "w")

        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected_preview())

        # ---------------- Bottom Action Bar (Preview, Marked PDF Export, Batch Sync, CSV) ----------------
        foot = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        foot.pack(fill="x", side="bottom")

        ttk.Button(foot, text="সব সিলেক্ট", command=self.select_all_rows).pack(side="left", padx=3)
        ttk.Button(foot, text="আনসিলেক্ট", command=self.deselect_all_rows).pack(side="left", padx=3)
        ttk.Button(foot, text="প্রিভিউ ও রোল এডিট", command=self.open_selected_preview).pack(side="left", padx=6)

        ttk.Button(foot, text="সিলেক্ট করা খাতার PDF সেভ", style="Accent.TButton", command=lambda: self.export_pdf(selected_only=True)).pack(side="left", padx=4)
        ttk.Button(foot, text="সব খাতার সম্পূর্ণ PDF সেভ", command=lambda: self.export_pdf(selected_only=False)).pack(side="left", padx=4)

        ttk.Button(foot, text="পেন্ডিং Sheet সিঙ্ক", command=self.sync_all_pending_async).pack(side="right", padx=4)
        ttk.Button(foot, text="CSV ডাউনলোড", command=self.export_csv).pack(side="right", padx=4)
        ttk.Button(foot, text="তালিকা পরিষ্কার", command=self.clear_results).pack(side="right", padx=4)

    # ---------------- Firebase & Offline JSON ----------------
    def refresh_firestore_async(self):
        self.lbl_status_top.config(text="Firebase থেকে শ্রেণি ও উত্তরমালা আপডেট হচ্ছে...")
        def worker():
            bundle = fetch_firestore_bundle(self.cache_path)
            self.after(0, lambda: self._apply_firestore_bundle(bundle))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_firestore_bundle(self, bundle: dict):
        self.fb_bundle = bundle
        classes = bundle.get("classes", [])
        types = bundle.get("exam_types", [])
        self.cb_class["values"] = classes
        self.cb_type["values"] = types
        if classes and not self.var_class.get():
            self.var_class.set(classes[0])
        if types and not self.var_type.get():
            self.var_type.set(types[0])
        src = bundle.get("source", "offline")
        if src == "online":
            self.lbl_status_top.config(text=f"Firebase সংযুক্ত — {len(bundle.get('exams', []))}টি পরীক্ষা লোড হয়েছে (অফলাইনেও ক্যাশ সংরক্ষিত)")
        elif src == "cache":
            self.lbl_status_top.config(text="অফলাইন ক্যাশ থেকে পরীক্ষার তালিকা লোড হয়েছে")
        else:
            self.lbl_status_top.config(text="অফলাইন মোড — আপনি অফলাইন JSON লোড করে স্ক্যান করতে পারবেন")
        self._on_filter_changed()

    def _on_filter_changed(self):
        cls = self.var_class.get()
        typ = self.var_type.get()
        if cls:
            self.var_sheet_tab.set(cls)
        self.filtered_exams = [
            e for e in self.fb_bundle.get("exams", [])
            if (not cls or e.get("className") == cls) and (not typ or e.get("examType") == typ)
        ]
        labels = [f"{e['title']} ({e['totalQuestions']} প্রশ্ন)" for e in self.filtered_exams]
        self.cb_exam["values"] = labels
        if labels:
            self.cb_exam.current(0)
            self._on_exam_selected()
        else:
            self.var_exam.set("এই বিভাগে পরীক্ষা নেই")

    def _on_exam_selected(self):
        idx = self.cb_exam.current()
        if 0 <= idx < len(self.filtered_exams):
            e = self.filtered_exams[idx]
            self.active_exam = dict(e)
            self.var_total_q.set(str(e["totalQuestions"]))
            self.var_pos.set(str(e["positiveMark"]))
            self.var_neg.set(str(e["negativeMark"]))
            col_name = e.get("subject") or f"{e.get('examType', '')} - {e.get('title', '')}".strip(" -")
            self.var_sheet_col.set(col_name)
            sets = ", ".join(e.get("answerKeys", {}).keys()) or "নেই"
            self.lbl_sets.config(text=f"সেট: {sets}")

    def load_offline_json(self):
        p = filedialog.askopenfilename(title="অফলাইন JSON উত্তরমালা নির্বাচন করুন", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not p:
            return
        try:
            cfg = parse_offline_json(Path(p).read_text(encoding="utf-8"))
            self.active_exam = cfg
            self.var_total_q.set(str(cfg["totalQuestions"]))
            self.var_pos.set(str(cfg["positiveMark"]))
            self.var_neg.set(str(cfg["negativeMark"]))
            if cfg.get("className"):
                self.var_sheet_tab.set(cfg["className"])
            if cfg.get("subject") or cfg.get("title"):
                self.var_sheet_col.set(cfg.get("subject") or cfg.get("title"))
            sets = ", ".join(cfg.get("answerKeys", {}).keys()) or "নেই"
            self.lbl_sets.config(text=f"অফলাইন JSON ({sets})")
            messagebox.showinfo("সফল", f"উত্তরমালা লোড হয়েছে: {cfg['title']} (সেট: {sets})")
        except Exception as err:
            messagebox.showerror("ত্রুটি", f"JSON ফাইল সঠিক নয়: {err}")

    # ---------------- Crash-Proof Streaming Batch Scan ----------------
    def scan_files(self):
        paths = filedialog.askopenfilenames(
            title="OMR ছবি বা PDF নির্বাচন করুন",
            filetypes=[("OMR Files", "*.jpg *.jpeg *.png *.pdf"), ("All files", "*.*")],
        )
        if paths:
            self._start_scan([Path(p) for p in paths])

    def scan_folder(self):
        folder = filedialog.askdirectory(title="OMR ফোল্ডার নির্বাচন করুন")
        if not folder:
            return
        exts = {".jpg", ".jpeg", ".png", ".pdf"}
        files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in exts)
        if not files:
            messagebox.showwarning("ফাঁকা", "এই ফোল্ডারে কোনো JPG, PNG বা PDF ফাইল পাওয়া যায়নি।")
            return
        self._start_scan(files)

    def stop_scan(self):
        self.stop_flag = True
        self.lbl_prog.config(text="থামানো হচ্ছে...")

    def _start_scan(self, file_paths):
        if self.scanning:
            return
        self._save_settings()
        keys = self.active_exam.get("answerKeys", {})
        if not keys:
            if not messagebox.askyesno("সতর্কতা", "কোনো উত্তরমালা লোড করা নেই। তবুও কি স্ক্যান করবেন?"):
                return
        try:
            total_q = max(1, min(50, int(self.var_total_q.get())))
            pos = float(self.var_pos.get())
            neg = float(self.var_neg.get())
        except ValueError:
            messagebox.showerror("ত্রুটি", "মোট প্রশ্ন ও নম্বর সঠিকভাবে সংখ্যায় লিখুন।")
            return

        self.scanning = True
        self.stop_flag = False
        self.btn_pick_files.config(state="disabled")
        self.btn_pick_folder.config(state="disabled")
        self.btn_stop.config(state="normal")

        def worker():
            total_pages = count_total_pages(file_paths)
            done = 0
            pending_batch = []
            self.after(0, lambda: self.progress.configure(maximum=max(1, total_pages), value=0))

            for fp in file_paths:
                if self.stop_flag:
                    break
                try:
                    for page_num, bgr in stream_file_pages(fp):
                        if self.stop_flag:
                            break
                        done += 1
                        self.after(0, lambda d=done, t=total_pages, n=fp.name: self.lbl_prog.config(text=f"স্ক্যান হচ্ছে: {d}/{t} ({n})"))
                        prev_file = self.session_dir / f"sheet_{int(time.time() * 1000)}_{done}.jpg"
                        try:
                            res = process_image_to_disk(bgr, keys, total_q, pos, neg, prev_file)
                            res["file"] = fp.name
                            res["page"] = page_num
                        except Exception as e:
                            res = {
                                "file": fp.name, "page": page_num, "roll": "", "roll_valid": False,
                                "set_code": "", "set_latin": "-", "correct": 0, "wrong": 0, "unanswered": 0,
                                "total_marks": None, "scored": False, "warnings": [str(e)],
                                "preview_path": "", "sync": "error", "student_name": "", "sync_message": str(e),
                            }
                        idx = len(self.results)
                        self.results.append(res)
                        self.checked_indices.add(idx)
                        self._autosave_session()
                        self.after(0, lambda i=idx, r=res, d=done: (self._insert_row(i, r), self.progress.configure(value=d)))

                        if self.var_auto_sync.get() and res.get("roll_valid") and res.get("scored"):
                            pending_batch.append(idx)
                            if len(pending_batch) >= 25:
                                self._sync_indices_blocking(pending_batch)
                                pending_batch = []
                except Exception as err:
                    idx = len(self.results)
                    res = {
                        "file": fp.name, "page": 1, "roll": "", "roll_valid": False, "set_code": "", "set_latin": "-",
                        "correct": 0, "wrong": 0, "unanswered": 0, "total_marks": None, "scored": False,
                        "warnings": [str(err)], "preview_path": "", "sync": "error", "student_name": "", "sync_message": str(err),
                    }
                    self.results.append(res)
                    self.after(0, lambda i=idx, r=res: self._insert_row(i, r))

            if pending_batch and not self.stop_flag:
                self._sync_indices_blocking(pending_batch)

            self.after(0, lambda d=done: self._finish_scan(d))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_scan(self, count: int):
        self.scanning = False
        self.btn_pick_files.config(state="normal")
        self.btn_pick_folder.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_prog.config(text=f"সম্পন্ন — মোট {count}টি খাতা প্রসেস হয়েছে")

    def _autosave_session(self):
        try:
            (self.session_dir / "results.json").write_text(
                json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ---------------- Table Rendering & Checkboxes ----------------
    def _row_values(self, idx: int, r: dict):
        chk = "☑" if idx in self.checked_indices else "☐"
        fname = f"{r['file']} (p{r['page']})" if r.get("page", 1) > 1 else r["file"]
        sync_lbl = {
            "synced": f"✔ {r.get('student_name') or 'Synced'}",
            "not_found": "⚠ রোল পাওয়া যায়নি",
            "error": f"✘ {r.get('sync_message', 'Error')[:25]}",
            "pending": "পেন্ডিং",
        }.get(r.get("sync", "pending"), "পেন্ডিং")
        return (
            chk,
            idx + 1,
            fname,
            r.get("roll", ""),
            f"{r.get('set_latin', '-')} {r.get('set_code', '')}".strip(),
            r.get("correct", 0),
            r.get("wrong", 0),
            r.get("unanswered", 0),
            r["total_marks"] if r.get("total_marks") is not None else "-",
            sync_lbl,
            " | ".join(r.get("warnings", [])),
        )

    def _insert_row(self, idx: int, r: dict):
        self.tree.insert("", "end", iid=str(idx), values=self._row_values(idx, r))

    def _refresh_row(self, idx: int):
        if self.tree.exists(str(idx)):
            self.tree.item(str(idx), values=self._row_values(idx, self.results[idx]))

    def _on_tree_click(self, event):
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        if col == "#1" and row_id:
            idx = int(row_id)
            if idx in self.checked_indices:
                self.checked_indices.remove(idx)
            else:
                self.checked_indices.add(idx)
            self._refresh_row(idx)

    def select_all_rows(self):
        self.checked_indices = set(range(len(self.results)))
        for i in range(len(self.results)):
            self._refresh_row(i)

    def deselect_all_rows(self):
        self.checked_indices.clear()
        for i in range(len(self.results)):
            self._refresh_row(i)

    def clear_results(self):
        if not self.results:
            return
        if messagebox.askyesno("নিশ্চিত করুন", "ফলাফলের তালিকা পরিষ্কার করতে চান?"):
            self.results.clear()
            self.checked_indices.clear()
            for item in self.tree.get_children():
                self.tree.delete(item)

    # ---------------- Batch Google Sheet Sync ----------------
    def _sync_indices_blocking(self, indices):
        cls_tab = self.var_sheet_tab.get().strip()
        subj_col = self.var_sheet_col.get().strip()
        gas_url = self.var_gas_url.get().strip()
        if not subj_col or not gas_url:
            return
        payload_items = [
            {"roll": self.results[i]["roll"], "marks": self.results[i]["total_marks"]}
            for i in indices
            if self.results[i].get("scored") and self.results[i].get("total_marks") is not None
        ]
        if not payload_items:
            return
        self.after(0, lambda: self.lbl_prog.config(text=f"Google Sheet ({cls_tab}) ট্যাবে {len(payload_items)}টি নম্বর সিঙ্ক হচ্ছে..."))
        try:
            outcomes = batch_sync_google_sheet(gas_url, cls_tab, subj_col, payload_items)
            for i in indices:
                r = self.results[i]
                o = outcomes.get(str(r["roll"]))
                if o:
                    r["sync"] = o["status"]
                    r["student_name"] = o.get("student_name", "")
                    r["sync_message"] = o.get("message", "")
                    self.after(0, lambda idx=i: self._refresh_row(idx))
            self._autosave_session()
        except Exception as err:
            for i in indices:
                self.results[i]["sync"] = "error"
                self.results[i]["sync_message"] = str(err)[:80]
                self.after(0, lambda idx=i: self._refresh_row(idx))

    def sync_all_pending_async(self):
        self._save_settings()
        if not self.var_sheet_col.get().strip():
            messagebox.showwarning("তথ্য দিন", "ডান পাশে Google Sheet কলামের নাম (যেমন: Bangla) লিখুন।")
            return
        pending = [
            i for i, r in enumerate(self.results)
            if r.get("scored") and r.get("total_marks") is not None and r.get("sync") != "synced" and str(r.get("roll", "")).isdigit()
        ]
        if not pending:
            messagebox.showinfo("তথ্য", "সিঙ্ক করার মতো কোনো পেন্ডিং ফলাফল নেই।")
            return
        def worker():
            self._sync_indices_blocking(pending)
            self.after(0, lambda: self.lbl_prog.config(text="Google Sheet সিঙ্ক সম্পন্ন হয়েছে"))
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Preview Window & Roll Edit ----------------
    def open_selected_preview(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("নির্বাচন করুন", "প্রিভিউ দেখতে তালিকার যেকোনো একটি খাতায় ক্লিক করুন।")
            return
        idx = int(sel[0])
        r = self.results[idx]
        p = r.get("preview_path")
        if not p or not Path(p).exists():
            messagebox.showwarning("নেই", "এই খাতার প্রিভিউ ছবি পাওয়া যায়নি।")
            return

        win = tk.Toplevel(self)
        win.title(f"প্রিভিউ — রোল {r.get('roll', '-')} ({r.get('file', '')})")
        win.geometry("760x860")
        win.configure(bg="#1E293B")

        top = ttk.Frame(win, style="Card.TFrame", padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="রোল সংশোধন:", style="Card.TLabel").pack(side="left")
        var_roll = tk.StringVar(value=r.get("roll", ""))
        ttk.Entry(top, textvariable=var_roll, width=12).pack(side="left", padx=6)

        def save_and_resync():
            new_roll = var_roll.get().strip()
            if not new_roll.isdigit():
                messagebox.showerror("ত্রুটি", "রোল নম্বর শুধু সংখ্যায় দিন।", parent=win)
                return
            r["roll"] = new_roll
            r["roll_valid"] = len(new_roll) == 6
            r["sync"] = "pending"
            self._refresh_row(idx)
            threading.Thread(target=lambda: self._sync_indices_blocking([idx]), daemon=True).start()
            messagebox.showinfo("সিঙ্ক", "রোল আপডেট করে Google Sheet-এ পাঠানো হচ্ছে।", parent=win)

        ttk.Button(top, text="রোল আপডেট ও Re-Sync", style="Accent.TButton", command=save_and_resync).pack(side="left", padx=4)

        def save_single_pdf():
            out = filedialog.asksaveasfilename(
                parent=win,
                defaultextension=".pdf",
                initialfile=f"OMR_Roll_{r.get('roll', idx + 1)}.pdf",
                filetypes=[("PDF file", "*.pdf")],
            )
            if out:
                export_marked_pdf([p], Path(out))
                messagebox.showinfo("সম্পন্ন", "এই খাতার মার্কড PDF সেভ হয়েছে।", parent=win)

        ttk.Button(top, text="এই খাতার PDF সেভ", command=save_single_pdf).pack(side="right", padx=4)

        # Scrollable Preview Image
        canvas = tk.Canvas(win, bg="#1E293B", highlightthickness=0)
        vsb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        im = Image.open(p)
        target_w = 700
        ratio = target_w / im.width
        im_resized = im.resize((target_w, int(im.height * ratio)), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(im_resized)
        win._photo = photo  # prevent GC
        canvas.create_image(15, 10, anchor="nw", image=photo)
        canvas.configure(scrollregion=(0, 0, target_w + 30, im_resized.height + 20))

    # ---------------- Export Marked PDF (All or Selected) & CSV ----------------
    def export_pdf(self, selected_only: bool):
        if not self.results:
            messagebox.showinfo("ফাঁকা", "এখনো কোনো খাতা স্ক্যান করা হয়নি।")
            return
        if selected_only:
            idxs = sorted(i for i in self.checked_indices if i < len(self.results))
            if not idxs:
                messagebox.showwarning("সিলেক্ট নেই", "প্রথমে তালিকার বাম পাশের ☑ ঘরে ক্লিক করে খাতা সিলেক্ট করুন।")
                return
            default_name = f"Selected_Marked_OMR_{len(idxs)}.pdf"
        else:
            idxs = list(range(len(self.results)))
            default_name = f"All_Marked_OMR_{len(idxs)}.pdf"

        previews = [self.results[i].get("preview_path", "") for i in idxs]
        out = filedialog.asksaveasfilename(
            title="মার্কড OMR PDF সেভ করুন",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out:
            return
        try:
            cnt = export_marked_pdf(previews, Path(out))
            messagebox.showinfo("সম্পন্ন", f"{cnt}টি মার্কড OMR খাতা নিয়ে PDF সফলভাবে সেভ হয়েছে!")
        except Exception as err:
            messagebox.showerror("ত্রুটি", f"PDF তৈরি ব্যর্থ: {err}")

    def export_csv(self):
        if not self.results:
            messagebox.showinfo("ফাঁকা", "ডাউনলোড করার মতো কোনো ফলাফল নেই।")
            return
        out = filedialog.asksaveasfilename(
            title="CSV ফলাফল সেভ করুন",
            defaultextension=".csv",
            initialfile=f"OMR_Results_{self.var_sheet_tab.get() or 'All'}.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not out:
            return
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["#", "File", "Page", "Roll", "Set", "Correct", "Wrong", "Unanswered", "Marks", "Sync", "Student"])
            for i, r in enumerate(self.results, 1):
                w.writerow([
                    i, r.get("file", ""), r.get("page", 1), r.get("roll", ""),
                    r.get("set_latin", ""), r.get("correct", 0), r.get("wrong", 0),
                    r.get("unanswered", 0), r.get("total_marks", ""), r.get("sync", ""), r.get("student_name", ""),
                ])
        messagebox.showinfo("সম্পন্ন", "CSV ফাইল সফলভাবে সেভ হয়েছে।")


if __name__ == "__main__":
    app = ByabodhanWindowsApp()
    app.mainloop()
