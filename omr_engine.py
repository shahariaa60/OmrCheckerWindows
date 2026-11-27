"""Memory-safe offline OMR Engine for Byabodhan Shikkhalay Windows App.
Processes 1 image or 1 PDF page at a time, saves annotated preview JPEG to disk,
and frees memory immediately so 500+ sheets never crash or slow down the PC."""

import gc
import math
import os
import re
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

W, H, M = 1240, 1760, 70
D = 25
MR = (1172, 225)
OPT = "কখগঘ"
LAT = {"A": "ক", "B": "খ", "C": "গ", "D": "ঘ"}
DISP_MAX = 20
DARK, MARK_MIN, MARK_MAX = 125, 0.40, 0.65

ROI = {
    "ans1": {"box": [0.1548, 0.2318, 0.0827, 0.6622], "rows": 25, "cols": 4},
    "ans2": {"box": [0.3040, 0.2338, 0.0811, 0.6605], "rows": 25, "cols": 4},
    "roll": {"box": [0.4605, 0.2597, 0.1270, 0.2019], "rows": 10, "cols": 6},
    "set":  {"box": [0.6516, 0.2599, 0.0000, 0.0685], "rows": 4, "cols": 1},
    "reg":  {"box": [0.4593, 0.5574, 0.2286, 0.2026], "rows": 10, "cols": 10},
    "subj": {"box": [0.7375, 0.5574, 0.0528, 0.2032], "rows": 10, "cols": 3, "dead": [[4, 0]]},
}

R = int(D * 0.32)
_yy, _xx = np.ogrid[-R:R + 1, -R:R + 1]
DISC = (_xx * _xx + _yy * _yy) <= R * R

RING = np.zeros((27, 27), np.float32)
cv2.circle(RING, (13, 13), 12, 1, 3)
SAMP = np.array([(math.cos(t), math.sin(t)) for t in np.linspace(0, 2 * math.pi, 16, endpoint=False)]) * 12.0
S2 = 6

GREEN, RED, BLUE, ORANGE = (60, 180, 0), (0, 0, 230), (230, 120, 0), (0, 140, 255)


def normalize_opt(v) -> str:
    s = str(v or "").strip()
    return LAT.get(s.upper(), s if s in OPT else "")


def norm_gray(img, k):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bg = cv2.GaussianBlur(cv2.dilate(g, np.ones((k, k), np.uint8)), (0, 0), k / 3)
    n = np.clip(g.astype(np.float32) / np.maximum(bg, 1) * 255, 0, 255).astype(np.uint8)
    return g, n


def find_markers(n):
    h, w = n.shape
    found, edge = [], []
    for t in (110, 125, 140):
        b = cv2.threshold(n, t, 255, cv2.THRESH_BINARY_INV)[1]
        for k in cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            x, y, bw, bh = cv2.boundingRect(k)
            if 0.02 * w < bw < 0.1 * w and 0.02 * w < bh < 0.1 * w and 0.5 < bw / bh < 2                     and cv2.contourArea(k) > 0.8 * bw * bh:
                c = (x + bw / 2, y + bh / 2)
                if all(math.hypot(c[0] - f[0], c[1] - f[1]) > 0.03 * w for f in found):
                    found.append(c)
                    edge.append(x <= 1 or y <= 1 or x + bw >= w - 2 or y + bh >= h - 2)
    return found, edge


def order4i(p):
    p = np.array(p, np.float32)
    s, d = p.sum(1), p[:, 0] - p[:, 1]
    return [s.argmin(), d.argmax(), d.argmin(), s.argmax()]


def order4(p):
    p = np.array(p, np.float32)
    return p[order4i(p)]


def plausible(q, shape):
    area = cv2.contourArea(q[[0, 1, 3, 2]].reshape(-1, 1, 2))
    wq = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[3] - q[2])) / 2
    hq = (np.linalg.norm(q[2] - q[0]) + np.linalg.norm(q[3] - q[1])) / 2
    return len({tuple(r) for r in q.round()}) == 4 and area > 0.2 * shape[0] * shape[1] and 0.5 < wq / hq < 0.95


