import json
from pathlib import Path

from app.services.pico_rules import PicoSpan, resolve_overlaps, split_sentences, tag_abstract


def test_sentence_splitter_preserves_offsets_and_safe_abbreviations():
    abstract = "Patients received 2.4 mg semaglutide vs. placebo, e.g. weekly dosing by Smith et al. Results improved by 3.5 kg. Conclusions followed."
    sentences = split_sentences(abstract)
    assert len(sentences) == 3
    assert all(abstract[item.start:item.end] == item.text for item in sentences)
    assert "3.5 kg" in sentences[1].text


def test_structured_cues_produce_exact_offset_spans():
    abstract = "METHODS: Adults with obesity (n = 120) were randomized to semaglutide 2.4 mg or placebo. RESULTS: The primary outcome was change in body weight."
    spans = tag_abstract(abstract)
    assert {span.label for span in spans} == {"P", "I", "C", "O"}
    assert all(abstract[span.start_char:span.end_char] == span.span_text for span in spans)
    assert any(span.label == "I" and span.span_text == "semaglutide 2.4 mg" for span in spans)
    assert any(span.label == "C" and span.span_text == "placebo" for span in spans)


def test_x_or_placebo_tags_placebo_but_never_intervention_as_comparator():
    abstract = "Participants received semaglutide or placebo for 68 weeks."
    spans = tag_abstract(abstract)
    assert any(span.label == "I" and "semaglutide" in span.span_text.casefold() for span in spans)
    assert any(span.label == "C" and span.span_text.casefold() == "placebo" for span in spans)
    assert not any(span.label == "C" and "semaglutide" in span.span_text.casefold() for span in spans)


def test_no_abstract_returns_no_spans():
    assert tag_abstract(None) == []
    assert tag_abstract("") == []


def test_overlap_resolution_uses_intervention_before_comparator_population_outcome():
    abstract = "semaglutide treatment"
    spans = resolve_overlaps(abstract, [
        PicoSpan("O", 0, 11, "semaglutide", 0.99),
        PicoSpan("I", 0, 11, "semaglutide", 0.80),
        PicoSpan("C", 0, 11, "semaglutide", 0.95),
    ])
    assert [(span.label, span.span_text) for span in spans] == [("I", "semaglutide")]


def test_gold_fixture_offsets_are_exact():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "pico_gold.json").read_text(encoding="utf-8")
    )
    assert len(fixture["records"]) == 5
    for record in fixture["records"]:
        for span in record["spans"]:
            assert record["abstract"][span["start_char"]:span["end_char"]] == span["span_text"]
