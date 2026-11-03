"""Typed, validated competition-rule loading and durable round snapshots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import tomllib

RULES_PATH = Path(__file__).resolve().parent.parent / "competition_rules.toml"
SUPPORTED_TIMER_MODES = {"CONTINUOUS", "PAUSE_DURING_HANDOVER"}
SUPPORTED_R1B_TURN_COUNT = 4  # coupled to player slots and DB constraints
SUPPORTED_R1A_ADVANCE_MODES = {"MANUAL", "AUTO"}


class CompetitionRulesError(RuntimeError):
    pass


@dataclass(frozen=True)
class R1ARules:
    advance_mode: str
    auto_advance_delay_ms: int
    decay_enabled: bool
    question_count: int
    question_duration_ms: int
    full_score_until_ms: int
    decay_end_ms: int
    maximum_correct_score: int
    minimum_correct_score: int
    wrong_answer_score: int
    unanswered_score: int


@dataclass(frozen=True)
class R1BRules:
    turn_count: int
    total_answering_budget_ms: int
    correct_score: int
    wrong_score: int
    skip_unanswered_score: int
    default_timer_mode: str


@dataclass(frozen=True)
class CompetitionRules:
    version: str
    r1a: R1ARules
    r1b: R1BRules

    def public_dict(self) -> dict:
        return {"version": self.version, "r1a": asdict(self.r1a), "r1b": asdict(self.r1b)}

    def snapshot_json(self) -> str:
        return json.dumps(self.public_dict(), sort_keys=True, separators=(",", ":"))


def _integer(section: dict, name: str) -> int:
    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompetitionRulesError(f"competition_rules.toml: {name} must be an integer")
    return value


def _boolean(section: dict, name: str) -> bool:
    value = section.get(name)
    if not isinstance(value, bool):
        raise CompetitionRulesError(f"competition_rules.toml: {name} must be a boolean")
    return value


def _validate(rules: CompetitionRules) -> None:
    a, b = rules.r1a, rules.r1b
    if not rules.version.strip():
        raise CompetitionRulesError("competition_rules.toml: version must be non-empty")
    if a.question_count <= 0 or a.question_duration_ms <= 0:
        raise CompetitionRulesError("competition_rules.toml: R1A question count and duration must be positive")
    if a.advance_mode not in SUPPORTED_R1A_ADVANCE_MODES:
        raise CompetitionRulesError("competition_rules.toml: r1a.advance_mode must be MANUAL or AUTO")
    if a.auto_advance_delay_ms < 0:
        raise CompetitionRulesError("competition_rules.toml: r1a.auto_advance_delay_ms must be nonnegative")
    if not 0 <= a.full_score_until_ms < a.decay_end_ms <= a.question_duration_ms:
        raise CompetitionRulesError("competition_rules.toml: require 0 <= full_score_until_ms < decay_end_ms <= question_duration_ms")
    if not a.maximum_correct_score >= a.minimum_correct_score >= 0:
        raise CompetitionRulesError("competition_rules.toml: require maximum_correct_score >= minimum_correct_score >= 0")
    if b.turn_count <= 0 or b.total_answering_budget_ms <= 0:
        raise CompetitionRulesError("competition_rules.toml: R1B turn count and budget must be positive")
    if b.turn_count != SUPPORTED_R1B_TURN_COUNT:
        raise CompetitionRulesError(
            f"competition_rules.toml: r1b.turn_count must be {SUPPORTED_R1B_TURN_COUNT}; "
            "relay turns are currently coupled to four assigned player slots"
        )
    if b.default_timer_mode not in SUPPORTED_TIMER_MODES:
        raise CompetitionRulesError("competition_rules.toml: unsupported R1B default_timer_mode")
    for value in (*asdict(a).values(), *[v for k, v in asdict(b).items() if k != "default_timer_mode"]):
        if isinstance(value, float) and not math.isfinite(value):
            raise CompetitionRulesError("competition_rules.toml: numeric values must be finite")


def rules_from_dict(data: dict) -> CompetitionRules:
    try:
        a, b = data["r1a"], data["r1b"]
        # Snapshots written before these controls existed deliberately retain
        # the historical manual/decaying behaviour.
        a = {"advance_mode": "MANUAL", "auto_advance_delay_ms": 8000,
             "decay_enabled": True, **a}
        rules = CompetitionRules(
            version=str(data["version"]),
            r1a=R1ARules(
                advance_mode=str(a["advance_mode"]),
                auto_advance_delay_ms=_integer(a, "auto_advance_delay_ms"),
                decay_enabled=_boolean(a, "decay_enabled"),
                **{name: _integer(a, name) for name in R1ARules.__annotations__
                   if name not in {"advance_mode", "auto_advance_delay_ms", "decay_enabled"}},
            ),
            r1b=R1BRules(**{
                **{name: _integer(b, name) for name in R1BRules.__annotations__ if name != "default_timer_mode"},
                "default_timer_mode": str(b["default_timer_mode"]),
            }),
        )
    except (KeyError, TypeError) as exc:
        raise CompetitionRulesError(f"competition_rules.toml: missing or invalid field: {exc}") from exc
    _validate(rules)
    return rules


@lru_cache(maxsize=1)
def configured_rules() -> CompetitionRules:
    try:
        with RULES_PATH.open("rb") as handle:
            return rules_from_dict(tomllib.load(handle))
    except FileNotFoundError as exc:
        raise CompetitionRulesError(f"Required competition rules file is missing: {RULES_PATH}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CompetitionRulesError(f"Invalid competition_rules.toml: {exc}") from exc


def round_rules(round_obj) -> CompetitionRules:
    snapshot = getattr(round_obj, "rules_snapshot", None)
    if snapshot:
        try:
            return rules_from_dict(json.loads(snapshot))
        except (json.JSONDecodeError, TypeError) as exc:
            raise CompetitionRulesError(f"Round {round_obj.code} has an invalid rules snapshot") from exc
    return configured_rules()


# Fail fast during application/module startup, before accepting requests.
configured_rules()
