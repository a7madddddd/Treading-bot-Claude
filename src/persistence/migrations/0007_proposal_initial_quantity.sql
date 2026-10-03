-- 0007: Add initial_quantity to proposals (D-0051, 2026-10-03).
--
-- D-0004 §1 fixed the Initial Entry share count at 10 and never stored
-- it on the TradeProposal row, so execution had to re-read it from
-- the frozen StrategyRuleSet at submission time. D-0051 supersedes that
-- part of D-0004 and sizes each layer as a percentage of equity, which
-- means the share count must be computed and FROZEN at proposal
-- creation time -- if equity or price moves between the Controller's
-- approval and the broker submission, the Controller's approved count
-- must still be what reaches the broker.
--
-- The column is NULLABLE on purpose: proposals persisted before this
-- migration (the pre-D-0051 fixed-share era) remain untouched by the
-- backfill, read back with initial_quantity = NULL, and the execution
-- service falls back to strategy.initial_qty for them. New proposals
-- created by build_trade_proposal() after this migration always set it.

ALTER TABLE proposals
    ADD COLUMN initial_quantity INTEGER;
