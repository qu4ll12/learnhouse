#!/usr/bin/env python3
"""
Import the MATHCDT exercise bank as a draft LearnHouse course (dev test data).

Mapping: whole bank -> 1 course; each category -> 1 chapter holding a worked
examples page (equation + solution_path) and an auto-graded assignment (one
math_steps task per exercise: the learner solves step by step, each step is
checked server-side).

Run from the apps/api directory (uses the same DB as the running API):
    python scripts/import_mathcdt.py <path>/exercise_bank_validation.json           # dry run
    python scripts/import_mathcdt.py <path>/exercise_bank_validation.json --apply   # write

Re-running the same spec replays instead of duplicating (idempotency_key).
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HINT = "Viết từng bước biến đổi cho đến khi có kết quả cuối (ví dụ x=5; vô nghiệm: 0=4; vô số nghiệm: 0=0)."


def worked_latex(ex: dict) -> str:
    lines = [ex["equation_text"], *ex["solution_path"]]
    return r"\begin{aligned}&" + r"\\ \Leftrightarrow\ &".join(lines) + r"\end{aligned}"


def build_spec(exercises: list[dict]) -> dict:
    by_category: dict[str, list[dict]] = {}
    for ex in exercises:
        by_category.setdefault(ex["category"], []).append(ex)

    chapters = []
    for category, items in by_category.items():
        examples = []
        for i, ex in enumerate(items, 1):
            examples += [
                {"type": "heading", "level": 3, "text": f"Bài {i}"},
                {"type": "math", "latex": worked_latex(ex)},
            ]
        tasks = [
            {
                "type": "math_steps",
                "title": f"Bài {i}: {ex['equation_text']}",
                "equation": ex["equation_text"],
                "explanation": "Đáp án: " + ex["expected_answer"] + " (" + " → ".join(ex["solution_path"]) + ")",
            }
            for i, ex in enumerate(items, 1)
        ]
        chapters.append({
            "key": category,
            "name": category,
            "description": f"{len(items)} bài từ MATHCDT ({category})",
            "activities": [
                {"key": f"{category}-examples", "kind": "page", "name": f"Bài mẫu: {category}", "blocks": examples},
                {
                    "key": f"{category}-check", "kind": "assignment", "name": f"Kiểm tra: {category}",
                    "description": HINT, "auto_grading": True, "allow_retries": True,
                    "show_correct_answers": True, "tasks": tasks,
                },
            ],
        })

    spec = {
        "course": {
            "name": "Phương trình bậc nhất (MATHCDT)",
            "description": f"{len(exercises)} bài phương trình bậc nhất lấy từ ngân hàng bài tập MATHCDT.",
            "tags": ["mathcdt", "test-data"],
        },
        "chapters": chapters,
    }
    # Same input -> same key -> replay; a changed bank builds a new course.
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
    return {"idempotency_key": f"mathcdt-{digest}", **spec}


def self_check():
    ex = {"category": "c", "equation_text": "3x-5=7", "expected_answer": "x=4", "solution_path": ["3x=12", "x=4"]}
    task = build_spec([ex])["chapters"][0]["activities"][1]["tasks"][0]
    assert task["type"] == "math_steps" and task["equation"] == "3x-5=7"


async def run(spec: dict, apply: bool, org_slug: str, username: str):
    from sqlmodel import select

    from src.core.events.database import _async_session_factory
    from src.db.organizations import Organization
    from src.db.users import PublicUser, User
    from src.services.ai.tools import get_mcp_registry, run_tool
    from src.services.ai.tools.base import ToolContext, make_synthetic_request

    async with _async_session_factory() as db:
        org = (await db.exec(select(Organization).where(Organization.slug == org_slug))).first()
        user = (await db.exec(select(User).where(User.username == username))).first()
        if org is None or user is None:
            sys.exit(f"org '{org_slug}' or user '{username}' not found")
        ctx = ToolContext(
            request=make_synthetic_request(), db_session=db, user=PublicUser(**user.model_dump()),
            org=org, org_slug=org.slug, mode="execute", surface="mcp",
            approved=True,  # the operator running this script is the approval
        )
        outcome = await run_tool(get_mcp_registry(), "build_course", {**spec, "dry_run": not apply}, ctx)
    print(outcome.status, outcome.denied_code or "", outcome.reason or "")
    print(json.dumps(outcome.result, ensure_ascii=False, indent=2, default=str))
    return outcome.status == "executed"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bank", help="MATHCDT server/seeds/exercise_bank_validation.json")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--org", default="default")
    parser.add_argument("--user", default="admin")
    args = parser.parse_args()

    self_check()
    with open(args.bank, encoding="utf-8") as f:
        spec = build_spec(json.load(f))
    ok = asyncio.run(run(spec, args.apply, args.org, args.user))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
