"""Trade evaluator (D-0050 Phase 10).

The single filter+score+rank pipeline that turns a candidate symbol
into a yes/no + 0-100 score + breakdown. Combines:

  HARD FILTER (veto rules — reject the candidate outright):
    - no live price data
    - earnings within 7 days (volatility risk)
    - day volume < 100k shares (illiquid)
    - RSI extreme (>90 overbought / <10 oversold)
    - macro regime = RISK_OFF (unless explicitly disabled)

  SOFT SCORE (weighted contributions, 0..90 before discounts — the
  weights below are the ones EvaluatorConfig actually carries; this
  list was stale until 2026-10-06 and claimed 20/25/15/10):
    - Technicals      (weight 20)  — RSI healthy, MACD bullish, golden cross
    - Fundamentals    (weight 16)  — P/E reasonable, buy ratio, insider positive
    - Political       (weight 15)  — congressional cluster signal
    - Trend           (weight 13)  — 5d / 30d / 90d returns
    - Momentum        (weight 10)  — day change positive, above SMA50
    - Rel. strength   (weight  9)  — vs SPY over 30 trading days
    - News            (weight  7)  — fresh news + catalyst/risk polarity
    - Risk discounts  (-up to 20)  — bearish MACD, BB extreme, high realized
                                     vol. The per-risk-BULLET charge was
                                     removed by D-0083: it cost every
                                     candidate the same -9.0, so it
                                     separated nothing.

Every rule, weight, and threshold is a module-level constant so the
Controller can tune without touching logic. Nothing here places or
cancels an order — this module produces advisory evaluations ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from engine.research_hub import SymbolResearch


# ---------------------------------------------------------------------------
# Tunable thresholds (defaults; adjust via EvaluatorConfig if needed)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvaluatorConfig:
    # Hard filter thresholds
    min_day_volume: float = 100_000.0
    reject_earnings_within_days: int = 7
    reject_rsi_above: float = 90.0
    reject_rsi_below: float = 10.0
    reject_on_risk_off_regime: bool = True
    require_price: bool = True

    # Soft-score weights (should sum to ~100 before risk discounts)
    weight_fundamentals: float = 16.0
    weight_technicals: float = 20.0
    weight_momentum: float = 10.0
    weight_news: float = 7.0
    weight_trend: float = 13.0        # D-0050 Phase 12: 5d/30d/90d returns
    weight_rel_strength: float = 9.0  # D-0050 Phase 12: vs SPY
    weight_political: float = 15.0    # D-0050 Phase B.26: congressional signal

    # Risk-discount caps
    max_risk_discount: float = 20.0
    # Phase 12: additional risk discount for high volatility
    high_volatility_pct_threshold: float = 60.0  # annualized stddev > this → penalty

    # Fundamentals tuning
    pe_sweet_spot_low: float = 10.0
    pe_sweet_spot_high: float = 35.0
    pe_rich_above: float = 60.0

    # Technicals tuning
    rsi_healthy_low: float = 40.0
    rsi_healthy_high: float = 65.0


DEFAULT_CONFIG = EvaluatorConfig()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class EvaluationResult:
    symbol: str
    passes_hard_filter: bool
    hard_filter_reasons: List[str]
    soft_score: float
    score_breakdown: Dict[str, float]
    research: SymbolResearch

    def is_recommended(self) -> bool:
        return self.passes_hard_filter and self.soft_score >= 50.0

    def summary_line(self) -> str:
        if not self.passes_hard_filter:
            return (f"{self.symbol}: REJECTED — {'; '.join(self.hard_filter_reasons)}")
        return (f"{self.symbol}: score={self.soft_score:.1f}/100 "
                + " · ".join(f"{k} {v:+.0f}"
                             for k, v in self.score_breakdown.items()))


# ---------------------------------------------------------------------------
# Hard filter
# ---------------------------------------------------------------------------

def _hard_filter(r: SymbolResearch, cfg: EvaluatorConfig) -> List[str]:
    """Returns a list of rejection reasons. Empty list = passes."""
    reasons: List[str] = []

    if cfg.require_price and r.current_price is None:
        reasons.append("no live price")

    if r.day_volume is not None and r.day_volume < cfg.min_day_volume:
        reasons.append(f"low volume ({r.day_volume:,.0f} < "
                       f"{cfg.min_day_volume:,.0f})")

    if (r.next_earnings_days is not None
            and 0 <= r.next_earnings_days <= cfg.reject_earnings_within_days):
        reasons.append(f"earnings in {r.next_earnings_days}d")

    if r.rsi_14 is not None:
        if r.rsi_14 > cfg.reject_rsi_above:
            reasons.append(f"RSI {r.rsi_14:.0f} extreme-overbought")
        elif r.rsi_14 < cfg.reject_rsi_below:
            reasons.append(f"RSI {r.rsi_14:.0f} extreme-oversold")

    if cfg.reject_on_risk_off_regime and r.regime == "risk_off":
        reasons.append("macro regime RISK_OFF")

    # D-0050 Phase B.26: insiders en-masse → trust the exit.
    if r.political_sell_wave:
        reasons.append(f"insider sell wave ({r.political_sells_30d} whitelisted sellers)")

    return reasons


# ---------------------------------------------------------------------------
# Soft scoring components
# ---------------------------------------------------------------------------

def _score_fundamentals(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_fundamentals. 0 if no data."""
    if r.pe_ratio is None and r.analyst_buy_ratio is None and r.insider_mspr is None:
        return 0.0
    score = 0.0
    components = 0

    if r.pe_ratio is not None:
        components += 1
        if cfg.pe_sweet_spot_low <= r.pe_ratio <= cfg.pe_sweet_spot_high:
            score += 1.0
        elif r.pe_ratio <= cfg.pe_rich_above:
            score += 0.5
        else:
            score += 0.0

    if r.analyst_buy_ratio is not None:
        components += 1
        score += r.analyst_buy_ratio  # 0..1 already

    if r.insider_mspr is not None:
        components += 1
        # Scale -100..100 to 0..1 with 0 insider_mspr → 0.5
        score += max(0.0, min(1.0, (r.insider_mspr + 100.0) / 200.0))

    if components == 0:
        return 0.0
    normalized = score / components  # 0..1
    return normalized * cfg.weight_fundamentals


