"""Firebase Firestore REST reader (with local offline cache) + Multi-Tab Batch Google Sheet Sync."""

import json
from pathlib import Path
from typing import Any, Dict, List

import requests

from omr_engine import normalize_opt

FIREBASE_PROJECT_ID = "byabodhan-omr"
FIREBASE_API_KEY = "AIzaSyAvEuwPTNGcRi_suYcLooj-462wnX3u73Q"
DEFAULT_GAS_URL = "https://script.google.com/macros/s/AKfycbyzljD5b8ADg6QJwRZK5-Ml9C2_ot-mfhizRZRHnuxKOA2HvHDJIQlIYBuQdwCn0L4h/exec"

DEFAULT_CLASSES = ["৬ষ্ঠ শ্রেণি", "৭ম শ্রেণি", "৮ম শ্রেণি", "৯ম শ্রেণি", "১০ম শ্রেণি"]
DEFAULT_EXAM_TYPES = ["মডেল টেস্ট", "অর্ধবার্ষিক পরীক্ষা", "বার্ষিক পরীক্ষা", "সাপ্তাহিক পরীক্ষা"]


def _decode_fs_val(val: dict) -> Any:
    if "stringValue" in val:
        return val["stringValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "booleanValue" in val:
        return bool(val["booleanValue"])
    if "mapValue" in val:
        fields = val["mapValue"].get("fields", {})
        return {k: _decode_fs_val(v) for k, v in fields.items()}
    if "arrayValue" in val:
        return [_decode_fs_val(v) for v in val["arrayValue"].get("values", [])]
    return None


def _fetch_collection(collection: str) -> List[dict]:
    url = (
        f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
        f"/databases/(default)/documents/{collection}?key={FIREBASE_API_KEY}&pageSize=300"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    docs = r.json().get("documents", [])
    out = []
    for d in docs:
        doc_id = d["name"].split("/")[-1]
        fields = {k: _decode_fs_val(v) for k, v in d.get("fields", {}).items()}
        fields["id"] = doc_id
        out.append(fields)
    return out


def fetch_firestore_bundle(cache_path: Path) -> dict:
    """Downloads classes, exam_types, and exams from Firestore and caches locally for offline use."""
    try:
        raw_classes = _fetch_collection("classes")
        raw_types = _fetch_collection("exam_types")
        raw_exams = _fetch_collection("exams")

        classes = [
            c["name"] for c in sorted(raw_classes, key=lambda x: x.get("order", 99))
            if c.get("active", True) is not False and c.get("name")
        ] or list(DEFAULT_CLASSES)

        exam_types = [
            t["name"] for t in sorted(raw_types, key=lambda x: x.get("order", 99))
            if t.get("active", True) is not False and t.get("name")
        ] or list(DEFAULT_EXAM_TYPES)

        exams = []
        for e in raw_exams:
            raw_keys = e.get("answerKeys") or {}
            clean_keys = {}
            for s, qmap in raw_keys.items():
                ns = normalize_opt(s)
                if not ns or not isinstance(qmap, dict):
                    continue
                clean_keys[ns] = {str(q): normalize_opt(a) for q, a in qmap.items() if normalize_opt(a)}
            exams.append({
                "id": e.get("id", ""),
                "title": e.get("title", "পরীক্ষা"),
                "className": e.get("className", ""),
                "examType": e.get("examType", ""),
                "subject": e.get("subject", ""),
                "totalQuestions": int(e.get("totalQuestions", 50)),
                "positiveMark": float(e.get("positiveMark", 1.0)),
                "negativeMark": float(e.get("negativeMark", 0.0)),
                "answerKeys": clean_keys,
            })

        bundle = {"classes": classes, "exam_types": exam_types, "exams": exams, "source": "online"}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        return bundle
    except Exception as err:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["source"] = "cache"
            cached["error"] = str(err)
            return cached
        return {
            "classes": list(DEFAULT_CLASSES),
            "exam_types": list(DEFAULT_EXAM_TYPES),
            "exams": [],
            "source": "offline",
            "error": str(err),
        }


def parse_offline_json(json_text: str) -> dict:
    root = json.loads(json_text)
    src = root.get("keys") or root.get("answerKeys") or root
    clean_keys = {}
    max_q = 0
    for s, qmap in src.items():
        ns = normalize_opt(s)
        if not ns or not isinstance(qmap, dict):
            continue
        cleaned = {}
        for q, a in qmap.items():
            na = normalize_opt(a)
            try:
                qn = int(str(q).strip())
            except ValueError:
                continue
            if 1 <= qn <= 50 and na:
                cleaned[str(qn)] = na
                max_q = max(max_q, qn)
        if cleaned:
            clean_keys[ns] = cleaned
    return {
        "id": "offline_json",
        "title": root.get("title", "অফলাইন JSON উত্তরমালা"),
        "className": root.get("className", ""),
        "examType": root.get("examType", ""),
        "subject": root.get("subject", ""),
        "totalQuestions": int(root.get("totalQuestions", max_q or 50)),
        "positiveMark": float(root.get("positiveMark", 1.0)),
        "negativeMark": float(root.get("negativeMark", 0.0)),
        "answerKeys": clean_keys,
    }


def batch_sync_google_sheet(
    gas_url: str,
    class_name: str,
    subject: str,
    items: List[dict],
    chunk_size: int = 25,
) -> Dict[str, dict]:
    # Sends students in chunks of 25 to the Multi-Tab Code.gs webhook.
    if not gas_url.strip():
        raise ValueError("Google Apps Script URL দেওয়া হয়নি")
    if not subject.strip():
        raise ValueError("Google Sheet কলামের নাম (বিষয়) দেওয়া হয়নি")

    outcomes: Dict[str, dict] = {}
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        payload = {
            "class_name": class_name.strip(),
            "subject": subject.strip(),
            "items": [{"roll": str(x["roll"]), "marks": float(x["marks"])} for x in chunk],
        }
        r = requests.post(
            gas_url.strip(),
            data=json.dumps(payload),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            timeout=45,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            msg = data.get("message", "Apps Script error")
            for x in chunk:
                outcomes[str(x["roll"])] = {"status": "error", "student_name": "", "message": msg}
            continue
        for res in data.get("results", []):
            outcomes[str(res.get("roll", ""))] = {
                "status": "synced" if res.get("status") == "success" else res.get("status", "error"),
                "student_name": res.get("student_name", ""),
                "message": res.get("message", ""),
            }
    return outcomes