def paper_quad(g):
    b = cv2.threshold(cv2.GaussianBlur(g, (9, 9), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    contours = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    if not contours:
        return np.float32([[0, 0], [g.shape[1], 0], [0, g.shape[0]], [g.shape[1], g.shape[0]]])
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    ap = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    return order4(ap.reshape(-1, 2) if len(ap) == 4 else cv2.boxPoints(cv2.minAreaRect(hull)))


def blob(g, cx, cy, r=22):
    return g[cy - r:cy + r, cx - r:cx + r].mean() < 0.6 * g[cy - 3 * r:cy + 3 * r, cx - 3 * r:cx + 3 * r].mean()


def warp(img):
    if img.shape[1] > 1800:
        img = cv2.resize(img, (1800, int(img.shape[0] * 1800 / img.shape[1])), interpolation=cv2.INTER_AREA)
    g, n = norm_gray(img, int(0.07 * img.shape[1]))
    mk, edge = find_markers(n)
    idx = order4i(mk) if len(mk) >= 4 else None
    q = np.array(mk, np.float32)[idx] if idx is not None else None
    if q is not None and plausible(q, g.shape):
        mode, dst = "markers", [(M, M), (W - M, M), (M, H - M), (W - M, H - M)]
        if any(edge[i] for i in idx):
            mode += "+EDGE-CUT"
    else:
        q, mode, dst = paper_quad(g), "paper-edges", [(0, 0), (W, 0), (0, H), (W, H)]
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(q, np.float32(dst)), (W, H))
    wg = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    if not blob(wg, *MR) and blob(wg, W - MR[0], H - MR[1]):
        out, mode = cv2.rotate(out, cv2.ROTATE_180), mode + "+rot180"
    return out, mode


def centers(cfg):
    x, y, w, h = cfg["box"]
    r, c = cfg["rows"], cfg["cols"]
    return [[((x + (w * j / (c - 1) if c > 1 else 0)) * W, (y + h * i / (r - 1)) * H) for j in range(c)] for i in range(r)]


def basis(u, v, deg2):
    c = [np.ones_like(u), u, v] + ([u * u, u * v, v * v] if deg2 else [])
    return np.stack(c, -1)


def locate(dk, cfg):
    P = P0 = np.array(centers(cfg))
    rows, cols = P.shape[:2]
    live = np.ones((rows, cols), bool)
    for r0, c0 in cfg.get("dead", []):
        live[r0:, c0] = False
    hh, ww = dk.shape
    dkd = cv2.dilate(dk, np.ones((3, 3), np.uint8))
    pts = (P[live][:, None, :] + SAMP[None]).reshape(-1, 2)
    best, dx0, dy0 = -1, 0, 0
    for dy in range(-14, 15, 2):
        for dx in range(-14, 15, 2):
            xs = np.clip((pts[:, 0] + dx).round().astype(int), 0, ww - 1)
            ys = np.clip((pts[:, 1] + dy).round().astype(int), 0, hh - 1)
            sc = dkd[ys, xs].sum()
            if sc > best:
                best, dx0, dy0 = sc, dx, dy
    P = P + (dx0, dy0)
    h = 13 + S2
    v, u = np.mgrid[0:rows, 0:cols].astype(np.float64)
    u, v = (u - (cols - 1) / 2) / max(cols - 1, 1) * 2, (v - (rows - 1) / 2) / max(rows - 1, 1) * 2

    def snap_all(C):
        Q, ok = C.copy(), np.zeros((rows, cols), bool)
        for i in range(rows):
            for j in range(cols):
                if not live[i, j]:
                    continue
                cx, cy = int(round(C[i, j, 0])), int(round(C[i, j, 1]))
                win = dk[cy - h:cy + h + 1, cx - h:cx + h + 1]
                if win.shape != (2 * h + 1, 2 * h + 1):
                    continue
                _, mx, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, RING, cv2.TM_CCORR))
                if mx >= 0.35 * RING.sum():
                    ok[i, j], Q[i, j] = True, (cx - S2 + loc[0], cy - S2 + loc[1])
        return Q, ok

    F, rms, nfound = P, 0.0, 0
    for _ in range(4):
        Q, ok = snap_all(F)
        if ok.sum() < 3:
            break
        for _ in range(3):
            deg2 = ok.sum() >= 40
            coef = np.linalg.lstsq(basis(u[ok], v[ok], deg2), Q[ok], rcond=None)[0]
            res = np.hypot(*(basis(u[ok], v[ok], deg2) @ coef - Q[ok]).T)
            keep = res <= max(2.5, 2.5 * float(np.median(res)))
            if keep.all() or keep.sum() < 3:
                break
            ok.flat[np.flatnonzero(ok)[~keep]] = False
        F, rms, nfound = basis(u, v, deg2) @ coef, float(np.sqrt((res ** 2).mean())), int(ok.sum())
    return [[tuple(F[i, j]) for j in range(cols)] for i in range(rows)], (
        nfound, int(live.sum()), rms, float(np.hypot(*(np.array(F) - P0)[live].T).max())
    )


