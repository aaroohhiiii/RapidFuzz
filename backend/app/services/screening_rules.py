"""Pure Step 5 criterion rules and combined-decision logic."""

from __future__ import annotations

from dataclasses import dataclass
import re

from langdetect import DetectorFactory, LangDetectException, detect

from app.config import settings


DetectorFactory.seed = 0


@dataclass(frozen=True)
class ScreeningRecord:
    """Record metadata consumed by screening rules."""

    id: int
    title: str | None = None
    abstract: str | None = None
    language: str | None = None
    pub_types: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleResult:
    """One criterion result and human-readable supporting evidence."""

    result: str
    evidence: str | None = None


@dataclass(frozen=True)
class DecisionInput:
    """One stored criterion result used by the combination function."""

    code: str
    kind: str
    active: bool
    result: str
    evidence: str | None = None


RULE_CODES = (
    "non_human", "non_english", "simulation", "case_report", "pediatric_only",
    "non_primary", "human_adult", "trial_design", "topic_match",
)


def evaluate_rule(code: str, record: ScreeningRecord) -> RuleResult:
    """Evaluate one named rule against one record."""

    evaluators = {
        "non_human": _non_human,
        "non_english": _non_english,
        "simulation": _simulation,
        "case_report": _case_report,
        "pediatric_only": _pediatric_only,
        "non_primary": _non_primary,
        "human_adult": _human_adult,
        "trial_design": _trial_design,
        "topic_match": _topic_match,
    }
    try:
        return evaluators[code](record)
    except KeyError as exc:
        raise ValueError(f"Unknown screening rule: {code}") from exc


def combine_decision(inputs: list[DecisionInput], has_abstract: bool) -> tuple[str, list[str]]:
    """Combine active criterion results into Include, Exclude, or Maybe."""

    active = [item for item in inputs if item.active]
    exclude_hits = sorted(item.code for item in active if item.kind == "exclude" and item.result == "hit")
    if exclude_hits:
        return "exclude", exclude_hits

    include_results = [item for item in active if item.kind == "include"]
    unknown = sorted(item.code for item in active if item.result == "unknown")
    review_maybe = any(
        item.code == "non_primary"
        and item.result == "unknown"
        and (item.evidence or "").startswith("review_maybe:")
        for item in active
    )
    if has_abstract and include_results and all(item.result == "hit" for item in include_results) and not review_maybe:
        return "include", []

    reasons: list[str] = []
    for item in sorted(include_results, key=lambda value: value.code):
        if item.result != "hit":
            reasons.append(f"not_confirmed:{item.code}")
    reasons.extend(f"unknown:{code}" for code in unknown)
    if not has_abstract:
        reasons.append("missing_abstract")
    return "maybe", list(dict.fromkeys(reasons))


def rule_versions() -> dict[str, str]:
    """Return the independently configurable version for each rule."""

    return {
        "non_human": settings.SCREEN_NON_HUMAN_VERSION,
        "non_english": settings.SCREEN_NON_ENGLISH_VERSION,
        "simulation": settings.SCREEN_SIMULATION_VERSION,
        "case_report": settings.SCREEN_CASE_REPORT_VERSION,
        "pediatric_only": settings.SCREEN_PEDIATRIC_VERSION,
        "non_primary": settings.SCREEN_NON_PRIMARY_VERSION,
        "human_adult": settings.SCREEN_HUMAN_ADULT_VERSION,
        "trial_design": settings.SCREEN_TRIAL_DESIGN_VERSION,
        "topic_match": settings.SCREEN_TOPIC_MATCH_VERSION,
    }


def _non_human(record: ScreeningRecord) -> RuleResult:
    mesh = _normalized(record.mesh_terms)
    if mesh:
        animals = _has_mesh(mesh, ("animals",))
        humans = _has_mesh(mesh, ("humans",))
        if animals and not humans:
            return RuleResult("hit", "MeSH: Animals without Humans")
        return RuleResult("no_hit", "MeSH does not indicate animal-only research")
    text = _text(record)
    animal = _first_term(text, settings.SCREEN_ANIMAL_TERMS)
    human = _first_term(text, settings.SCREEN_HUMAN_CUES + settings.SCREEN_ADULT_CUES)
    if animal and not human:
        return RuleResult("hit", f"keyword:{animal}")
    return RuleResult("unknown", "MeSH missing; animal-only status not confirmed")


def _non_english(record: ScreeningRecord) -> RuleResult:
    if record.language and record.language.strip():
        language = record.language.strip().casefold()
        if language in settings.SCREEN_ENGLISH_CODES:
            return RuleResult("no_hit", f"language:{record.language}")
        return RuleResult("hit", f"language:{record.language}")
    text = _text(record)
    if len(text) < settings.SCREEN_LANGUAGE_MIN_CHARS:
        return RuleResult("unknown", "language missing; text too short to detect")
    try:
        language = detect(text)
    except LangDetectException:
        return RuleResult("unknown", "language detection failed")
    return RuleResult("no_hit" if language == "en" else "hit", f"detected_language:{language}")


