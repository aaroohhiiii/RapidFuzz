# Step 0: Review Question and Criteria

Mini-Rayyan Hackathon · Topic: GLP-1 receptor agonists for weight loss in adults with obesity

## Review question

In adults with obesity, do GLP-1 receptor agonists, compared with placebo or another weight-loss treatment, reduce body weight?

## PICO

| Part | Definition |
| --- | --- |
| Population | Adults (18+) with obesity or overweight |
| Intervention | GLP-1 receptor agonists (semaglutide, liraglutide, tirzepatide) |
| Comparator | Placebo, lifestyle intervention, or another drug |
| Outcome | Change in body weight (kg or %) |

## Keyphrases

- ("obesity" OR "overweight" OR "weight loss")
- AND ("semaglutide" OR "liraglutide" OR "tirzepatide" OR "GLP-1")
- AND ("placebo" OR "randomized")

Date range: keep it tight (for example 2018 to 2026) so the demo pulls hundreds of records, not tens of thousands.

## Inclusion criteria

| Criterion | Reason | Source |
| --- | --- | --- |
| Human studies of adults | The question is about adults | Metadata: MeSH Humans / Adult |
| Trials comparing a GLP-1 drug with a comparator | These can answer the question | Own algorithm: study-design keywords |

## Exclusion criteria

| Criterion | Reason | Source |
| --- | --- | --- |
| Animal studies | The question is about people | Metadata: MeSH "Animals" without "Humans" |
| Non-English text | The team cannot check it | Metadata: language field |
| Simulation or modelling studies | They model results and do not measure patients | Own algorithm: keywords such as "simulation", "Markov", "cost-effectiveness model" |
| Case reports | One patient cannot show an average effect | Metadata: publication type |
| Pediatric-only studies | The question is about adults | Metadata: MeSH Child / Adolescent, plus age keywords |
| Reviews and editorials | They report no new measurements | Metadata: publication type |

## Where a human decides

The reviewer chooses which criteria are active, can override any exclusion, and every override is logged. Uncertain records are marked Maybe, never silently excluded.