def fills(wn, dk, cfg):
    cs, stat = locate(dk, cfg)
    out = np.zeros((cfg["rows"], cfg["cols"]))
    for i, row in enumerate(cs):
        for j, (cx, cy) in enumerate(row):
            cx, cy = int(round(cx)), int(round(cy))
            p = wn[cy - R:cy + R + 1, cx - R:cx + R + 1]
            out[i, j] = (p[DISC] < DARK).mean() if p.shape == DISC.shape else 0
    for r0, c0 in cfg.get("dead", []):
        out[r0:, c0] = 0
    return out, cs, stat


def marked(f):
    base = float(np.median(f))
    return f >= min(max(base + 0.30, MARK_MIN), MARK_MAX)


def col_digits(s):
    out = ""
    for j in range(s.shape[1]):
        r = np.flatnonzero(s[:, j])
        out += "?" if len(r) == 0 else str(r[0]) if len(r) == 1 else "*"
    return out


def row_choice(s):
    r = np.flatnonzero(s)
    return "" if len(r) == 0 else OPT[r[0]] if len(r) == 1 else "*"


def draw_ring(im, c, color, t=3):
    cv2.circle(im, (int(c[0]), int(c[1])), int(D * 0.62), color, t, cv2.LINE_AA)


def process_image_to_disk(
    img: np.ndarray,
    keys: Dict[str, Dict[str, str]],
    total_questions: int,
    pos: float,
    neg: float,
    preview_save_path: Path,
) -> dict:
    """Process one BGR image, save annotated JPEG to preview_save_path, and release heavy buffers."""
    wimg, mode = warp(img)
    wn = cv2.GaussianBlur(norm_gray(wimg, 41)[1], (3, 3), 0)
    dk = (wn < 185).astype(np.float32)
    rd = {k: fills(wn, dk, ROI[k]) for k in ROI}
    sel = {k: marked(v[0]) for k, v in rd.items()}
    cs = {k: v[1] for k, v in rd.items()}
    stats = {k: v[2] for k, v in rd.items()}

    roll, reg, subj = col_digits(sel["roll"]), col_digits(sel["reg"]), col_digits(sel["subj"])
    sc = row_choice(sel["set"][:, 0])
    ans = [row_choice(sel["ans1"][i]) for i in range(25)] + [row_choice(sel["ans2"][i]) for i in range(25)]

    warns = []
    if "EDGE" in mode:
        warns.append("কালো কোণা কেটে গেছে")
    if mode.startswith("paper"):
        warns.append("৪টি কোণা পাওয়া যায়নি (কাগজের ধার)")
    for k, (f, n, rms, dsp) in stats.items():
        if f < 0.75 * n or dsp > DISP_MAX:
            warns.append(f"'{k}' এলাইনমেন্ট অনিশ্চিত ({f}/{n})")

    valid_set = sc != "" and sc in OPT
    key = keys.get(sc) if valid_set else None
    if key is None and len(keys) == 1:
        key = next(iter(keys.values()))
        warns.append("সেট কোড পাওয়া যায়নি — একমাত্র উত্তরমালা ব্যবহার করা হয়েছে")
    if not valid_set:
        warns.append("সেট কোড ফাঁকা বা ডাবল")
    roll_valid = bool(re.fullmatch(r"\d{6}", roll))
    if not roll_valid:
        warns.append("রোল নম্বরে ফাঁকা বা ডাবল দাগ")

    total_q = max(1, min(50, int(total_questions)))
    cor = wro = una = 0
    per = {}
    if key:
        for qi in range(1, total_q + 1):
            k_opt = normalize_opt(key.get(str(qi)) or key.get(qi))
            if not k_opt:
                continue
            a = ans[qi - 1]
            st = "blank" if a == "" else "correct" if a == k_opt else "wrong"
            cor += (st == "correct")
            wro += (st == "wrong")
            una += (st == "blank")
            per[qi] = (st, k_opt)
    total = round(cor * pos - wro * neg, 2) if key else None

    # ---- Draw marked preview & save directly to disk (keeps RAM flat!) ----
    im = wimg.copy()
    for q in range(1, total_q + 1):
        g, i = ("ans1", q - 1) if q <= 25 else ("ans2", q - 26)
        row, picked = cs[g][i], np.flatnonzero(sel[g][i])
        st, k_opt = per.get(q, (None, None))
        if st is None:
            for j in picked:
                draw_ring(im, row[j], ORANGE)
            continue
        if st == "correct":
            draw_ring(im, row[OPT.index(k_opt)], GREEN)
        else:
            for j in picked:
                draw_ring(im, row[j], RED)
            draw_ring(im, row[OPT.index(k_opt)], BLUE)
    for g in ("roll", "reg", "subj", "set"):
        for i, j in zip(*np.nonzero(sel[g])):
            draw_ring(im, cs[g][i][j], ORANGE, 2)

    bar = np.full((52, W, 3), 255, np.uint8)
    lat = "ABCD"[OPT.index(sc)] if valid_set else "-"
    cv2.putText(
        bar,
        f"Roll {roll}  Set {lat}  OK {cor}  Wrong {wro}  Blank {una}  Marks {total}",
        (15, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    stacked = np.vstack([bar, im])
    preview_save_path.parent.mkdir(parents=True, exist_ok=True)
    # Use imencode + write_bytes so Unicode/Bangla paths on Windows work 100% reliably
    ok_enc, buf = cv2.imencode(".jpg", stacked, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if ok_enc:
        preview_save_path.write_bytes(buf.tobytes())

    # Free heavy arrays immediately
    del wimg, wn, dk, im, bar, stacked
    gc.collect()

    return {
        "roll": roll,
        "roll_valid": roll_valid,
        "set_code": sc,
        "set_latin": lat,
        "registration": reg,
        "subject_code": subj,
        "correct": cor,
        "wrong": wro,
        "unanswered": una,
        "total_marks": total,
        "max_marks": round(total_q * pos, 2),
        "scored": key is not None,
        "warnings": warns,
        "warp_mode": mode,
        "preview_path": str(preview_save_path),
        "sync": "pending",
        "student_name": "",
        "sync_message": "",
    }


def count_total_pages(file_paths: List[Path]) -> int:
    total = 0
    for p in file_paths:
        if p.suffix.lower() == ".pdf" and fitz is not None:
            try:
                with fitz.open(str(p)) as doc:
                    total += len(doc)
            except Exception:
                total += 1
        else:
            total += 1
    return total


def stream_file_pages(file_path: Path) -> Generator[Tuple[int, np.ndarray], None, None]:
    """Yields (page_number, bgr_image) one page at a time — safe for 200-page PDFs!"""
    if file_path.suffix.lower() == ".pdf":
        if fitz is None:
            raise RuntimeError("PyMuPDF ইনস্টল করা নেই — PDF পড়া যাচ্ছে না")
        with fitz.open(str(file_path)) as doc:
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=180, alpha=False)
                arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
                bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR if pix.n == 1 else cv2.COLOR_RGB2BGR)
                yield i + 1, bgr
                del pix, arr, bgr
                gc.collect()
    else:
        raw = file_path.read_bytes()
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("ছবি ফাইল পড়া যায়নি")
        yield 1, img


def export_marked_pdf(preview_paths: List[str], out_pdf: Path) -> int:
    """Builds a crisp multi-page A4 PDF containing the exact marked OMR previews (1 sheet per page)."""
    valid = [Path(p) for p in preview_paths if p and Path(p).exists()]
    if not valid:
        raise ValueError("PDF তৈরির জন্য কোনো মার্কড প্রিভিউ পাওয়া যায়নি")

    if fitz is not None:
        doc = fitz.open()
        a4_w, a4_h = 595.28, 841.89  # A4 portrait in points
        margin = 14
        box = fitz.Rect(margin, margin, a4_w - margin, a4_h - margin)
        for p in valid:
            page = doc.new_page(width=a4_w, height=a4_h)
            page.insert_image(box, filename=str(p), keep_proportion=True)
        doc.save(str(out_pdf), deflate=True)
        doc.close()
        return len(valid)

    # Pillow fallback if PyMuPDF is absent
    imgs = [Image.open(p).convert("RGB") for p in valid]
    imgs[0].save(str(out_pdf), save_all=True, append_images=imgs[1:])
    for im in imgs:
        im.close()
    return len(valid)
