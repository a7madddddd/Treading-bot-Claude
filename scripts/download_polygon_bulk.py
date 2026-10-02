"""Download Polygon.io bulk flat-files (D-0050 Phase 8).

Thin CLI around PolygonS3Client for backtesting data acquisition.

Reads credentials from env:
  POLYGON_S3_ENDPOINT (e.g. https://files.polygon.io)
  POLYGON_S3_ACCESS_KEY_ID
  POLYGON_S3_SECRET_KEY

Usage examples:

  # List one day's available files under us_stocks_sip day_aggs
  python3 scripts/download_polygon_bulk.py list \\
    --prefix us_stocks_sip/day_aggs_v1/2026/10/

  # Download one specific file
  python3 scripts/download_polygon_bulk.py get \\
    --key us_stocks_sip/day_aggs_v1/2026/10/2026-10-01.csv.gz \\
    --out ./data/polygon/2026-10-01.csv.gz

  # Bulk-download every file matching a prefix (idempotent: skips
  # files already on disk at the correct size).
  python3 scripts/download_polygon_bulk.py sync \\
    --prefix us_stocks_sip/day_aggs_v1/2026/10/ \\
    --out-dir ./data/polygon/
"""

from __future__ import annotations

import argparse
import os
import sys

# Make src/ importable when the script is run directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from marketdata.polygon_source import PolygonS3Config  # noqa: E402


def _get_client():
    cfg = PolygonS3Config.from_env()
    if cfg is None:
        print("ERROR: POLYGON_S3_ENDPOINT / POLYGON_S3_ACCESS_KEY_ID / "
              "POLYGON_S3_SECRET_KEY must all be set.",
              file=sys.stderr)
        sys.exit(2)
    return cfg.build_client()


def cmd_list(args: argparse.Namespace) -> int:
    client = _get_client()
    rows = client.list_objects(args.prefix, max_pages=args.max_pages)
    if not rows:
        print("(no objects or request failed)", file=sys.stderr)
        return 1
    for r in rows:
        size = r.get("size") or 0
        print(f"{size:>12}  {r['key']}")
    print(f"\n{len(rows)} objects.", file=sys.stderr)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    client = _get_client()
    ok = client.download_object(args.key, args.out)
    if ok:
        size = os.path.getsize(args.out)
        print(f"OK: {args.key} -> {args.out} ({size:,} bytes)")
        return 0
    print(f"FAIL: could not download {args.key}", file=sys.stderr)
    return 1


def cmd_sync(args: argparse.Namespace) -> int:
    client = _get_client()
    rows = client.list_objects(args.prefix, max_pages=args.max_pages)
    if not rows:
        print("(no objects found; nothing to sync)", file=sys.stderr)
        return 1
    os.makedirs(args.out_dir, exist_ok=True)
    ok_count = 0
    skip_count = 0
    fail_count = 0
    for r in rows:
        key = r["key"]
        size_expected = r.get("size")
        # Flatten the key into a filename; preserve path structure.
        local = os.path.join(args.out_dir, key)
        if os.path.exists(local) and size_expected is not None \
           and os.path.getsize(local) == size_expected:
            skip_count += 1
            continue
        if client.download_object(key, local):
            ok_count += 1
            print(f"✓ {key}")
        else:
            fail_count += 1
            print(f"✗ {key}", file=sys.stderr)
    print(f"\nSynced {ok_count}, skipped {skip_count}, failed {fail_count}.",
          file=sys.stderr)
    return 0 if fail_count == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Polygon.io S3 bulk downloader")
    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="List objects under a prefix.")
    lp.add_argument("--prefix", required=True)
    lp.add_argument("--max-pages", type=int, default=50)
    lp.set_defaults(func=cmd_list)

    gp = sub.add_parser("get", help="Download one object.")
    gp.add_argument("--key", required=True)
    gp.add_argument("--out", required=True)
    gp.set_defaults(func=cmd_get)

    sp = sub.add_parser("sync", help="Mirror a prefix to a local directory.")
    sp.add_argument("--prefix", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--max-pages", type=int, default=50)
    sp.set_defaults(func=cmd_sync)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
