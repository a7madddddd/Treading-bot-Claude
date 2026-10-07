-- Migration 0010 (P-091): when the Controller actually SAW the proposal.
--
-- The 60-minute approval TTL was measured from `proposal_created_at`,
-- but the research report is assembled between saving the row and
-- sending the message. Measured twice on 2026-10-07: 9m52s and 9m47s.
-- The Controller was given 50 minutes of a 60-minute window and was
-- never told.
--
-- `notified_at` is the moment the proposal notification was handed to
-- the notification service. The TTL sweep measures from it when it is
-- present and falls back to `proposal_created_at` when it is NULL, so
-- every pre-migration row keeps its existing behavior exactly.
--
-- Deliberately a NEW column rather than reusing `approval_expires_at`:
-- that column already carries D-0007's 5-minute post-approval window
-- (models.py: `expires_at = decided_at + APPROVAL_WINDOW`), and
-- overloading it would corrupt the ladder submission gate.

ALTER TABLE proposals ADD COLUMN notified_at TEXT;
