"""CLI ranker (D-0050 Phase 10).

Takes a comma-separated list of symbols, collects every available
API's data for each via SymbolResearchHub, filters/scores through
TradeEvaluator, and prints a best-first ranking.

Usage:
  cd ~/Treading-bot-Claude && set -a && source .env && set +a && \\
  PYTHONPATH=src python3.11 scripts/rank_symbols.py AAPL,MSFT,GOOGL,TSLA
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from engine.research_hub import SymbolResearchHub       # noqa: E402
from engine.trade_evaluator import (                     # noqa: E402
    TradeEvaluator, format_evaluation_block,
)


def _build_hub():
    """Instantiates whichever clients the env has keys for."""
    px = None
    try:
        from research.perplexity_agent import PerplexityAgentClient
        k = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        if k:
            px = PerplexityAgentClient(api_key=k)
    except Exception:  # noqa: BLE001
        pass

    from marketdata.fred_source import FredSource
    from marketdata.finnhub_source import FinnhubSource
    from marketdata.alpha_vantage_source import AlphaVantageSource
    from marketdata.polygon_source import PolygonSource
    from marketdata.tiingo_source import TiingoSource

    return SymbolResearchHub(
        fred=FredSource.from_env(),
        finnhub=FinnhubSource.from_env(),
        alpha_vantage=AlphaVantageSource.from_env(),
        polygon=PolygonSource.from_env(),
        tiingo=TiingoSource.from_env(),
        perplexity=px,
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: rank_symbols.py SYM1,SYM2,SYM3", file=sys.stderr)
        return 2
    symbols = [s.strip().upper() for s in sys.argv[1].split(",") if s.strip()]
    if not symbols:
        print("No symbols given.", file=sys.stderr)
        return 2

    hub = _build_hub()
    evaluator = TradeEvaluator(hub)

    print(f"\nEvaluating {len(symbols)} symbol(s): "
          f"{', '.join(symbols)}\n")
    results = evaluator.rank(symbols)

    print(f"{'='*68}")
    print(f"  RANKING (best first)")
    print(f"{'='*68}")
    for i, res in enumerate(results, start=1):
        print(f"\n{i}. {res.symbol}")
        block = format_evaluation_block(res)
        for ln in block.split("\n"):
            print(f"   {ln}")
        if res.research.sources_failed:
            print(f"   (sources that failed: "
                  f"{', '.join(res.research.sources_failed)})")

    passed = [r for r in results if r.passes_hard_filter]
    print(f"\n{'='*68}")
    print(f"  {len(passed)}/{len(results)} passed hard filter.")
    if passed:
        best = passed[0]
        print(f"  Recommended entry: {best.symbol} "
              f"(score {best.soft_score:.1f}/100)")
    print(f"{'='*68}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
