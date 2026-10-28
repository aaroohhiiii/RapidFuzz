-- PROPOSED ONLY: do not apply without approval.
-- Needed to represent an append-only undo of the first human decision.
ALTER TABLE human_decisions
ADD COLUMN restores_machine BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN human_decisions.restores_machine IS
'When true, this append-only event restores machine control; decision is ignored.';
