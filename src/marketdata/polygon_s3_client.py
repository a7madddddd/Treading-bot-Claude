"""Polygon.io S3 bulk flat-files client (D-0050 Phase 8).

Implements AWS Signature Version 4 request signing in stdlib so this
client stays dependency-free (no boto3). Polygon exposes an
S3-compatible endpoint at https://files.polygon.io with the bucket
``flatfiles``; access requires an access-key-id/secret pair with
SigV4 signing against region ``us-east-1`` and service ``s3``.

Capabilities:
  - list_objects(prefix, max_keys=1000) -> List[dict]
      returns [{"key": "...", "size": 123, "etag": "..."}, ...]
      walks continuation tokens so large prefixes are fully listed.
  - download_object(key, dest_path) -> bool
      streams one object to a local file; returns True on success.

Fail-open: any failure (network, 403, parse) returns [] / False
without raising. Secrets are never written to logs or returned
values; __repr__ hides both the access key id and the secret.

NOT wired into the trading loop (bulk historical only). The
intended consumer is scripts/download_polygon_bulk.py — a backtesting
helper that pulls day-aggregate CSV.gz files.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple


_DEFAULT_REGION = "us-east-1"
_DEFAULT_SERVICE = "s3"
_DEFAULT_BUCKET = "flatfiles"
_DEFAULT_TIMEOUT = 60.0
_ALGORITHM = "AWS4-HMAC-SHA256"


class PolygonS3ClientError(Exception):
    pass


# ---------------------------------------------------------------------------
# SigV4 signing
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(secret_key: str, date_stamp: str,
                        region: str, service: str) -> bytes:
    """Standard SigV4 key derivation chain."""
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "aws4_request")
    return k_signing


def _canonical_query(params: Mapping[str, str]) -> str:
    """SigV4 requires URI-encoded key=value pairs sorted by key."""
    pairs = []
    for k in sorted(params):
        v = params[k]
        pairs.append(f"{urllib.parse.quote(k, safe='-_.~')}="
                     f"{urllib.parse.quote(str(v), safe='-_.~')}")
    return "&".join(pairs)


def _canonical_uri(path: str) -> str:
    """SigV4 S3 canonical URI: path segments percent-encoded except '/'."""
    if not path.startswith("/"):
        path = "/" + path
    # Encode each segment but keep '/' as separator.
    segments = path.split("/")
    encoded = [urllib.parse.quote(s, safe="-_.~") for s in segments]
    return "/".join(encoded)


def sign_v4(
    *,
    method: str,
    url: str,
    region: str,
    service: str,
    access_key_id: str,
    secret_key: str,
    now_utc: Optional[_dt.datetime] = None,
    extra_headers: Optional[Mapping[str, str]] = None,
    payload: bytes = b"",
) -> Dict[str, str]:
    """Returns the headers (including Authorization + x-amz-date +
    x-amz-content-sha256) a signed request must carry.

    Exposed at module scope so tests can validate signatures against
    AWS's published test vectors.
    """
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    amz_date = now_utc.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now_utc.strftime("%Y%m%d")

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.port and not (
        (parsed.scheme == "https" and parsed.port == 443) or
        (parsed.scheme == "http" and parsed.port == 80)
    ):
        host = f"{host}:{parsed.port}"
    canonical_uri = _canonical_uri(parsed.path or "/")
    query_params: Dict[str, str] = {}
    if parsed.query:
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            query_params[k] = v
    canonical_qs = _canonical_query(query_params)

    payload_hash = _sha256_hex(payload)

    base_headers: Dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if extra_headers:
        for k, v in extra_headers.items():
            base_headers[k.lower()] = v

    signed_header_names = sorted(base_headers)
    canonical_headers = "".join(
        f"{name}:{base_headers[name].strip()}\n" for name in signed_header_names
    )
    signed_headers = ";".join(signed_header_names)

    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_qs,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        _ALGORITHM,
        amz_date,
        credential_scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])

    signing_key = _derive_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key,
                         string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    authorization = (
        f"{_ALGORITHM} "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    out = dict(base_headers)
    out["Authorization"] = authorization
    return out


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

class HttpResponse:
    __slots__ = ("status", "body", "headers")

    def __init__(self, status: int, body: bytes,
                 headers: Optional[Mapping[str, str]] = None) -> None:
        self.status = status
        self.body = body
        self.headers = dict(headers or {})


HttpTransport = Callable[[str, str, Mapping[str, str], bytes, float], HttpResponse]


def _urllib_transport(url: str, method: str, headers: Mapping[str, str],
                      body: bytes, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body or None, method=method,
                                 headers=dict(headers))
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return HttpResponse(r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as ex:
        return HttpResponse(ex.code,
                            ex.read() if hasattr(ex, "read") else b"",
                            dict(ex.headers) if hasattr(ex, "headers") else {})


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

# S3 ListObjectsV2 responses use this namespace.
_S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


class PolygonS3Client:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key_id: str,
        secret_key: str,
        bucket: str = _DEFAULT_BUCKET,
        region: str = _DEFAULT_REGION,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        transport: HttpTransport = _urllib_transport,
        clock: Callable[[], _dt.datetime] = lambda: _dt.datetime.now(_dt.timezone.utc),
    ) -> None:
        if not endpoint:
            raise PolygonS3ClientError("endpoint is required")
        if not access_key_id or not secret_key:
            raise PolygonS3ClientError(
                "access_key_id and secret_key are required")
        self._endpoint = endpoint.rstrip("/")
        self._access_key_id = access_key_id
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock

    def _signed_get(self, path: str,
                    query: Optional[Mapping[str, str]] = None) -> HttpResponse:
        """Sends a signed GET. Fail-open on any transport error."""
        url = f"{self._endpoint}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(dict(query))
        headers = sign_v4(
            method="GET",
            url=url,
            region=self._region,
            service=_DEFAULT_SERVICE,
            access_key_id=self._access_key_id,
            secret_key=self._secret_key,
            now_utc=self._clock(),
            payload=b"",
        )
        try:
            return self._transport(url, "GET", headers, b"", self._timeout)
        except Exception as exc:  # noqa: BLE001
            # Normalize to a dummy 599 error; callers already fail-open.
            return HttpResponse(599, str(exc).encode(), {})

    # ---- list ---------------------------------------------------------

    def list_objects(self, prefix: str, *, max_keys: int = 1000,
                     max_pages: int = 50) -> List[Dict[str, object]]:
        """ListObjectsV2 under the given prefix. Walks continuation
        tokens up to ``max_pages``. Returns [] on any failure."""
        results: List[Dict[str, object]] = []
        continuation: Optional[str] = None
        for _ in range(max_pages):
            params = {
                "list-type": "2",
                "prefix": prefix,
                "max-keys": str(max_keys),
            }
            if continuation:
                params["continuation-token"] = continuation
            resp = self._signed_get(f"/{self._bucket}", params)
            if resp.status != 200:
                return results  # fail-open: return what we have
            page, continuation = _parse_list_objects_v2(resp.body)
            results.extend(page)
            if not continuation:
                break
        return results

    # ---- download -----------------------------------------------------

    def download_object(self, key: str, dest_path: str,
                        *, chunk_size: int = 1_048_576) -> bool:
        """Downloads `s3://<bucket>/<key>` to `dest_path`. Returns False
        on any failure (and removes any partial file)."""
        path = f"/{self._bucket}/{urllib.parse.quote(key, safe='/')}"
        resp = self._signed_get(path)
        if resp.status != 200:
            return False
        tmp = dest_path + ".part"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".",
                        exist_ok=True)
            with open(tmp, "wb") as f:
                # resp.body is the whole payload (urllib already read it).
                # chunk-write so very large bodies don't thrash memory.
                mv = memoryview(resp.body)
                for i in range(0, len(mv), chunk_size):
                    f.write(mv[i:i + chunk_size])
            os.replace(tmp, dest_path)
            return True
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    # ---- repr ---------------------------------------------------------

    def __repr__(self) -> str:
        return (f"PolygonS3Client(endpoint={self._endpoint!r}, "
                f"bucket={self._bucket!r}, access_key_id='***', "
                f"secret_key='***')")


def _parse_list_objects_v2(body: bytes) -> Tuple[List[Dict[str, object]],
                                                 Optional[str]]:
    """Parses an S3 ListObjectsV2 XML response. Returns (contents,
    next_continuation_token). Namespaced or not."""
    results: List[Dict[str, object]] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return results, None

    def _localname(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    next_token: Optional[str] = None
    for child in root:
        name = _localname(child.tag)
        if name == "Contents":
            key = None
            size = None
            etag = None
            for leaf in child:
                lname = _localname(leaf.tag)
                if lname == "Key":
                    key = leaf.text
                elif lname == "Size":
                    try:
                        size = int(leaf.text or "0")
                    except ValueError:
                        size = None
                elif lname == "ETag":
                    etag = (leaf.text or "").strip('"')
            if key:
                results.append({"key": key, "size": size, "etag": etag})
        elif name == "NextContinuationToken":
            next_token = child.text
        elif name == "IsTruncated":
            # Only trust NextContinuationToken; IsTruncated is informational.
            pass

    return results, next_token
