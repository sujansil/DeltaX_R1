"""Shared pre-start configuration validation for Round 1."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Question, QuestionOption, R1BTimerMode, Round


def finite_nonnegative(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and value >= 0


async def validate_round_configuration(db: AsyncSession, round_obj: Round) -> list[dict]:
    code = round_obj.code
    required = 23 if code == "R1A" else 4
    issues: list[dict] = []
    questions = (await db.scalars(select(Question).where(
        Question.round_code == code, Question.enabled.is_(True)
    ).order_by(Question.position))).all()
    positions = [q.position for q in questions]
    if positions != list(range(1, required + 1)):
        issues.append({"code": "QUESTION_POSITIONS", "field": "questions",
                       "message": f"{code} needs exactly enabled questions 1 through {required}"})
    if code == "R1A":
        for q in questions:
            options = (await db.scalars(select(QuestionOption).where(
                QuestionOption.question_id == q.id
            ).order_by(QuestionOption.position))).all()
            option_ids = {o.id for o in options}
            if q.answer_type != "MCQ":
                issues.append({"code": "ANSWER_TYPE", "field": f"question.{q.id}.answer_type",
                               "message": f"R1A question {q.position} must be MCQ"})
            if not options:
                issues.append({"code": "OPTIONS_REQUIRED", "field": f"question.{q.id}.options",
                               "message": f"R1A question {q.position} needs at least one option"})
            for option in options:
                if not (option.option_text and option.option_text.strip()) and not option.image_url:
                    issues.append({"code": "OPTION_CONTENT_REQUIRED",
                                   "field": f"option.{option.id}",
                                   "message": f"Option {option.position} for question {q.position} needs text or an image"})
            if q.correct_option_id is None:
                issues.append({"code": "CORRECT_OPTION_REQUIRED",
                               "field": f"question.{q.id}.correct_option_id",
                               "message": f"R1A question {q.position} needs a correct option"})
            elif q.correct_option_id not in option_ids:
                issues.append({"code": "CORRECT_OPTION_OWNERSHIP",
                               "field": f"question.{q.id}.correct_option_id",
                               "message": f"Correct option for question {q.position} belongs to another question"})
    else:
        if round_obj.r1b_timer_mode not in {m.value for m in R1BTimerMode}:
            issues.append({"code": "TIMER_MODE", "field": "timer_mode",
                           "message": "R1B timer mode is invalid"})
        for q in questions:
            if q.answer_type != "NUMERIC":
                issues.append({"code": "ANSWER_TYPE", "field": f"question.{q.id}.answer_type",
                               "message": f"R1B question {q.position} must be NUMERIC"})
            if q.correct_numeric is None or not q.correct_numeric.is_finite():
                issues.append({"code": "NUMERIC_ANSWER", "field": f"question.{q.id}.correct_numeric",
                               "message": f"R1B question {q.position} needs a finite numerical answer"})
            if not finite_nonnegative(q.tolerance_abs):
                issues.append({"code": "ABS_TOLERANCE", "field": f"question.{q.id}.tolerance_abs",
                               "message": f"R1B question {q.position} needs a finite nonnegative absolute tolerance"})
            if not finite_nonnegative(q.tolerance_rel):
                issues.append({"code": "REL_TOLERANCE", "field": f"question.{q.id}.tolerance_rel",
                               "message": f"R1B question {q.position} needs a finite nonnegative relative tolerance"})
    return issues
