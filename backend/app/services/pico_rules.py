"""Fast offline PICO span extraction with exact abstract offsets."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.config import settings


SECTION_RE = re.compile(
    r"(?im)(?:(?<=^)|(?<=\n))[ \t]*(BACKGROUND|OBJECTIVE|METHODS?|RESULTS?|CONCLUSIONS?)[ \t]*:?[ \t]*"
)
SENTENCE_END_RE = re.compile(r"[.!?]")
SAFE_ABBREVIATIONS = ("vs.", "e.g.", "i.e.", "et al.")


@dataclass(frozen=True)
class TextSegment:
    """A section or sentence with offsets into the original abstract."""

    start: int
    end: int
    text: str
    section: str | None = None


@dataclass(frozen=True)
class PicoSpan:
    """One proposed PICO annotation."""

    label: str
    start_char: int
    end_char: int
    span_text: str
    confidence: float
    method: str = "rules"


def tag_abstract(abstract: str | None) -> list[PicoSpan]:
    """Return deterministic, non-overlapping PICO spans for an abstract."""

    if not abstract:
        return []
    sentences = split_sentences(abstract)
    candidates: list[PicoSpan] = []
    for index, sentence in enumerate(sentences):
        methods_or_early = (sentence.section or "").casefold().startswith(("method", "objective")) or index < 2
        if methods_or_early:
            candidates.extend(_population_spans(abstract, sentence))
        candidates.extend(_intervention_spans(abstract, sentence))
        candidates.extend(_comparator_spans(abstract, sentence))
        candidates.extend(_outcome_spans(abstract, sentence))
    spans = resolve_overlaps(abstract, candidates)
    for span in spans:
        if abstract[span.start_char : span.end_char] != span.span_text:
            raise ValueError("PICO offset invariant violated")
    return spans


def split_sections(abstract: str) -> list[TextSegment]:
    """Split structured abstracts while preserving full-abstract offsets."""

    matches = list(SECTION_RE.finditer(abstract))
    if not matches:
        return [TextSegment(0, len(abstract), abstract, None)]
    sections: list[TextSegment] = []
    if abstract[: matches[0].start()].strip():
        start, end = _trim_bounds(abstract, 0, matches[0].start())
        sections.append(TextSegment(start, end, abstract[start:end], None))
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(abstract)
        start, end = _trim_bounds(abstract, body_start, body_end)
        if start < end:
            sections.append(TextSegment(start, end, abstract[start:end], match.group(1).upper()))
    return sections


def split_sentences(abstract: str) -> list[TextSegment]:
    """Split sections into sentences without breaking common biomedical abbreviations."""

    output: list[TextSegment] = []
    for section in split_sections(abstract):
        sentence_start = section.start
        for match in SENTENCE_END_RE.finditer(abstract, section.start, section.end):
            punctuation = match.start()
            if not _is_sentence_boundary(abstract, punctuation, section.end):
                continue
            start, end = _trim_bounds(abstract, sentence_start, match.end())
            if start < end:
                output.append(TextSegment(start, end, abstract[start:end], section.section))
            sentence_start = match.end()
        start, end = _trim_bounds(abstract, sentence_start, section.end)
        if start < end:
            output.append(TextSegment(start, end, abstract[start:end], section.section))
    return output


def resolve_overlaps(abstract: str, candidates: list[PicoSpan]) -> list[PicoSpan]:
    """Filter invalid spans and resolve overlaps using configured label priority."""

    priority = {label: index for index, label in enumerate(settings.PICO_LABEL_PRIORITY)}
    unique = {
        (span.label, span.start_char, span.end_char): span
        for span in candidates
        if settings.PICO_MIN_SPAN <= span.end_char - span.start_char <= settings.PICO_MAX_SPAN
        and 0 <= span.start_char < span.end_char <= len(abstract)
        and abstract[span.start_char : span.end_char] == span.span_text
    }
    ordered = sorted(
        unique.values(),
        key=lambda span: (priority[span.label], -span.confidence, span.start_char, span.end_char),
    )
    accepted: list[PicoSpan] = []
    for span in ordered:
        if any(span.start_char < other.end_char and other.start_char < span.end_char for other in accepted):
            continue
        accepted.append(span)
    return sorted(accepted, key=lambda span: (span.start_char, span.end_char, priority[span.label]))


def _population_spans(abstract: str, sentence: TextSegment) -> list[PicoSpan]:
    text = sentence.text
    patterns = (
        r"\bn\s*=\s*\d+\b",
        r"\b\d+\s+(?:patients|participants|subjects|adults|women|men|children|volunteers)\b(?:\s+with\s+[^,;:.]{3,100}?(?=\s+(?:were|was|are|is|received|treated|randomi[sz]ed|underwent)|[,;:.]|$))?",
        r"\b(?:patients with|adults with|individuals with)\s+[^,;:.]{3,100}?(?=\s+(?:were|was|are|is|received|treated|randomi[sz]ed|underwent)|[,;:.]|$)",
    )
    spans = [_span_from_match(abstract, sentence, match, "P", 0.9) for pattern in patterns for match in re.finditer(pattern, text, re.I)]
    for cue in settings.PICO_POPULATION_CUES:
        for match in _term_matches(text, cue):
            local_start = _left_boundary(text, match.start(), 35)
            local_end = _right_boundary(text, match.end(), 100)
            spans.append(_make_span(abstract, sentence.start + local_start, sentence.start + local_end, "P", 0.7))
    return [span for span in spans if span is not None]


def _intervention_spans(abstract: str, sentence: TextSegment) -> list[PicoSpan]:
    spans: list[PicoSpan] = []
    for term in settings.PICO_INTERVENTION_TERMS:
        pattern = rf"(?<!\w){re.escape(term)}(?!\w)(?:\s+(?:at\s+)?\d+(?:\.\d+)?\s*(?:mg|mcg|µg))?"
        for match in re.finditer(pattern, sentence.text, re.I):
            spans.append(_span_from_match(abstract, sentence, match, "I", 0.95))
    received = re.compile(r"\b(?:received|treated with|randomi[sz]ed to|administered)\s+([^,;:.]{3,100})", re.I)
    for match in received.finditer(sentence.text):
        phrase_start, phrase_end = match.span(1)
        phrase = sentence.text[phrase_start:phrase_end]
        cutoff = re.search(r"\b(?:versus|vs\.?|compared (?:with|to)|or placebo)\b", phrase, re.I)
        if cutoff:
            phrase_end = phrase_start + cutoff.start()
        span = _make_span(abstract, sentence.start + phrase_start, sentence.start + phrase_end, "I", 0.75)
        if span and not _contains_comparator(span.span_text):
            spans.append(span)
    return spans


def _comparator_spans(abstract: str, sentence: TextSegment) -> list[PicoSpan]:
    spans: list[PicoSpan] = []
    for term in settings.PICO_COMPARATOR_TERMS:
        for match in _term_matches(sentence.text, term):
            spans.append(_span_from_match(abstract, sentence, match, "C", 0.95))
    cue_pattern = re.compile(r"\b(?:versus|vs\.?|compared with|compared to)\s+([^,;:.]{3,100})", re.I)
    for match in cue_pattern.finditer(sentence.text):
        start, end = match.span(1)
        span = _make_span(abstract, sentence.start + start, sentence.start + end, "C", 0.82)
        if span and not _contains_intervention(span.span_text):
            spans.append(span)
    return spans


def _outcome_spans(abstract: str, sentence: TextSegment) -> list[PicoSpan]:
    spans: list[PicoSpan] = []
    for term in settings.PICO_OUTCOME_TERMS:
        for match in _term_matches(sentence.text, term):
            spans.append(_span_from_match(abstract, sentence, match, "O", 0.78))
    cue_pattern = re.compile(
        r"\b(?:primary outcome|primary endpoint|secondary outcome|secondary endpoint|the main outcome)\s+(?:was|were|is|included)?\s*([^,;:.]{3,120})",
        re.I,
    )
    for match in cue_pattern.finditer(sentence.text):
        start, end = match.span(1)
        phrase = sentence.text[start:end]
        vocabulary_match = _first_vocabulary_match(phrase, settings.PICO_OUTCOME_TERMS)
        if vocabulary_match:
            start += vocabulary_match.start()
            end = start + len(vocabulary_match.group(0))
        spans.append(_make_span(abstract, sentence.start + start, sentence.start + end, "O", 0.9))
    return [span for span in spans if span is not None]


def _is_sentence_boundary(text: str, punctuation: int, section_end: int) -> bool:
    next_position = punctuation + 1
    while next_position < section_end and text[next_position].isspace():
        next_position += 1
    if text[punctuation] == ".":
        if punctuation > 0 and punctuation + 1 < len(text) and text[punctuation - 1].isdigit() and text[punctuation + 1].isdigit():
            return False
        prefix = text[max(0, punctuation - 8) : punctuation + 1].casefold()
        if any(prefix.endswith(abbreviation) for abbreviation in SAFE_ABBREVIATIONS[:-1]):
            return False
        if prefix.endswith("et al.") and next_position < section_end and text[next_position].islower():
            return False
    return next_position >= section_end or text[next_position].isupper()


def _term_matches(text: str, term: str):
    return re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I)


def _first_vocabulary_match(text: str, terms: tuple[str, ...]):
    matches = [match for term in terms for match in _term_matches(text, term)]
    return min(matches, key=lambda match: match.start()) if matches else None


def _span_from_match(abstract: str, sentence: TextSegment, match, label: str, confidence: float) -> PicoSpan:
    return _make_span(abstract, sentence.start + match.start(), sentence.start + match.end(), label, confidence)


def _make_span(abstract: str, start: int, end: int, label: str, confidence: float) -> PicoSpan | None:
    start, end = _trim_bounds(abstract, start, end)
    if start >= end:
        return None
    return PicoSpan(label, start, end, abstract[start:end], confidence)


def _trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in ",;:"):
        end -= 1
    return start, end


def _left_boundary(text: str, position: int, window: int) -> int:
    start = max(0, position - window)
    boundary = max(text.rfind(char, start, position) for char in ",;:")
    return boundary + 1 if boundary >= start else start


def _right_boundary(text: str, position: int, window: int) -> int:
    limit = min(len(text), position + window)
    boundaries = [value for char in ",;:." if (value := text.find(char, position, limit)) >= 0]
    return min(boundaries) if boundaries else limit


def _contains_intervention(text: str) -> bool:
    return any(next(_term_matches(text, term), None) for term in settings.PICO_INTERVENTION_TERMS)


def _contains_comparator(text: str) -> bool:
    return any(next(_term_matches(text, term), None) for term in settings.PICO_COMPARATOR_TERMS)
