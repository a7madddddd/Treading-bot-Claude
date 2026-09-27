"""Perplexity Agent API adapter (D-0019, D-0037, D-0046).

Uses `POST https://api.perplexity.ai/v1/responses`. Never uses the
deprecated `/chat/completions` path — that endpoint returns
`agent_api_migration_required` and is CRITICAL.

Default request uses `preset: "medium"`; Perplexity picks the best
agent model available on that preset. An explicit `model` in
`provider/model` format overrides the preset when the caller wants
reproducible output.

Advisory-only per CLAUDE.md §5. Never places, cancels, or modifies
a trade. Callers get typed `ResearchReport` objects back.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Mapping, Optional, Tuple

from research.models import (
    Confidence,
    EvidenceSource,
    Finding,
    FindingLabel,
    ResearchOperation,
    ResearchReport,
)


_DEFAULT_ENDPOINT = "https://api.perplexity.ai/v1/responses"
_DEFAULT_PRESET = "medium"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_BASE_SECONDS = 1.0


class PerplexityConfigError(Exception):
    pass


class PerplexityTransportError(RuntimeError):
    """Raised on unrecoverable HTTP / network failure. Caller logs and
    continues without a fabricated summary (per research-sources.md §5)."""


class PerplexityMigrationRequiredError(RuntimeError):
    """Raised when Perplexity returns `code=agent_api_migration_required`.
    CRITICAL; blocks the caller's routine (per research-sources.md §7)."""


class HttpResponse:
    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


HttpTransport = Callable[[str, bytes, Mapping[str, str], float], HttpResponse]


def _urllib_transport(url: str, data: bytes, headers: Mapping[str, str],
                      timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=dict(headers))
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return HttpResponse(resp.status, resp.read())
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code, ex.read())


