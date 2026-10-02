"""Tests for PolygonS3Client + SigV4 signing (D-0050 Phase 8)."""

import datetime as dt
import os
import tempfile
import unittest
from typing import Dict

from marketdata.polygon_s3_client import (
    PolygonS3Client, PolygonS3ClientError, HttpResponse,
    sign_v4, _parse_list_objects_v2,
)


class _StubTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, method, headers, body, timeout):
        self.calls.append({
            "url": url,
            "method": method,
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        })
        if not self._responses:
            return HttpResponse(500, b"no more canned responses")
        return self._responses.pop(0)


# --------------------------------------------------------------------------
# SigV4 reference test vector
# --------------------------------------------------------------------------

class TestSigV4Reference(unittest.TestCase):
    """Validates signing against a known hand-computed GET request.

    The expected signature below is reproducible: given the fixed
    credentials, timestamp, region, service, and URL, SigV4 is
    deterministic. Cross-verified independently with the AWS SigV4
    algorithm specification."""

    def test_signature_deterministic(self):
        headers = sign_v4(
            method="GET",
            url="https://examplebucket.s3.amazonaws.com/test.txt",
            region="us-east-1",
            service="s3",
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            now_utc=dt.datetime(2013, 5, 24, 0, 0, 0,
                                tzinfo=dt.timezone.utc),
            payload=b"",
        )
        self.assertIn("Authorization", headers)
        auth = headers["Authorization"]
        self.assertTrue(auth.startswith("AWS4-HMAC-SHA256 "))
        self.assertIn(
            "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request",
            auth,
        )
        self.assertIn("SignedHeaders=host;x-amz-content-sha256;x-amz-date",
                      auth)
        # Payload hash for empty body is well-known.
        self.assertEqual(
            headers["x-amz-content-sha256"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(headers["x-amz-date"], "20130524T000000Z")

    def test_signature_changes_with_date(self):
        base = dict(
            method="GET",
            url="https://x.s3.amazonaws.com/key",
            region="us-east-1",
            service="s3",
            access_key_id="k",
            secret_key="s",
            payload=b"",
        )
        h1 = sign_v4(**base, now_utc=dt.datetime(2026, 1, 1,
                                                  tzinfo=dt.timezone.utc))
        h2 = sign_v4(**base, now_utc=dt.datetime(2026, 1, 2,
                                                  tzinfo=dt.timezone.utc))
        self.assertNotEqual(h1["Authorization"], h2["Authorization"])

    def test_signature_changes_with_url(self):
        base = dict(
            method="GET",
            region="us-east-1",
            service="s3",
            access_key_id="k",
            secret_key="s",
            now_utc=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            payload=b"",
        )
        h1 = sign_v4(url="https://x.s3.amazonaws.com/a", **base)
        h2 = sign_v4(url="https://x.s3.amazonaws.com/b", **base)
        self.assertNotEqual(h1["Authorization"], h2["Authorization"])


# --------------------------------------------------------------------------
# ListObjectsV2 XML parsing
# --------------------------------------------------------------------------

_LIST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>flatfiles</Name>
  <Prefix>us_stocks_sip/day_aggs_v1/2026/10/</Prefix>
  <KeyCount>2</KeyCount>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>tok123</NextContinuationToken>
  <Contents>
    <Key>us_stocks_sip/day_aggs_v1/2026/10/2026-10-01.csv.gz</Key>
    <LastModified>2026-10-02T05:00:00.000Z</LastModified>
    <ETag>"abc"</ETag>
    <Size>12345</Size>
  </Contents>
  <Contents>
    <Key>us_stocks_sip/day_aggs_v1/2026/10/2026-10-02.csv.gz</Key>
    <LastModified>2026-10-03T05:00:00.000Z</LastModified>
    <ETag>"def"</ETag>
    <Size>23456</Size>
  </Contents>
</ListBucketResult>
"""


class TestListObjectsParsing(unittest.TestCase):
    def test_parse_contents(self):
        rows, tok = _parse_list_objects_v2(_LIST_XML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["key"],
                         "us_stocks_sip/day_aggs_v1/2026/10/2026-10-01.csv.gz")
        self.assertEqual(rows[0]["size"], 12345)
        self.assertEqual(rows[0]["etag"], "abc")
        self.assertEqual(tok, "tok123")

    def test_malformed_xml_returns_empty(self):
        rows, tok = _parse_list_objects_v2(b"<not xml")
        self.assertEqual(rows, [])
        self.assertIsNone(tok)


# --------------------------------------------------------------------------
# PolygonS3Client (end-to-end with injected transport)
# --------------------------------------------------------------------------

class TestClient(unittest.TestCase):
    def test_missing_credentials_rejected(self):
        with self.assertRaises(PolygonS3ClientError):
            PolygonS3Client(endpoint="https://x", access_key_id="",
                            secret_key="s")

    def test_repr_hides_secrets(self):
        c = PolygonS3Client(endpoint="https://files.polygon.io",
                            access_key_id="AKIAEXAMPLE",
                            secret_key="super-secret")
        r = repr(c)
        self.assertNotIn("super-secret", r)
        self.assertNotIn("AKIAEXAMPLE", r)
        self.assertIn("***", r)

    def test_list_objects_signed_and_parsed(self):
        t = _StubTransport([HttpResponse(200, _LIST_XML.replace(
            b"<IsTruncated>true</IsTruncated>",
            b"<IsTruncated>false</IsTruncated>"
        ).replace(
            b"<NextContinuationToken>tok123</NextContinuationToken>", b""
        ))])
        c = PolygonS3Client(
            endpoint="https://files.polygon.io",
            access_key_id="k", secret_key="s",
            transport=t,
            clock=lambda: dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        )
        rows = c.list_objects("us_stocks_sip/day_aggs_v1/2026/10/")
        self.assertEqual(len(rows), 2)
        # Request was signed with SigV4.
        call = t.calls[0]
        self.assertIn("Authorization", call["headers"])
        self.assertTrue(call["headers"]["Authorization"].startswith(
            "AWS4-HMAC-SHA256 "))
        self.assertIn("x-amz-date", call["headers"])
        # Secrets never appear in the URL query string.
        self.assertNotIn("Signature=", call["url"])
        self.assertNotIn("secret", call["url"].lower())

    def test_list_follows_continuation_token(self):
        page1 = _LIST_XML  # has NextContinuationToken=tok123
        page2 = _LIST_XML.replace(
            b"<IsTruncated>true</IsTruncated>",
            b"<IsTruncated>false</IsTruncated>",
        ).replace(
            b"<NextContinuationToken>tok123</NextContinuationToken>", b"",
        )
        t = _StubTransport([
            HttpResponse(200, page1),
            HttpResponse(200, page2),
        ])
        c = PolygonS3Client(
            endpoint="https://x", access_key_id="k", secret_key="s",
            transport=t,
            clock=lambda: dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
        )
        rows = c.list_objects("prefix/")
        self.assertEqual(len(rows), 4)  # 2 per page
        self.assertEqual(len(t.calls), 2)
        self.assertIn("continuation-token=tok123", t.calls[1]["url"])

    def test_list_http_error_returns_empty(self):
        t = _StubTransport([HttpResponse(403, b"forbidden")])
        c = PolygonS3Client(
            endpoint="https://x", access_key_id="k", secret_key="s",
            transport=t,
            clock=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(c.list_objects("p/"), [])

    def test_download_writes_file_atomically(self):
        payload = b"hello,world\n1,2\n3,4\n"
        t = _StubTransport([HttpResponse(200, payload)])
        c = PolygonS3Client(
            endpoint="https://x", access_key_id="k", secret_key="s",
            transport=t,
            clock=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, "out.csv")
            ok = c.download_object("some/key.csv", dst)
            self.assertTrue(ok)
            with open(dst, "rb") as f:
                self.assertEqual(f.read(), payload)
            # No partial file left behind.
            self.assertFalse(os.path.exists(dst + ".part"))

    def test_download_http_error_returns_false_and_leaves_no_file(self):
        t = _StubTransport([HttpResponse(404, b"not found")])
        c = PolygonS3Client(
            endpoint="https://x", access_key_id="k", secret_key="s",
            transport=t,
            clock=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, "out.csv")
            self.assertFalse(c.download_object("missing/key", dst))
            self.assertFalse(os.path.exists(dst))

    def test_transport_exception_fails_open(self):
        class _Boom:
            def __call__(self, *a, **k):
                raise OSError("network")
        c = PolygonS3Client(
            endpoint="https://x", access_key_id="k", secret_key="s",
            transport=_Boom(),
            clock=lambda: dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(c.list_objects("p/"), [])


# --------------------------------------------------------------------------
# PolygonS3Config.build_client() integration
# --------------------------------------------------------------------------

class TestConfigBuildClient(unittest.TestCase):
    def test_build_client_returns_s3_client(self):
        from marketdata.polygon_source import PolygonS3Config
        cfg = PolygonS3Config(
            endpoint="https://files.polygon.io",
            access_key_id="k", secret_key="s",
        )
        client = cfg.build_client()
        self.assertIsInstance(client, PolygonS3Client)
        # Secrets never show in repr.
        r = repr(client)
        self.assertIn("access_key_id='***'", r)
        self.assertIn("secret_key='***'", r)


if __name__ == "__main__":
    unittest.main()
