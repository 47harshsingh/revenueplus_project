"""Run the 25-question eval through the real chat pipeline and score it.

Usage (what .github/workflows/eval.yml runs):
    DATABASE_URL=... CHAT_DB_PASSWORD=... GEMINI_API_KEY=... GEMINI_MODEL=... \\
        python -m eval.run_eval --load

--load rebuilds DATABASE_URL with the seeded data and the chatbot_ro role first, so the
eval always runs against exactly the data its answer key was computed from.
Writes eval/results.json and eval/results.md (also appended to the GitHub job summary).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import yaml
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from api import db
from api.chat import prompt
from api.chat.llm import GeminiLLM, LLMUnavailable
from api.chat.service import answer_question

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.yaml"


# ---------- scoring ----------

def _numbers(rows: list[dict]) -> list[float]:
    return [float(v) for r in rows for v in r.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-9)


def score(expected: dict, result: dict) -> bool:
    """Lenient by design (see questions.yaml header): is the right answer in the rows?"""
    if expected.get("refused"):
        return result["status"] == "refused"
    if result["status"] != "answered":
        return False
    if "value" in expected:
        v, tol = expected["value"], expected["tolerance"]
        return any(_close(n, v, tol) or _close(n, v * 100, tol) for n in _numbers(result["rows"]))
    cells = [str(v).lower() for r in result["rows"] for v in r.values()]
    return all(any(s.lower() in c for c in cells) for s in expected["contains"])


# ---------- running ----------

def load_seed_data(admin_url: str, chat_password: str) -> None:
    from data.generate import generate
    from data.load import load_all

    with psycopg.connect(admin_url) as conn:
        with conn.transaction():
            load_all(conn, generate(42), chat_password=chat_password)


def chat_url(admin_url: str, chat_password: str) -> str:
    return make_conninfo(**{**conninfo_to_dict(admin_url), "user": "chatbot_ro",
                            "password": chat_password})


class EvalAborted(Exception):
    """A configuration error (bad model name or key): every question would fail the same way."""


def ask(question: str, llm, retries: int = 1, retry_wait: float = 30.0) -> dict:
    """One question. A transient provider error (429 quota, 5xx, malformed JSON) is retried
    once, then recorded as 'error' (excluded from accuracy, not counted as wrong). A config
    error (other 4xx) aborts the whole run instead of failing 25 times the same way."""
    for attempt in range(retries + 1):
        try:
            return answer_question(question, llm).model_dump()
        except LLMUnavailable as e:
            if e.is_config_error:
                raise EvalAborted(f"{e}: check the GEMINI_MODEL variable and GEMINI_API_KEY "
                                  "secret") from e
            if attempt == retries:
                return {"status": "error", "reason": str(e), "rows": [], "sql": None,
                        "answer": ""}
            time.sleep(retry_wait)


def run(questions: list[dict], llm, delay: float) -> list[dict]:
    results = []
    for i, q in enumerate(questions):
        if i:
            time.sleep(delay)  # free-tier rate limits (2 model calls per question)
        r = ask(q["question"], llm)
        ok = None if r["status"] == "error" else score(q["expected"], r)
        results.append({"id": q["id"], "type": q["type"], "question": q["question"],
                        "expected": q["expected"], "correct": ok, **r})
        mark = {True: "PASS", False: "FAIL", None: "ERROR"}[ok]
        print(f"{q['id']} {mark:5} {r['status']:8} {q['question'][:70]}", flush=True)
    return results


def summarise(results: list[dict], model: str) -> dict:
    scored = [r for r in results if r["correct"] is not None]
    correct = sum(r["correct"] for r in scored)
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "model": model,
        "prompt_version": prompt.PROMPT_VERSION,
        "git_sha": os.environ.get("GITHUB_SHA") or _git_sha(),
        "questions": len(results),
        "scored": len(scored),
        "errors": len(results) - len(scored),
        "correct": correct,
        "accuracy": round(correct / len(scored), 4) if scored else None,
    }


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def to_markdown(summary: dict, results: list[dict]) -> str:
    acc = f"{summary['accuracy']:.1%}" if summary["accuracy"] is not None else "n/a"
    lines = [
        f"## Eval: {summary['correct']}/{summary['scored']} correct ({acc})",
        "",
        f"model `{summary['model']}` · prompt `{summary['prompt_version']}` · "
        f"commit `{summary['git_sha'][:7]}` · {summary['date']} · errors excluded: "
        f"{summary['errors']}",
        "",
        "| id | type | result | status | question | reason |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        mark = {True: "✅", False: "❌", None: "⚠️ error"}[r["correct"]]
        reason = (r.get("reason") or "").replace("|", "/")[:80]
        lines.append(f"| {r['id']} | {r['type']} | {mark} | {r['status']} | "
                     f"{r['question'].replace('|', '/')} | {reason} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true", help="rebuild DATABASE_URL with seed data first")
    ap.add_argument("--delay", type=float, default=float(os.environ.get("EVAL_DELAY_SECONDS", 13)))
    ap.add_argument("--only", nargs="*", help="question ids to run (default: all)")
    args = ap.parse_args()

    admin_url, chat_password = os.environ.get("DATABASE_URL"), os.environ.get("CHAT_DB_PASSWORD")
    if not admin_url or not chat_password:
        print("DATABASE_URL and CHAT_DB_PASSWORD must be set", file=sys.stderr)
        return 1
    try:
        llm = GeminiLLM()  # check the key/model before touching the database
    except LLMUnavailable as e:
        print(f"Cannot run the eval: {e}", file=sys.stderr)
        return 1
    if args.load:
        load_seed_data(admin_url, chat_password)
    os.environ["CHAT_DATABASE_URL"] = chat_url(admin_url, chat_password)
    db.open_pool()
    db.chat_pool.wait(timeout=30)

    questions = yaml.safe_load(QUESTIONS.read_text())
    if args.only:
        questions = [q for q in questions if q["id"] in args.only]
    try:
        results = run(questions, llm, args.delay)
    except EvalAborted as e:
        print(f"\nEVAL ABORTED: {e}", file=sys.stderr)
        return 1
    finally:
        db.close_pool()
    summary = summarise(results, llm.model)

    (HERE / "results.json").write_text(json.dumps({"summary": summary, "results": results},
                                                  indent=2, default=str))
    md = to_markdown(summary, results)
    (HERE / "results.md").write_text(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    print("\n" + md)
    if summary["scored"] == 0:  # all transient errors: don't let the workflow look green
        print("No question could be scored (all errored).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