def _simulation(record: ScreeningRecord) -> RuleResult:
    text = _text(record)
    if not text:
        return RuleResult("unknown", "title and abstract missing")
    match = _first_term(text, settings.SCREEN_SIMULATION_TERMS)
    if not match:
        return RuleResult("no_hit", "no simulation keyword")
    guarded_simulated_patient = (
        match == "simulated"
        and "simulated patient" in text
        and _first_term(text, settings.SCREEN_SIMULATION_EDUCATION_GUARDS)
    )
    if guarded_simulated_patient:
        return RuleResult("no_hit", "guarded educational simulated-patient wording")
    return RuleResult("hit", f"keyword:{match}")


def _case_report(record: ScreeningRecord) -> RuleResult:
    pub_types = _normalized(record.pub_types)
    match = _first_term(" ".join(pub_types), ("case reports", "case report"))
    if match:
        return RuleResult("hit", f"publication_type:{match}")
    title = (record.title or "").casefold()
    match = _first_term(title, settings.SCREEN_CASE_PATTERNS)
    if match:
        return RuleResult("hit", f"title_pattern:{match}")
    if pub_types or title:
        return RuleResult("no_hit", "no case-report metadata or title pattern")
    return RuleResult("unknown", "publication type and title missing")


def _pediatric_only(record: ScreeningRecord) -> RuleResult:
    mesh = _normalized(record.mesh_terms)
    pediatric_mesh = _has_mesh(mesh, ("child", "infant", "adolescent"))
    adult_mesh = _has_mesh(mesh, ("adult", "adults"))
    if pediatric_mesh and not adult_mesh:
        return RuleResult("hit", "MeSH pediatric term without Adult")
    if adult_mesh:
        return RuleResult("no_hit", "MeSH includes Adult")
    text = _text(record)
    pediatric = _first_term(text, settings.SCREEN_PEDIATRIC_CUES)
    adult = _first_term(text, settings.SCREEN_ADULT_CUES)
    if pediatric and not adult:
        return RuleResult("hit", f"keyword:{pediatric}")
    if mesh:
        return RuleResult("no_hit", "MeSH does not indicate pediatric-only population")
    return RuleResult("unknown", "pediatric-only status not confirmed")


def _non_primary(record: ScreeningRecord) -> RuleResult:
    pub_types = _normalized(record.pub_types)
    if not pub_types:
        return RuleResult("unknown", "publication type missing")
    joined = " ".join(pub_types)
    match = _first_term(joined, settings.SCREEN_NON_PRIMARY_TYPES)
    if match:
        return RuleResult("hit", f"publication_type:{match}")
    review = _first_term(joined, settings.SCREEN_REVIEW_TYPES)
    if review and settings.SCREEN_REVIEWS_AS_EXCLUDE:
        return RuleResult("hit", f"publication_type:{review}")
    if review:
        return RuleResult("unknown", f"review_maybe:{review}")
    return RuleResult("no_hit", "publication type is not configured as non-primary")


def _human_adult(record: ScreeningRecord) -> RuleResult:
    mesh = _normalized(record.mesh_terms)
    humans = _has_mesh(mesh, ("humans",))
    adults = _has_mesh(mesh, ("adult", "adults"))
    if humans and adults:
        return RuleResult("hit", "MeSH: Humans and Adult")
    adult = _first_term((record.abstract or "").casefold(), settings.SCREEN_ADULT_CUES)
    if adult:
        return RuleResult("hit", f"abstract_keyword:{adult}")
    if _has_mesh(mesh, ("animals",)) and not humans:
        return RuleResult("no_hit", "MeSH indicates Animals without Humans")
    return RuleResult("unknown", "adult human population not confirmed")


def _trial_design(record: ScreeningRecord) -> RuleResult:
    pub_types = " ".join(_normalized(record.pub_types))
    match = _first_term(pub_types, settings.SCREEN_TRIAL_TYPES)
    if match:
        return RuleResult("hit", f"publication_type:{match}")
    text = _text(record)
    match = _first_term(text, settings.SCREEN_TRIAL_TERMS)
    if match:
        return RuleResult("hit", f"keyword:{match}")
    if not record.abstract:
        return RuleResult("unknown", "trial design not confirmed; abstract missing")
    return RuleResult("no_hit", "no trial publication type or keyword")


def _topic_match(record: ScreeningRecord) -> RuleResult:
    text = _text(record)
    match = _first_term(text, settings.SCREEN_INTERVENTION_TERMS)
    if match:
        return RuleResult("hit", f"intervention:{match}")
    if not text:
        return RuleResult("unknown", "title and abstract missing")
    return RuleResult("no_hit", "no configured GLP-1 intervention term")


def _text(record: ScreeningRecord) -> str:
    return " ".join(value for value in (record.title, record.abstract) if value).casefold()


def _normalized(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(value).strip().casefold() for value in values if value)


def _has_mesh(values: tuple[str, ...], terms: tuple[str, ...]) -> bool:
    return any(any(term == value or term in value.split("/") for term in terms) for value in values)


def _first_term(text: str, terms: tuple[str, ...]) -> str | None:
    return next(
        (term for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)),
        None,
    )
