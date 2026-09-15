"""Security identity layer.

Design reference: docs/trading/historical-data-calibration-plan.md
(implementation-plan conversation, 2026-09-15) and
docs/trading/github-native-data-sources.md §9.4 (the DELL finding).

Hard rule enforced throughout this module: a ticker string is never
treated as a permanent security identifier. ``SecurityIdentity`` is the
stable, timeless anchor; ``TickerAlias`` carries the date range a given
ticker string actually pointed at that identity. Resolution always takes
a ticker *and* a date, and always returns an explicit outcome — resolved,
ambiguous, or unresolved. Ambiguity and absence are first-class,
auditable results, never silently guessed away.

No numeric D-0026 parameter is defined or implied anywhere in this
module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Sequence, Tuple


class IdentityConfidence(Enum):
    """How the identity was established, ordered roughly strongest-first.

    RESOLVED_CIK: anchored to a verified CIK.
    RESOLVED_CORROBORATED_RANGE: no CIK, but an independent point-in-time
        source corroborates a bounded date range for this ticker/identity
        pairing (this is how the DELL boundary dates were established —
        see github-native-data-sources.md §9.4 — without CIK being
        available for the pre-2013 entity).
    PROVISIONAL: ticker alone, no corroborating range or CIK. A normal,
        expected, and fully auditable confidence tier — not a failure
        state, but never silently promoted to a stronger tier without
        new evidence.
    """

    RESOLVED_CIK = "resolved_cik"
    RESOLVED_CORROBORATED_RANGE = "resolved_corroborated_range"
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class SecurityIdentity:
    """A stable, timeless security-level identity anchor.

    Distinct from a *company* (an issuer may have multiple securities or
    share classes) and distinct from a *ticker* (a security may have had
    multiple tickers over time, and a ticker string may be reused by an
    unrelated security after enough time has passed).

    Immutable. A correction to an existing identity's fields is never an
    in-place edit — it is a new ``SecurityIdentity`` record with its own
    ``security_id``, and the old one is superseded, never overwritten.
    """

    security_id: str
    display_name: str
    cik: Optional[str] = None
    confidence: IdentityConfidence = IdentityConfidence.PROVISIONAL

    def __post_init__(self) -> None:
        if not self.security_id:
            raise ValueError("security_id must be non-empty")
        if not self.display_name:
            raise ValueError("display_name must be non-empty")
        if self.cik is not None and self.confidence is IdentityConfidence.PROVISIONAL:
            raise ValueError(
                "a CIK-bearing identity cannot be marked PROVISIONAL; "
                "use RESOLVED_CIK"
            )


@dataclass(frozen=True)
class TickerAlias:
    """A ticker string, and the date range during which it pointed at a
    specific SecurityIdentity.

    ``effective_end`` is None to mean "still current as of the source's
    last refresh" — never to mean "unknown/don't care."

    This is the object that lets the same ticker string map to two
    *different* SecurityIdentity records across two non-overlapping date
    ranges — the DELL case:

        SecurityIdentity("dell-inc-original", ...)
            TickerAlias("DELL", effective_start=None, effective_end=2013-10-29)
        SecurityIdentity("dell-technologies", cik="0001571996", ...)
            TickerAlias("DELL", effective_start=2024-09-23, effective_end=None)

    Immutable, versioned the same way as SecurityIdentity.
    """

    security_id: str
    ticker: str
    effective_start: Optional[date]
    effective_end: Optional[date]
    source_reference: str = ""

    def __post_init__(self) -> None:
        if not self.security_id:
            raise ValueError("security_id must be non-empty")
        if not self.ticker:
            raise ValueError("ticker must be non-empty")
        if (
            self.effective_start is not None
            and self.effective_end is not None
            and self.effective_start > self.effective_end
        ):
            raise ValueError("effective_start must not be after effective_end")

    def covers(self, as_of_date: date) -> bool:
        if self.effective_start is not None and as_of_date < self.effective_start:
            return False
        if self.effective_end is not None and as_of_date > self.effective_end:
            return False
        return True

    def overlaps(self, other: "TickerAlias") -> bool:
        if self.ticker != other.ticker:
            return False
        start_a = self.effective_start
        end_a = self.effective_end
        start_b = other.effective_start
        end_b = other.effective_end
        # Treat None as unbounded for the purpose of overlap detection.
        if end_a is not None and start_b is not None and end_a < start_b:
            return False
        if end_b is not None and start_a is not None and end_b < start_a:
            return False
        return True


class ResolutionOutcome(Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class IdentityResolution:
    """The result of resolving (ticker, as_of_date) to a security identity.

    Exactly one of the three outcomes always applies, and the object's
    own invariants (enforced in __post_init__) make it structurally
    impossible to construct a RESOLVED result without both an identity
    and the alias that produced it, or a non-RESOLVED result that still
    carries an identity as if it were resolved.
    """

    outcome: ResolutionOutcome
    ticker: str
    as_of_date: date
    identity: Optional[SecurityIdentity] = None
    alias: Optional[TickerAlias] = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.outcome is ResolutionOutcome.RESOLVED:
            if self.identity is None or self.alias is None:
                raise ValueError(
                    "a RESOLVED outcome must carry both identity and alias"
                )
        else:
            if self.identity is not None or self.alias is not None:
                raise ValueError(
                    f"a {self.outcome.value} outcome must not carry an identity "
                    "or alias — never guess"
                )
            if not self.detail:
                raise ValueError(
                    f"a {self.outcome.value} outcome must carry an explanatory "
                    "detail for the audit trail"
                )


class IdentityResolver(ABC):
    """Resolves a (ticker, as_of_date) pair to a SecurityIdentity, or to
    an explicit AMBIGUOUS/UNRESOLVED outcome. Never guesses.

    No implementation in this module is wired to a real, production
    identity data source. Concrete implementations used in tests operate
    on synthetic, in-memory fixtures only.
    """

    @abstractmethod
    def resolve(self, ticker: str, as_of_date: date) -> IdentityResolution:
        raise NotImplementedError


class StaticAliasIdentityResolver(IdentityResolver):
    """Reference IdentityResolver over a fixed, in-memory set of aliases.

    Test/scaffolding use only — the alias set is supplied by the caller
    (e.g. a unit test encoding the verified DELL boundary dates as a
    synthetic fixture) and is never fetched from a real data source by
    this class.
    """

    def __init__(
        self,
        identities: Sequence[SecurityIdentity],
        aliases: Sequence[TickerAlias],
    ) -> None:
        self._identities = {identity.security_id: identity for identity in identities}
        self._aliases: Tuple[TickerAlias, ...] = tuple(aliases)
        self._validate_no_undisclosed_overlap()

    def _validate_no_undisclosed_overlap(self) -> None:
        # Two aliases for the same ticker under *different* security_ids
        # must never overlap in time — that would be an internally
        # inconsistent fixture, not a real-world ambiguity to represent
        # at resolution time. Fail loudly at construction, not silently
        # at query time.
        for i, a in enumerate(self._aliases):
            for b in self._aliases[i + 1 :]:
                if a.security_id == b.security_id:
                    continue
                if a.overlaps(b):
                    raise ValueError(
                        f"inconsistent fixture: aliases for ticker "
                        f"{a.ticker!r} under distinct security_ids "
                        f"{a.security_id!r} and {b.security_id!r} overlap"
                    )

    def resolve(self, ticker: str, as_of_date: date) -> IdentityResolution:
        matches: Tuple[TickerAlias, ...] = tuple(
            alias
            for alias in self._aliases
            if alias.ticker == ticker and alias.covers(as_of_date)
        )
        if not matches:
            return IdentityResolution(
                outcome=ResolutionOutcome.UNRESOLVED,
                ticker=ticker,
                as_of_date=as_of_date,
                detail=(
                    f"no ticker alias for {ticker!r} covers {as_of_date.isoformat()}"
                ),
            )
        if len(matches) > 1:
            return IdentityResolution(
                outcome=ResolutionOutcome.AMBIGUOUS,
                ticker=ticker,
                as_of_date=as_of_date,
                detail=(
                    f"{len(matches)} distinct aliases for {ticker!r} cover "
                    f"{as_of_date.isoformat()}; refusing to guess"
                ),
            )
        alias = matches[0]
        identity = self._identities.get(alias.security_id)
        if identity is None:
            return IdentityResolution(
                outcome=ResolutionOutcome.UNRESOLVED,
                ticker=ticker,
                as_of_date=as_of_date,
                detail=(
                    f"alias for {ticker!r} references unknown "
                    f"security_id {alias.security_id!r}"
                ),
            )
        return IdentityResolution(
            outcome=ResolutionOutcome.RESOLVED,
            ticker=ticker,
            as_of_date=as_of_date,
            identity=identity,
            alias=alias,
        )
