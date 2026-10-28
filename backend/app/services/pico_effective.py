"""Single effective-span merge shared by API and future exports."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import PicoHuman, PicoMachine


@dataclass(frozen=True)
class EffectivePicoSpan:
    """A machine or human span after applying append-only corrections."""

    id: int
    source: str
    label: str
    start_char: int
    end_char: int
    span_text: str
    method: str
    confidence: float | None
    rule_version: str | None


def merge_effective_spans(
    abstract: str, machine_spans: list[PicoMachine], human_actions: list[PicoHuman]
) -> list[EffectivePicoSpan]:
    """Apply latest remove/edit actions and append human additions."""

    latest_by_machine: dict[int, PicoHuman] = {}
    additions: list[PicoHuman] = []
    for action in sorted(human_actions, key=lambda item: item.id):
        if action.action == "add":
            additions.append(action)
        elif action.machine_span_id is not None:
            latest_by_machine[action.machine_span_id] = action

    output: list[EffectivePicoSpan] = []
    for span in machine_spans:
        action = latest_by_machine.get(span.id)
        if action and action.action == "remove":
            continue
        if action and action.action == "edit":
            human_span = _human_span(abstract, action)
            if human_span:
                output.append(human_span)
            continue
        if abstract[span.start_char : span.end_char] == span.span_text:
            output.append(EffectivePicoSpan(
                id=span.id,
                source="machine",
                label=span.label,
                start_char=span.start_char,
                end_char=span.end_char,
                span_text=span.span_text,
                method=span.method or "rules",
                confidence=span.confidence,
                rule_version=span.rule_version,
            ))
    output.extend(
        span for action in additions if (span := _human_span(abstract, action)) is not None
    )
    return sorted(output, key=lambda span: (span.start_char, span.end_char, span.label, span.source))


def _human_span(abstract: str, action: PicoHuman) -> EffectivePicoSpan | None:
    if action.label is None or action.start_char is None or action.end_char is None or action.span_text is None:
        return None
    if abstract[action.start_char : action.end_char] != action.span_text:
        return None
    return EffectivePicoSpan(
        id=action.id,
        source="human",
        label=action.label,
        start_char=action.start_char,
        end_char=action.end_char,
        span_text=action.span_text,
        method="human",
        confidence=None,
        rule_version=None,
    )