class PerplexityAgentClient:
    """Concrete adapter. All six D-0019 operations funnel through
    `_call_agent()` — a shared HTTP path with retries, error mapping,
    and structured-response parsing."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = _DEFAULT_ENDPOINT,
        preset: Optional[str] = _DEFAULT_PRESET,
        model: Optional[str] = None,
        transport: HttpTransport = _urllib_transport,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = _DEFAULT_BACKOFF_BASE_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_key.strip():
            raise PerplexityConfigError("PERPLEXITY_API_KEY missing or empty")
        if preset is None and model is None:
            raise PerplexityConfigError(
                "Either preset or explicit model must be provided"
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._preset = preset
        self._model = model
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._sleep_fn = sleep_fn

    # ---- named operations (D-0019 §1.A) -------------------------------

    def research_market(self, question: str, *,
                        subject_symbol: Optional[str] = None) -> ResearchReport:
        return self._call_agent(ResearchOperation.RESEARCH_MARKET, question,
                                subject_symbol=subject_symbol)

    def research_stock(self, ticker: str, question: str) -> ResearchReport:
        prefixed = f"[stock={ticker}] {question}"
        return self._call_agent(ResearchOperation.RESEARCH_STOCK, prefixed,
                                subject_symbol=ticker)

    def research_news(self, question: str, *,
                      subject_symbol: Optional[str] = None) -> ResearchReport:
        return self._call_agent(ResearchOperation.RESEARCH_NEWS, question,
                                subject_symbol=subject_symbol)

    def research_strategy(self, question: str) -> ResearchReport:
        return self._call_agent(ResearchOperation.RESEARCH_STRATEGY, question)

    def research_risk(self, question: str, *,
                      subject_symbol: Optional[str] = None) -> ResearchReport:
        return self._call_agent(ResearchOperation.RESEARCH_RISK, question,
                                subject_symbol=subject_symbol)

    def research_backtesting_method(self, question: str) -> ResearchReport:
        return self._call_agent(ResearchOperation.RESEARCH_BACKTESTING_METHOD,
                                question)

    # ---- shared HTTP path ---------------------------------------------

    def _build_payload(self, question: str) -> dict:
        payload = {"input": _build_structured_prompt(question)}
        if self._model is not None:
            payload["model"] = self._model
        else:
            payload["preset"] = self._preset
        return payload

    def _call_agent(self, operation: ResearchOperation, question: str, *,
                    subject_symbol: Optional[str] = None) -> ResearchReport:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data = json.dumps(self._build_payload(question)).encode("utf-8")

        last_error: Optional[str] = None
        for attempt in range(1, self._max_attempts + 1):
            resp = self._transport(self._endpoint, data, headers,
                                   self._timeout_seconds)
            parsed = _safe_json(resp.body)
            if resp.status == 200 and parsed is not None:
                return _report_from_payload(parsed, operation=operation,
                                            question=question,
                                            subject_symbol=subject_symbol)

            code = _extract_error_code(parsed)
            if code == "agent_api_migration_required":
                raise PerplexityMigrationRequiredError(
                    "Perplexity Agent API reports migration required; "
                    "blocking the routine (research-sources.md §7)."
                )
            if resp.status in (401, 403, 400, 404):
                # 400 is retryable only if it's a rate/quota body; treat
                # explicit validation failures as permanent.
                last_error = _error_snippet(parsed, resp)
                if resp.status == 400 and _is_retryable_400(parsed):
                    pass
                else:
                    raise PerplexityTransportError(
                        f"Perplexity permanent error (status={resp.status}): "
                        f"{last_error}"
                    )

            last_error = f"status={resp.status} {_error_snippet(parsed, resp)}"
            if attempt < self._max_attempts:
                self._sleep_fn(self._backoff_base_seconds * (2 ** (attempt - 1)))

        raise PerplexityTransportError(
            f"Perplexity transport failed after {self._max_attempts} attempts: "
            f"{last_error}"
        )


# ---- helpers -----------------------------------------------------------

def _build_structured_prompt(question: str) -> str:
    """Perplexity's Responses API accepts free-form input. We shape the
    prompt so the response contains the fields we parse back into
    `ResearchReport` (summary / findings / sources / risks / confidence
    / recommendation / experiment). The response format is tolerant:
    missing sections default to empty tuples."""

    return (
        "You are an advisory research assistant. Answer the question below.\n"
        "Return the answer in the following EXACT sections, each on its own "
        "line, in this order:\n"
        "SUMMARY: <2-4 sentences>\n"
        "FINDINGS:\n"
        "- <LABEL> <one finding>\n"
        "(where LABEL is one of FACT, SOURCE, INFERENCE, HYPOTHESIS, "
        "RECOMMENDATION; one bullet per line; include as many as apply)\n"
        "RISKS:\n"
        "- <one risk>\n"
        "CONFIDENCE: <LOW|MEDIUM|HIGH>\n"
        "RECOMMENDATION: <one sentence, may be blank>\n"
        "SUGGESTED_EXPERIMENT: <one sentence, may be blank>\n\n"
        f"QUESTION: {question}\n"
    )


def _safe_json(body: bytes) -> Optional[dict]:
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _extract_error_code(parsed: Optional[dict]) -> Optional[str]:
    if not isinstance(parsed, dict):
        return None
    err = parsed.get("error")
    if isinstance(err, dict):
        return err.get("code") or err.get("type")
    if isinstance(err, str):
        return err
    return None


def _error_snippet(parsed: Optional[dict], resp: HttpResponse) -> str:
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            return err.get("message", str(err))[:250]
        if err is not None:
            return str(err)[:250]
    return resp.body[:250].decode("utf-8", errors="replace")


def _is_retryable_400(parsed: Optional[dict]) -> bool:
    if not isinstance(parsed, dict):
        return False
    err = parsed.get("error")
    if isinstance(err, dict):
        msg = (err.get("message") or "").lower()
        if "rate" in msg or "quota" in msg or "temporary" in msg:
            return True
    return False


def _extract_output_text(payload: dict) -> str:
    """Perplexity Responses shape: `output` is a list; each item may
    have a `content` list whose items carry `text` (type=output_text)."""

    out = payload.get("output")
    if not isinstance(out, list):
        return ""
    chunks: List[str] = []
    for item in out:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for piece in content:
            if isinstance(piece, dict) and piece.get("type") == "output_text":
                text = piece.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks)


def _extract_annotations(payload: dict) -> Tuple[EvidenceSource, ...]:
    """Perplexity annotations carry citation URLs. Shape varies; we
    handle both `annotations[].url` and `annotations[].url_citation.url`."""

    out = payload.get("output")
    if not isinstance(out, list):
        return ()
    sources: List[EvidenceSource] = []
    now = datetime.now(timezone.utc)
    for item in out:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for piece in content:
            if not isinstance(piece, dict):
                continue
            anns = piece.get("annotations", [])
            if not isinstance(anns, list):
                continue
            for a in anns:
                if not isinstance(a, dict):
                    continue
                url = a.get("url")
                title = a.get("title", "")
                if not url and isinstance(a.get("url_citation"), dict):
                    url = a["url_citation"].get("url")
                    title = a["url_citation"].get("title", title)
                if isinstance(url, str) and url:
                    sources.append(EvidenceSource(
                        url=url, title=title or url,
                        retrieval_timestamp=now,
                    ))
    return tuple(sources)


def _parse_structured_answer(text: str, sources: Tuple[EvidenceSource, ...]
                             ) -> dict:
    """Parses the SUMMARY/FINDINGS/RISKS/CONFIDENCE/RECOMMENDATION/
    SUGGESTED_EXPERIMENT layout the prompt asks for. Missing sections
    become empty. Never raises."""

    summary_lines: List[str] = []
    finding_lines: List[str] = []
    risk_lines: List[str] = []
    confidence = "LOW"
    recommendation: Optional[str] = None
    experiment: Optional[str] = None

    section = "SUMMARY"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.split(":", 1)[0].strip().upper() if ":" in line else ""
        if upper in ("SUMMARY", "FINDINGS", "RISKS", "CONFIDENCE",
                     "RECOMMENDATION", "SUGGESTED_EXPERIMENT"):
            section = upper
            payload = line.split(":", 1)[1].strip() if ":" in line else ""
            if section == "SUMMARY" and payload:
                summary_lines.append(payload)
            elif section == "CONFIDENCE" and payload:
                confidence = payload.upper().split()[0]
            elif section == "RECOMMENDATION" and payload:
                recommendation = payload
            elif section == "SUGGESTED_EXPERIMENT" and payload:
                experiment = payload
            continue

        if section == "SUMMARY":
            summary_lines.append(line)
        elif section == "FINDINGS":
            if line.startswith("-") or line.startswith("*"):
                finding_lines.append(line.lstrip("-* ").strip())
        elif section == "RISKS":
            if line.startswith("-") or line.startswith("*"):
                risk_lines.append(line.lstrip("-* ").strip())
        elif section == "RECOMMENDATION" and recommendation is None:
            recommendation = line
        elif section == "SUGGESTED_EXPERIMENT" and experiment is None:
            experiment = line

    findings = tuple(_finding_from_bullet(b, sources) for b in finding_lines if b)
    return {
        "summary": " ".join(summary_lines).strip(),
        "findings": findings,
        "risks": tuple(r for r in risk_lines if r),
        "confidence": Confidence(_normalize_confidence(confidence)),
        "recommendation": recommendation,
        "suggested_experiment": experiment,
    }


def _normalize_confidence(raw: str) -> str:
    up = (raw or "").strip().upper()
    if up in ("LOW", "MEDIUM", "HIGH"):
        return up
    return "LOW"


def _finding_from_bullet(bullet: str, sources: Tuple[EvidenceSource, ...]
                         ) -> Finding:
    label = FindingLabel.INFERENCE
    text = bullet
    for candidate in FindingLabel:
        prefix = candidate.value + " "
        if bullet.startswith(prefix):
            label = candidate
            text = bullet[len(prefix):].strip()
            break
        prefix_c = candidate.value + ":"
        if bullet.startswith(prefix_c):
            label = candidate
            text = bullet[len(prefix_c):].strip()
            break
    return Finding(label=label, text=text or bullet,
                   supporting_sources=sources if label is FindingLabel.SOURCE
                                     else ())


def _report_from_payload(payload: dict, *, operation: ResearchOperation,
                         question: str, subject_symbol: Optional[str]
                         ) -> ResearchReport:
    text = _extract_output_text(payload)
    sources = _extract_annotations(payload)
    parsed = _parse_structured_answer(text, sources)

    return ResearchReport(
        report_id=f"rr-{uuid.uuid4().hex[:12]}",
        operation=operation,
        question=question,
        summary=parsed["summary"] or text.strip()[:500],
        findings=parsed["findings"],
        sources=sources,
        risks=parsed["risks"],
        confidence=parsed["confidence"],
        recommendation=parsed["recommendation"],
        suggested_experiment=parsed["suggested_experiment"],
        model_served=str(payload.get("model", "unknown")),
        generated_at=datetime.now(timezone.utc),
        subject_symbol=subject_symbol,
    )
