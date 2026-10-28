## Shared definitions (apply to every task)

Effective status of a record (one function, used by API, UI data and CSV export):
1. A hidden duplicate resolves to its group's representative.
2. Group human decision comes from members' latest human_decisions (v_group_decision). Differing values = 'conflict'.
3. No human decision = machine decision of the representative.
Returns {status: include|exclude|maybe|conflict, decided_by, reasons, note, reviewer, decided_at}.

Effective PICO spans (one function): machine spans minus human removals, plus human additions/edits, each tagged source machine|human.

Write endpoints: reviewer name is required (422 if missing); the data write and its audit_log row happen in one transaction; human tables are append-only.

Every pipeline step (count, fetch, dedup, screen, pico) is a background job: returns 202 {run_id}, logs a run_log row with stats, is idempotent and resumable.

Frontend rules: typed API client in src/api/, TanStack Query, loading/empty/error states, disabled buttons while saving, no mock data.
If a needed endpoint is missing or awkward, tell me the exact change instead of faking it. Reviewer name is asked once and kept in localStorage.

Output style (saves my credits): be terse. No long summaries, no reprinting files, no explaining code I did not ask about.
Do not re-read all of docs/ each task, only the files relevant to the task. After each step show only the key result (test tail, one sample output).
Commit after each step. Ask me only when blocked.