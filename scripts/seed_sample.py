"""Editable sample data. Run from project root: .venv/bin/python scripts/seed_sample.py

Requires: alembic upgrade head. Re-running skips existing records.
This script never resets rounds, passwords, sessions or scores.
Edit TEAMS, MCQS and NUMERICAL below to seed your own data.
Image fields accept a /media/... path or hosted image URL.
"""
import asyncio
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Team, Round, Question, QuestionOption
from app.security import hash_password

# Development credentials only. Change these before using real event accounts.
TEAMS = [
    {"code": f"DEMO{i:03}", "name": name, "username": f"team{i:02}",
     "password": f"DemoTeam{i:02}!"}
    for i, name in enumerate(
        ["Number Ninjas", "Decimal Dragons", "Prime Pioneers", "Logic Legends", "Math Mavericks"], 1
    )
]

# Each entry: (question text, four option texts, correct option number starting at 1).
MCQS = [
    ("What is 7 + 8?", ["13", "14", "15", "16"], 3),
    ("What is 12 × 3?", ["24", "36", "48", "15"], 2),
    ("Which number is prime?", ["21", "27", "29", "33"], 3),
    ("What is the square root of 144?", ["10", "11", "12", "14"], 3),
    ("What is 25% of 80?", ["10", "20", "25", "40"], 2),
    ("What comes next: 2, 4, 8, 16, ...?", ["18", "24", "30", "32"], 4),
    ("How many degrees are in a triangle's interior angles?", ["90", "180", "270", "360"], 2),
    ("Solve 3x + 6 = 21.", ["3", "4", "5", "7"], 3),
    ("What is 5 factorial (5!)?", ["25", "60", "100", "120"], 4),
    ("What is the median of 3, 7, 9, 11, 20?", ["7", "9", "10", "11"], 2),
    ("What is the area of a rectangle 6 cm by 4 cm?", ["10 cm²", "20 cm²", "24 cm²", "36 cm²"], 3),
    ("Which fraction equals 0.75?", ["1/4", "1/2", "2/3", "3/4"], 4),
    ("What is 2 to the power 5?", ["10", "16", "25", "32"], 4),
    ("What is the greatest common divisor of 18 and 24?", ["3", "6", "9", "12"], 2),
    ("What is the least common multiple of 4 and 6?", ["8", "10", "12", "24"], 3),
    ("What is the probability of rolling a 6 on a fair six-sided die?", ["1/2", "1/3", "1/6", "1/12"], 3),
    ("What is 15 squared?", ["125", "200", "225", "250"], 3),
    ("How many sides does a regular hexagon have?", ["5", "6", "7", "8"], 2),
    ("What is the average of 10, 20 and 30?", ["15", "20", "25", "30"], 2),
    ("A clock shows 3:00. What is the smaller angle between its hands?", ["30°", "60°", "90°", "180°"], 3),
    ("What is 1000 divided by 25?", ["4", "25", "40", "250"], 3),
    ("If all squares are rectangles, which statement must be true?", ["All rectangles are squares", "Every square is a rectangle", "No square is a rectangle", "All rectangles have equal sides"], 2),
    ("What comes next: 1, 1, 2, 3, 5, 8, ...?", ["10", "11", "12", "13"], 4),
]
# Optional images keyed by question number or (question number, option number).
QUESTION_IMAGES = {}  # Example: {1: "/media/questions/addition.png"}
OPTION_IMAGES = {}    # Example: {(1, 3): "/media/options/fifteen.png"}

NUMERICAL = [
    {"text": "Calculate 12.5 × 3.2.", "answer": "40", "abs_tol": "0"},
    {"text": "A circle has radius 3. Using pi = 3.14, calculate its area.", "answer": "28.26", "abs_tol": "0.01"},
    {"text": "Find the mean of 2.5, 4.75, 6.25 and 8.5.", "answer": "5.5", "abs_tol": "0"},
    {"text": "A price of 250 is reduced by 12.5%. What is the final price?", "answer": "218.75", "abs_tol": "0.01"},
]  # Demo questions; replace with your event's actual relay questions.


async def seed():
    if get_settings().environment != "development":
        raise ValueError("Sample seed is for ENVIRONMENT=development only.")
    if len(MCQS) != 23 or len(NUMERICAL) != 4:
        raise ValueError("Provide exactly 23 MCQs and 4 numerical questions.")
    for _, options, correct in MCQS:
        if not 1 <= correct <= len(options):
            raise ValueError("Correct option number must refer to an existing option.")
    messages = []
    async with SessionLocal() as db:
        async with db.begin():
            # Serialize seed runs without interfering with normal player requests.
            await db.execute(text("SELECT pg_advisory_xact_lock(71012304)"))
            rounds = {}
            for code in ("R1A", "R1B"):
                r = await db.scalar(select(Round).where(Round.code == code))
                if r is None:
                    r = Round(code=code, status="DRAFT")
                    db.add(r)
                rounds[code] = r
            for data in TEAMS:
                existing = await db.scalar(select(Team).where(
                    (Team.code == data["code"]) | (Team.login_username == data["username"])
                ))
                if existing:
                    messages.append(f"SKIP existing team: {data['code']} (credentials unchanged)")
                    continue
                db.add(Team(code=data["code"], name=data["name"],
                            login_username=data["username"],
                            password_hash=hash_password(data["password"])))
                messages.append(f"CREATED {data['code']} | {data['name']} | username={data['username']} | password={data['password']}")
            for code, entries in (("R1A", MCQS), ("R1B", NUMERICAL)):
                added = skipped = 0
                for position, data in enumerate(entries, 1):
                    existing = await db.scalar(select(Question.id).where(
                        Question.round_code == code, Question.position == position))
                    if existing:
                        skipped += 1
                        continue
                    if rounds[code].status != "DRAFT":
                        raise ValueError(f"{code} is not DRAFT; cannot add questions. No changes saved.")
                    if code == "R1A":
                        title, options, correct = data
                        q = Question(round_code=code, position=position,
                                     question_text=title, answer_type="MCQ",
                                     question_image_url=QUESTION_IMAGES.get(position))
                        db.add(q)
                        await db.flush()
                        for option_position, option_text in enumerate(options, 1):
                            option = QuestionOption(question_id=q.id, position=option_position,
                                                    option_text=option_text,
                                                    image_url=OPTION_IMAGES.get((position, option_position)))
                            db.add(option)
                            await db.flush()
                            if option_position == correct:
                                q.correct_option_id = option.id
                    else:
                        answer = Decimal(data["answer"])
                        tolerance = Decimal(data.get("abs_tol", "0"))
                        relative = Decimal(data.get("rel_tol", "0"))
                        if not all(x.is_finite() for x in (answer, tolerance, relative)) or min(tolerance, relative) < 0:
                            raise ValueError("Answers must be finite and tolerances nonnegative.")
                        db.add(Question(round_code=code, position=position,
                                        question_text=data["text"], answer_type="NUMERIC",
                                        question_image_url=data.get("image_url"),
                                        correct_numeric=answer, tolerance_abs=tolerance,
                                        tolerance_rel=relative))
                    added += 1
                messages.append(f"{code}: created {added} questions; skipped {skipped} existing positions.")
    print("\n".join(messages))
    print("Seed committed. Existing round state and admin accounts were preserved.")
    print("Use the admin page to start/open rounds when ready.")


if __name__ == "__main__":
    async def main():
        try:
            await seed()
        finally:
            await engine.dispose()
    asyncio.run(main())

