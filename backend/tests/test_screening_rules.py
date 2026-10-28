from __future__ import annotations

import pytest

from app.services.screening_rules import (
    DecisionInput,
    ScreeningRecord,
    combine_decision,
    evaluate_rule,
)


@pytest.mark.parametrize(
    ("code", "hit", "no_hit", "unknown"),
    [
        (
            "non_human",
            ScreeningRecord(1, mesh_terms=("Animals",)),
            ScreeningRecord(2, mesh_terms=("Humans", "Adult")),
            ScreeningRecord(3),
        ),
        (
            "non_english",
            ScreeningRecord(1, language="spa"),
            ScreeningRecord(2, language="eng"),
            ScreeningRecord(3, title="Short"),
        ),
        (
            "simulation",
            ScreeningRecord(1, title="A Monte Carlo simulation"),
            ScreeningRecord(2, title="Simulated patient education for medical students"),
            ScreeningRecord(3),
        ),
        (
            "case_report",
            ScreeningRecord(1, pub_types=("Case Reports",)),
            ScreeningRecord(2, title="A randomized trial", pub_types=("Clinical Trial",)),
            ScreeningRecord(3),
        ),
        (
            "pediatric_only",
            ScreeningRecord(1, mesh_terms=("Child", "Humans")),
            ScreeningRecord(2, mesh_terms=("Adult", "Humans")),
            ScreeningRecord(3),
        ),
        (
            "non_primary",
            ScreeningRecord(1, pub_types=("Editorial",)),
            ScreeningRecord(2, pub_types=("Journal Article",)),
            ScreeningRecord(3),
        ),
        (
            "human_adult",
            ScreeningRecord(1, mesh_terms=("Humans", "Adult")),
            ScreeningRecord(2, mesh_terms=("Animals",)),
            ScreeningRecord(3),
        ),
        (
            "trial_design",
            ScreeningRecord(1, pub_types=("Randomized Controlled Trial",)),
            ScreeningRecord(2, abstract="This observational cohort followed adults over time."),
            ScreeningRecord(3),
        ),
        (
            "topic_match",
            ScreeningRecord(1, title="Semaglutide and weight loss"),
            ScreeningRecord(2, title="Diet and exercise for weight loss"),
            ScreeningRecord(3),
        ),
    ],
)
def test_each_rule_has_hit_no_hit_and_unknown_fixtures(code, hit, no_hit, unknown):
    assert evaluate_rule(code, hit).result == "hit"
    assert evaluate_rule(code, no_hit).result == "no_hit"
    assert evaluate_rule(code, unknown).result == "unknown"


def test_non_human_keyword_fallback_requires_no_human_cue():
    assert evaluate_rule("non_human", ScreeningRecord(1, abstract="Preclinical mice study")).result == "hit"
    result = evaluate_rule("non_human", ScreeningRecord(2, abstract="Mice and adult patients were compared"))
    assert result.result == "unknown"


def test_decision_excludes_with_all_active_hit_reasons():
    decision, reasons = combine_decision([
        DecisionInput("simulation", "exclude", True, "hit"),
        DecisionInput("case_report", "exclude", True, "hit"),
        DecisionInput("topic_match", "include", True, "hit"),
    ], has_abstract=True)
    assert decision == "exclude"
    assert reasons == ["case_report", "simulation"]


def test_decision_includes_only_when_every_active_include_hits():
    decision, reasons = combine_decision([
        DecisionInput("non_human", "exclude", True, "no_hit"),
        DecisionInput("human_adult", "include", True, "hit"),
        DecisionInput("trial_design", "include", True, "hit"),
        DecisionInput("topic_match", "include", True, "hit"),
        DecisionInput("unused", "include", False, "unknown"),
    ], has_abstract=True)
    assert (decision, reasons) == ("include", [])


def test_decision_maybe_lists_unconfirmed_and_unknown_inputs():
    decision, reasons = combine_decision([
        DecisionInput("human_adult", "include", True, "unknown"),
        DecisionInput("trial_design", "include", True, "no_hit"),
        DecisionInput("topic_match", "include", True, "hit"),
    ], has_abstract=True)
    assert decision == "maybe"
    assert reasons == [
        "not_confirmed:human_adult",
        "not_confirmed:trial_design",
        "unknown:human_adult",
    ]


def test_missing_abstract_and_review_default_force_maybe_unless_excluded():
    all_positive = [
        DecisionInput("human_adult", "include", True, "hit"),
        DecisionInput("trial_design", "include", True, "hit"),
        DecisionInput("topic_match", "include", True, "hit"),
    ]
    assert combine_decision(all_positive, has_abstract=False)[0] == "maybe"
    review = all_positive + [
        DecisionInput("non_primary", "exclude", True, "unknown", "review_maybe:review")
    ]
    assert combine_decision(review, has_abstract=True)[0] == "maybe"
