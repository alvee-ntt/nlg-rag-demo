"""Run the FlexLife underwriting test questions against the Foundry chat endpoint.

Parses tests/underwriting_guide_questions.md, sends each question to
POST /v1/foundry/chat, and writes a CSV + JSONL of
  Question | Expected answer | Received answer  (+ tracking metadata),
so we can track how the hosted KnowledgeBase agent answers over time.

Resilient to being killed mid-run:
  * results are appended to a FIXED-name CSV + JSONL after every question
    (not held in memory until the end), and stdout is line-buffered;
  * on restart it reads the CSV, skips questions already answered *without an
    error*, and re-asks the rest -- so a resume also retries failures.
  Delete tests/results/foundry_eval.csv (+ .jsonl) to force a clean run.

Contextual follow-ups ("Same case, but 55?") are threaded with their parent
question and the parent's *actual* received answer as conversation history.

Env overrides: EVAL_BASE_URL, EVAL_USERNAME, EVAL_PASSWORD, EVAL_SLEEP.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(line_buffering=True)  # progress shows up in the interim file

REPO = Path(__file__).resolve().parents[1]
QUESTIONS = REPO / "tests" / "underwriting_guide_questions.md"
OUT_DIR = REPO / "tests" / "results"
CSV_PATH = OUT_DIR / "foundry_eval.csv"
JSONL_PATH = OUT_DIR / "foundry_eval.jsonl"

BASE = os.getenv("EVAL_BASE_URL", "http://localhost:8000")
USERNAME = os.getenv("EVAL_USERNAME", "user")
PASSWORD = os.getenv("EVAL_PASSWORD", "flexlife")
# Foundry/gpt-5 is slow and 502s under bursty load; a bigger gap between calls
# lets each request finish instead of racing the gateway timeout.
SLEEP_BETWEEN = float(os.getenv("EVAL_SLEEP", "1.5"))

ROW_RE = re.compile(r"^\|\s*(\d+)\s*(★?)\s*\|(.+?)\|(.+?)\|(.+?)\|\s*$")

COLS = [
    "num", "section", "star", "question", "expected", "received",
    "page", "threaded", "citation_urls", "model", "status",
    "response_id", "latency_s", "error",
]


def parse_rows(md: str) -> list[dict]:
    rows: list[dict] = []
    section = ""
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            section = s.lstrip("#").strip()
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        num, star, q, expected, page = m.groups()
        rows.append({
            "num": int(num), "star": bool(star), "section": section,
            "question": q.strip(), "expected": expected.strip(), "page": page.strip(),
        })
    return rows


def is_followup(question: str) -> bool:
    return question.lower().startswith("same")


def load_done() -> dict[int, str]:
    """num -> received answer, for questions already answered WITHOUT an error.
    Errored rows are treated as not-done so a resume retries them."""
    done: dict[int, str] = {}
    if CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    if not r.get("error"):
                        done[int(r["num"])] = r.get("received", "")
                except (KeyError, ValueError):
                    continue
    return done


def login() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    r = s.post(f"{BASE}/v1/auth/login",
               json={"username": USERNAME, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    return s


def ask(s: requests.Session, message: str, history: list[dict]) -> dict:
    t0 = time.time()
    try:
        r = s.post(f"{BASE}/v1/foundry/chat",
                   json={"message": message, "history": history}, timeout=200)
        r.raise_for_status()
        data = r.json()
        data["_latency_s"] = round(time.time() - t0, 1)
        data["_error"] = ""
        return data
    except Exception as exc:  # noqa: BLE001 - record the failure, keep going
        return {"answer": "", "citations": [], "model": None, "status": None,
                "response_id": None, "_latency_s": round(time.time() - t0, 1),
                "_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    rows = parse_rows(QUESTIONS.read_text(encoding="utf-8"))
    print(f"Parsed {len(rows)} questions")
    if len(rows) != 130:
        print("WARNING: expected 130 rows", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = load_done()
    if done:
        print(f"Resuming: {len(done)} done, {len(rows) - len(done)} to (re)do")

    s = login()
    print("Logged in")

    new_csv = not CSV_PATH.exists()
    csv_f = CSV_PATH.open("a", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(csv_f, fieldnames=COLS, extrasaction="ignore")
    if new_csv:
        writer.writeheader()
        csv_f.flush()
    jsonl_f = JSONL_PATH.open("a", encoding="utf-8")

    prev_q = prev_a = ""
    errors = 0
    try:
        for i, row in enumerate(rows):
            num = row["num"]
            if num in done:
                prev_q, prev_a = row["question"], done[num]
                continue

            history: list[dict] = []
            if is_followup(row["question"]) and i > 0:
                parent_a = prev_a or done.get(rows[i - 1]["num"], "")
                if prev_q and parent_a:
                    history = [{"role": "user", "text": prev_q},
                               {"role": "assistant", "text": parent_a}]

            res = ask(s, row["question"], history)
            answer = (res.get("answer") or "").strip()
            cites = res.get("citations") or []
            rec = {
                **row, "received": answer, "threaded": bool(history),
                "citations": cites,
                "citation_urls": "; ".join(c.get("url", "") for c in cites),
                "model": res.get("model"), "status": res.get("status"),
                "response_id": res.get("response_id"),
                "latency_s": res.get("_latency_s"), "error": res.get("_error"),
            }
            writer.writerow(rec)
            csv_f.flush()
            jsonl_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            jsonl_f.flush()

            if rec["error"]:
                errors += 1
            flag = "!" if rec["error"] else ("~" if history else " ")
            print(f"[{i + 1:3}/{len(rows)}] {flag} #{num:<3} {res['_latency_s']:>5}s "
                  f"{row['question'][:58]}")
            prev_q, prev_a = row["question"], answer
            time.sleep(SLEEP_BETWEEN)
    finally:
        csv_f.close()
        jsonl_f.close()

    print(f"\nDone this pass. {errors} errors in new rows.")
    print(f"CSV:   {CSV_PATH}")
    print(f"JSONL: {JSONL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
