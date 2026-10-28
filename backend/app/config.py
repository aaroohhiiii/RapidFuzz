from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    NCBI_API_KEY: str = os.getenv("NCBI_API_KEY", "")
    NCBI_TOOL: str = os.getenv("NCBI_TOOL", "mini-rayyan")
    NCBI_EMAIL: str = os.getenv("NCBI_EMAIL", "dev@example.com")
    USE_CACHE: bool = os.getenv("USE_CACHE", "false").lower() in {"1", "true", "yes", "on"}
    MAX_RECORDS: int = int(os.getenv("MAX_RECORDS", "250"))

    # Deduplication v1. All tunable scoring parameters live here for reporting.
    DEDUP_RULE_VERSION: str = os.getenv("DEDUP_RULE_VERSION", "dedup-v1")
    DUP_HIGH: float = float(os.getenv("DUP_HIGH", "0.93"))
    DUP_LOW: float = float(os.getenv("DUP_LOW", "0.85"))
    DEDUP_TITLE_WEIGHT: float = float(os.getenv("DEDUP_TITLE_WEIGHT", "0.82"))
    DEDUP_YEAR_WEIGHT: float = float(os.getenv("DEDUP_YEAR_WEIGHT", "0.06"))
    DEDUP_AUTHOR_WEIGHT: float = float(os.getenv("DEDUP_AUTHOR_WEIGHT", "0.06"))
    DEDUP_JOURNAL_WEIGHT: float = float(os.getenv("DEDUP_JOURNAL_WEIGHT", "0.03"))
    DEDUP_PAGES_WEIGHT: float = float(os.getenv("DEDUP_PAGES_WEIGHT", "0.03"))
    DEDUP_NEAR_TITLE: float = float(os.getenv("DEDUP_NEAR_TITLE", "0.90"))
    DEDUP_ABSTRACT_LOW: float = float(os.getenv("DEDUP_ABSTRACT_LOW", "0.55"))
    DEDUP_ABSTRACT_MIN_CHARS: int = int(os.getenv("DEDUP_ABSTRACT_MIN_CHARS", "100"))
    DEDUP_CONFERENCE_TYPES: tuple[str, ...] = (
        "congress",
        "meeting abstract",
        "conference",
        "abstract",
    )

    # Screening rule versions are independent so one rule can be rerun alone.
    SCREEN_RUN_VERSION: str = os.getenv("SCREEN_RUN_VERSION", "screen-v1")
    SCREEN_DECISION_VERSION: str = os.getenv("SCREEN_DECISION_VERSION", "screen-decision-v1")
    SCREEN_NON_HUMAN_VERSION: str = os.getenv("SCREEN_NON_HUMAN_VERSION", "non-human-v1")
    SCREEN_NON_ENGLISH_VERSION: str = os.getenv("SCREEN_NON_ENGLISH_VERSION", "non-english-v1")
    SCREEN_SIMULATION_VERSION: str = os.getenv("SCREEN_SIMULATION_VERSION", "simulation-v1")
    SCREEN_CASE_REPORT_VERSION: str = os.getenv("SCREEN_CASE_REPORT_VERSION", "case-report-v1")
    SCREEN_PEDIATRIC_VERSION: str = os.getenv("SCREEN_PEDIATRIC_VERSION", "pediatric-only-v1")
    SCREEN_NON_PRIMARY_VERSION: str = os.getenv("SCREEN_NON_PRIMARY_VERSION", "non-primary-v1")
    SCREEN_HUMAN_ADULT_VERSION: str = os.getenv("SCREEN_HUMAN_ADULT_VERSION", "human-adult-v1")
    SCREEN_TRIAL_DESIGN_VERSION: str = os.getenv("SCREEN_TRIAL_DESIGN_VERSION", "trial-design-v1")
    SCREEN_TOPIC_MATCH_VERSION: str = os.getenv("SCREEN_TOPIC_MATCH_VERSION", "topic-match-v1")
    SCREEN_LANGUAGE_MIN_CHARS: int = int(os.getenv("SCREEN_LANGUAGE_MIN_CHARS", "50"))
    SCREEN_REVIEWS_AS_EXCLUDE: bool = os.getenv("SCREEN_REVIEWS_AS_EXCLUDE", "false").lower() in {
        "1", "true", "yes", "on"
    }
    SCREEN_ENGLISH_CODES: tuple[str, ...] = ("en", "eng", "english")
    SCREEN_ANIMAL_TERMS: tuple[str, ...] = (
        "mice", "mouse", "murine", "rat", "rats", "rodent", "in vitro",
        "zebrafish", "cell line", "preclinical",
    )
    SCREEN_HUMAN_CUES: tuple[str, ...] = ("patient", "patients", "participant", "participants", "volunteer", "volunteers")
    SCREEN_ADULT_CUES: tuple[str, ...] = ("adult", "adults", "aged 18", "18 years", "men and women")
    SCREEN_PEDIATRIC_CUES: tuple[str, ...] = (
        "pediatric", "paediatric", "children", "child", "adolescent", "adolescents",
        "infant", "infants", "teenager", "teenagers",
    )
    SCREEN_SIMULATION_TERMS: tuple[str, ...] = (
        "simulation", "simulated", "in silico", "markov model", "microsimulation",
        "cost-effectiveness model", "modelling study", "modeling study", "monte carlo",
    )
    SCREEN_SIMULATION_EDUCATION_GUARDS: tuple[str, ...] = (
        "education", "educational", "training", "medical student", "nursing student",
        "standardized patient", "standardised patient",
    )
    SCREEN_CASE_PATTERNS: tuple[str, ...] = ("a case of", "case report", "we report a patient")
    SCREEN_NON_PRIMARY_TYPES: tuple[str, ...] = (
        "editorial", "letter", "comment", "news", "published erratum",
    )
    SCREEN_REVIEW_TYPES: tuple[str, ...] = ("review", "meta-analysis", "meta analysis")
    SCREEN_TRIAL_TYPES: tuple[str, ...] = ("randomized controlled trial", "clinical trial")
    SCREEN_TRIAL_TERMS: tuple[str, ...] = (
        "randomized", "randomised", "placebo", "double-blind", "double blind",
        "versus", "compared with",
    )
    SCREEN_INTERVENTION_TERMS: tuple[str, ...] = (
        "semaglutide", "liraglutide", "tirzepatide", "glp-1", "glp 1",
        "glucagon-like peptide-1", "glucagon like peptide 1",
    )

    # Offline PICO rules and display-only heuristic confidence values.
    PICO_RULE_VERSION: str = os.getenv("PICO_RULE_VERSION", "pico-rules-v1")
    PICO_MIN_SPAN: int = int(os.getenv("PICO_MIN_SPAN", "3"))
    PICO_MAX_SPAN: int = int(os.getenv("PICO_MAX_SPAN", "200"))
    PICO_LABEL_PRIORITY: tuple[str, ...] = ("I", "C", "P", "O")
    PICO_INTERVENTION_TERMS: tuple[str, ...] = (
        "semaglutide", "liraglutide", "tirzepatide", "glp-1 receptor agonist",
        "glp 1 receptor agonist", "glucagon-like peptide-1 receptor agonist",
    )
    PICO_COMPARATOR_TERMS: tuple[str, ...] = (
        "placebo", "control group", "usual care", "standard of care", "sham",
        "active comparator", "lifestyle intervention alone",
    )
    PICO_COMPARATOR_CUES: tuple[str, ...] = (
        "versus", "vs", "compared with", "compared to",
    )
    PICO_OUTCOME_TERMS: tuple[str, ...] = (
        "body weight", "weight loss", "bmi", "body mass index", "waist circumference",
        "hba1c", "glycated hemoglobin", "change in weight", "weight reduction",
        "percent weight change", "percentage weight change",
    )
    PICO_OUTCOME_CUES: tuple[str, ...] = (
        "primary outcome", "primary endpoint", "secondary outcome", "secondary endpoint",
        "the main outcome", "change in", "reduction in", "improvement in", "measured", "assessed",
    )
    PICO_POPULATION_CUES: tuple[str, ...] = (
        "patients with", "adults with", "participants", "subjects", "aged",
        "individuals with", "women", "men", "children", "volunteers",
    )


settings = Settings()
