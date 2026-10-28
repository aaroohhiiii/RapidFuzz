# Step 6: Offline PICO tagging

## Scope and provenance

PICO tagging runs on every non-empty abstract in a query version unless the
current machine screening decision is Exclude. Include and Maybe records are
tagged. If screening has not run, a record is not considered excluded and is
eligible.

Every machine span stores exact offsets into `records.abstract`, the copied
text, `method=rules`, heuristic confidence, rule version, and PICO `run_log` ID.
Before persistence the implementation checks:

```text
abstract[start_char:end_char] == span_text
```

No network calls or external models are used.

## Text segmentation

Structured labels at line starts—BACKGROUND, OBJECTIVE, METHOD/METHODS,
RESULT/RESULTS, and CONCLUSION/CONCLUSIONS—define sections. Each section is
split into sentences while retaining full-abstract offsets. The splitter avoids
breaking decimals and the abbreviations `vs.`, `e.g.`, `i.e.`, and `et al.`.

## Population (P)

Population candidates are taken from Methods/Objective sections and the first
two sentences. Configured cues are patients with, adults with, participants,
subjects, aged, individuals with, women, men, children, and volunteers.
Patterns also recognize `n = 120`, `120 patients`, and similar sample-size
phrases. Boundary rules stop at punctuation or enrollment/treatment verbs so
the full sentence is not tagged.

## Intervention (I)

The Step 0 intervention vocabulary is semaglutide, liraglutide, tirzepatide,
GLP-1 receptor agonist, and glucagon-like peptide-1 receptor agonist variants.
An attached dose such as `2.4 mg` is included. Additional patterns inspect text
after received, treated with, randomized/randomised to, and administered.

## Comparator (C)

Comparator rules are project-authored because no provider metadata supplies
this field. Direct terms are placebo, control group, usual care, standard of
care, sham, active comparator, and lifestyle intervention alone. The rules also
inspect phrases following versus, vs, compared with, and compared to. In
`semaglutide or placebo`, only placebo is tagged as Comparator. A captured
phrase containing a configured intervention is rejected as a comparator.

## Outcome (O)

Outcome cues include primary/secondary outcome or endpoint, main outcome,
change in, reduction in, improvement in, measured, and assessed. The Step 0
weight vocabulary includes body weight, weight loss, BMI/body mass index, waist
circumference, HbA1c/glycated hemoglobin, weight reduction, and percent weight
change. Vocabulary phrases are preferred over a full cue clause.

## Overlap and length rules

Spans shorter than 3 or longer than 200 characters are dropped. Any character
overlap is resolved using `I > C > P > O`. Intervention and comparator phrases
are the most arm-specific evidence; population precedes outcome because broad
outcome cue windows are more likely to consume neighboring subject text. Ties
within a label use higher confidence, then earlier and shorter spans.

## Confidence

Confidence is not calibrated probability. It is only a sorting/display
heuristic: direct configured terms and explicit patterns receive higher values
than bounded generic cue windows. It must not be used as an exclusion threshold.

## Incremental abstract hashes

No migration is required. Each completed run stores short SHA-256 abstract
hashes by record ID inside `run_log.stats.abstract_hashes`. A matching hash and
rule version reuses the prior run, including records that produced zero spans.
Changed abstracts create new historical spans. Reads select only the newest
completed run whose saved hash matches the current abstract; old rows are never
deleted.

## Effective spans

`merge_effective_spans` is the single merge function intended for both API and
CSV export. It starts with the current hash-matched machine spans, applies the
latest human remove/edit action for each machine span, and adds human-created
spans. Stale human offsets that no longer reproduce `span_text` are ignored.

## Evaluation

Run:

```bash
cd backend
python scripts/pico_eval.py
```

The current starter fixture has five hand-labelled topic abstracts and exact
offset spans. It must be expanded with reviewer help to 15–20 records before
the final report. Precision and recall are exact-span estimates on this small,
constructed set, not claims of general performance.

## Limits and ML improvements

- No coreference: “the drug”, “the control”, or “they” is not linked back.
- No negation or speculation handling.
- Complex coordinated arms and nested noun phrases may be truncated.
- Sentence splitting is intentionally simple and cannot cover every abbreviation.
- Population extraction favors early/Methods text and may miss Results-only descriptions.
- Vocabulary rules miss unseen drug, outcome, and comparator synonyms.
- Human corrections whose abstract offsets become stale are not projected onto changed text.
- A trained biomedical sequence tagger could improve contextual boundaries,
  synonyms, coreference, negation, and coordinated-arm interpretation, but would
  require labelled data, versioned model provenance, and offline packaging.