def _score_technicals(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_technicals. Rewards healthy RSI + bullish MACD + golden cross."""
    if r.rsi_14 is None and r.macd_signal is None and r.golden_cross is None:
        return 0.0
    score = 0.0
    components = 0

    if r.rsi_14 is not None:
        components += 1
        if cfg.rsi_healthy_low <= r.rsi_14 <= cfg.rsi_healthy_high:
            score += 1.0
        elif r.rsi_14 < 30 or r.rsi_14 > 75:
            score += 0.2
        else:
            score += 0.6

    if r.macd_signal is not None:
        components += 1
        score += {
            "bullish_crossover": 1.0,
            "bullish": 0.75,
            "bearish": 0.25,
            "bearish_crossover": 0.0,
        }.get(r.macd_signal, 0.5)

    if r.golden_cross is not None:
        components += 1
        score += 1.0 if r.golden_cross else 0.3

    if components == 0:
        return 0.0
    return (score / components) * cfg.weight_technicals


def _score_momentum(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_momentum. Rewards positive day change + above SMA50."""
    score = 0.0
    components = 0
    if r.day_change_pct is not None:
        components += 1
        # Cap at ±5% range; cleanly scaled
        clamped = max(-5.0, min(5.0, r.day_change_pct))
        score += (clamped + 5.0) / 10.0  # 0..1

    if r.current_price is not None and r.sma_50 is not None:
        components += 1
        score += 1.0 if r.current_price > r.sma_50 else 0.3

    if components == 0:
        return 0.0
    return (score / components) * cfg.weight_momentum


def _score_news(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_news. Rewards fresh news + POLARITY of catalyst/risk
    balance (D-0050 Phase 17), not just raw counts. Polarity maps
    [-1, +1] to [0, 1] so purely negative sentiment contributes zero
    rather than pulling the component into negative weighting (the
    risk component handles that side)."""
    if r.news_count_48h == 0 and not r.perplexity_catalysts and not r.perplexity_risks:
        return 0.0
    score = 0.0
    components = 0
    if r.news_count_48h > 0:
        components += 1
        score += min(1.0, r.news_count_48h / 5.0)
    from engine.news_sentiment import compute_news_polarity
    polarity = compute_news_polarity(r)
    if polarity is not None:
        components += 1
        # Shift from [-1, 1] to [0, 1]
        score += (polarity + 1.0) / 2.0
    if components == 0:
        return 0.0
    return (score / components) * cfg.weight_news


def _risk_discount(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..max_risk_discount — SUBTRACTED from the raw score."""
    discount = 0.0
    # D-0083 (2026-10-06): the Perplexity-risk-COUNT rule was removed
    # here. It charged 3.0 per risk bullet, and the research prompt
    # (deep_research.py) asks for exactly 3 bullets and caps the list at
    # 3, so EVERY symbol paid exactly -9.0 in every cycle -- measured on
    # 2026-10-06, where MUFG, TX and SMH all carried -9.0 and nothing
    # else fired. A discount that every candidate pays identically
    # cannot separate a safe symbol from a risky one; it only lowered
    # the whole distribution 9 points under a fixed 60-point bar. The
    # prompt even ends with "If nothing material, say so", so a
    # "no material risk" answer arrived as a bullet and was charged for.
    #
    # The three rules below are kept BECAUSE THEY DISCRIMINATE: each one
    # fires on a measured condition of that specific symbol, so the gap
    # between a clean and a risky candidate is unchanged by this removal
    # (a bearish crossover still costs 5.0 more than no crossover).
    if r.macd_signal == "bearish_crossover":
        discount += 5.0
    if r.bb_position is not None and r.bb_position > 0.95:
        discount += 3.0  # price at upper band — mean reversion risk
    if r.regime == "neutral":
        discount += 0.0  # neutral is fine
    # Phase 12: high realized vol = larger position-size risk
    if (r.volatility_30d_pct is not None
            and r.volatility_30d_pct > cfg.high_volatility_pct_threshold):
        discount += min(5.0, (r.volatility_30d_pct - cfg.high_volatility_pct_threshold) / 20)
    return min(cfg.max_risk_discount, discount)


# ---- Trend (5d / 30d / 90d returns) ---------------------------------

def _score_trend(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_trend. Rewards positive multi-period performance.
    Each window contributes equally when present."""
    components = 0
    score = 0.0
    for ret in (r.return_5d_pct, r.return_30d_pct, r.return_90d_pct):
        if ret is None:
            continue
        components += 1
        # Normalize: -10% → 0, +10% → 1 (clamped)
        clamped = max(-10.0, min(10.0, ret))
        score += (clamped + 10.0) / 20.0
    if components == 0:
        return 0.0
    return (score / components) * cfg.weight_trend


# ---- Political signal (D-0050 Phase B.26) ---------------------------

def _score_political(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_political. Driven by the composite weighted_signal
    the cluster builder produces (0..25) → mapped to 0..weight_political."""
    sig = getattr(r, "political_weighted_signal", 0.0) or 0.0
    if sig <= 0:
        return 0.0
    normalized = min(1.0, sig / 25.0)
    return normalized * cfg.weight_political


# ---- Relative strength vs SPY ---------------------------------------

def _score_rel_strength(r: SymbolResearch, cfg: EvaluatorConfig) -> float:
    """0..weight_rel_strength. Rewards outperformance of the market
    (SPY). Positive RS = stock beats SPY by that many points over the
    last 30 trading days."""
    if r.rel_strength_30d_pct is None:
        return 0.0
    # -5% RS → 0, +5% RS → 1 (clamped)
    clamped = max(-5.0, min(5.0, r.rel_strength_30d_pct))
    normalized = (clamped + 5.0) / 10.0
    return normalized * cfg.weight_rel_strength


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class TradeEvaluator:
    """Orchestrates research collection, hard filter, soft score, and
    ranking. Advisory only."""

    def __init__(self, hub, config: EvaluatorConfig = DEFAULT_CONFIG) -> None:
        self._hub = hub
        self._cfg = config

    def evaluate(self, symbol: str) -> EvaluationResult:
        research = self._hub.collect(symbol)
        return self.evaluate_research(research)

    def evaluate_research(self, research: SymbolResearch) -> EvaluationResult:
        cfg = self._cfg
        reasons = _hard_filter(research, cfg)
        if reasons:
            return EvaluationResult(
                symbol=research.symbol,
                passes_hard_filter=False,
                hard_filter_reasons=reasons,
                soft_score=0.0,
                score_breakdown={},
                research=research,
            )
        breakdown: Dict[str, float] = {
            "fundamentals": _score_fundamentals(research, cfg),
            "technicals":   _score_technicals(research, cfg),
            "momentum":     _score_momentum(research, cfg),
            "trend":        _score_trend(research, cfg),
            "rel_str":      _score_rel_strength(research, cfg),
            "news":         _score_news(research, cfg),
            "political":    _score_political(research, cfg),
            "risk":        -_risk_discount(research, cfg),
        }
        total = sum(breakdown.values())
        total = max(0.0, min(100.0, total))
        return EvaluationResult(
            symbol=research.symbol,
            passes_hard_filter=True,
            hard_filter_reasons=[],
            soft_score=total,
            score_breakdown=breakdown,
            research=research,
        )

    def rank(self, symbols: Sequence[str]) -> List[EvaluationResult]:
        """Evaluates every symbol and returns them sorted best-first.
        Rejected (hard-filter) symbols appear at the end."""
        results = [self.evaluate(s) for s in symbols]
        passed = sorted([r for r in results if r.passes_hard_filter],
                        key=lambda r: -r.soft_score)
        failed = [r for r in results if not r.passes_hard_filter]
        return passed + failed


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_evaluation_block(result: EvaluationResult) -> str:
    """Compact human-readable block for a Telegram proposal message.
    Shows score, pass/fail, each component, and the top signals."""
    lines = []
    if not result.passes_hard_filter:
        lines.append(f"🚫 Evaluator REJECTED: {'; '.join(result.hard_filter_reasons)}")
        return "\n".join(lines)

    r = result.research
    verdict_tag = "⭐ STRONG" if result.soft_score >= 70 else (
        "🟢 OK" if result.soft_score >= 50 else "🟡 WEAK")
    lines.append(f"⚖ Evaluator score: {result.soft_score:.1f}/100  {verdict_tag}")
    bits = []
    for k in ("fundamentals", "technicals", "momentum", "trend",
              "rel_str", "news", "political", "risk"):
        v = result.score_breakdown.get(k)
        if v is not None:
            short = {"fundamentals":"fund","technicals":"tech",
                     "momentum":"mome","trend":"trnd","rel_str":"rsvs",
                     "news":"news","political":"poli","risk":"risk"}.get(k, k[:4])
            bits.append(f"{short} {v:+.0f}")
    lines.append("  " + " · ".join(bits))

    # Confirm what each API contributed
    sig_bits = []
    if r.pe_ratio is not None:
        sig_bits.append(f"P/E {r.pe_ratio:.1f}")
    if r.analyst_buys is not None and r.analyst_holds is not None and r.analyst_sells is not None:
        sig_bits.append(f"Buy {r.analyst_buys}/{r.analyst_buys + r.analyst_holds + r.analyst_sells}")
    if r.insider_mspr is not None:
        sig_bits.append(f"Insider {r.insider_mspr:+.0f}")
    if r.rsi_14 is not None:
        sig_bits.append(f"RSI {r.rsi_14:.0f}")
    if r.macd_signal is not None:
        sig_bits.append(f"MACD {r.macd_signal}")
    if r.day_change_pct is not None:
        sig_bits.append(f"Day {r.day_change_pct:+.2f}%")
    if r.return_5d_pct is not None:
        sig_bits.append(f"5d {r.return_5d_pct:+.1f}%")
    if r.return_30d_pct is not None:
        sig_bits.append(f"30d {r.return_30d_pct:+.1f}%")
    if r.rel_strength_30d_pct is not None:
        sig_bits.append(f"RS/SPY {r.rel_strength_30d_pct:+.1f}")
    if r.volatility_30d_pct is not None:
        sig_bits.append(f"Vol {r.volatility_30d_pct:.0f}%")
    if r.volume_ratio_30d is not None:
        sig_bits.append(f"VolX {r.volume_ratio_30d:.1f}")
    if r.news_count_48h:
        sig_bits.append(f"News {r.news_count_48h}")
    if r.regime is not None:
        sig_bits.append(f"Macro {r.regime}")
    if r.political_buys_30d:
        if r.political_committee_match:
            sig_bits.append(f"🏛️ {r.political_buys_30d} buyers (cmte-match)")
        else:
            sig_bits.append(f"🏛️ {r.political_buys_30d} buyers")
    if r.political_sells_30d:
        sig_bits.append(f"🏛️⚠ {r.political_sells_30d} sellers")
    if sig_bits:
        lines.append("  Signals: " + " · ".join(sig_bits))

    return "\n".join(lines)
