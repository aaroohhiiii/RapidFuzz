from app.models import PicoHuman, PicoMachine
from app.services.pico_effective import merge_effective_spans


def test_effective_merge_removes_edits_and_adds_in_one_function():
    abstract = "Adults received semaglutide or placebo. Weight loss was measured."
    intervention_start = abstract.index("semaglutide")
    placebo_start = abstract.index("placebo")
    outcome_start = abstract.index("Weight loss")
    machine = [
        PicoMachine(
            id=1, record_id=1, label="I", start_char=intervention_start,
            end_char=intervention_start + len("semaglutide"), span_text="semaglutide",
            method="rules", confidence=0.95, rule_version="pico-rules-v1", run_id=1,
        ),
        PicoMachine(
            id=2, record_id=1, label="C", start_char=placebo_start,
            end_char=placebo_start + len("placebo"), span_text="placebo",
            method="rules", confidence=0.95, rule_version="pico-rules-v1", run_id=1,
        ),
    ]
    actions = [
        PicoHuman(id=1, record_id=1, action="remove", machine_span_id=1, reviewer="r"),
        PicoHuman(
            id=2, record_id=1, action="edit", machine_span_id=2, label="C",
            start_char=placebo_start, end_char=placebo_start + len("placebo"),
            span_text="placebo", reviewer="r",
        ),
        PicoHuman(
            id=3, record_id=1, action="add", label="O",
            start_char=outcome_start, end_char=outcome_start + len("Weight loss"),
            span_text="Weight loss", reviewer="r",
        ),
    ]

    spans = merge_effective_spans(abstract, machine, actions)
    assert [(span.source, span.label, span.span_text) for span in spans] == [
        ("human", "C", "placebo"),
        ("human", "O", "Weight loss"),
    ]


def test_effective_merge_drops_stale_offsets_after_abstract_change():
    machine = [
        PicoMachine(
            id=1, record_id=1, label="I", start_char=0, end_char=11,
            span_text="semaglutide", method="rules", confidence=0.9,
            rule_version="pico-rules-v1", run_id=1,
        )
    ]
    assert merge_effective_spans("changed text", machine, []) == []
