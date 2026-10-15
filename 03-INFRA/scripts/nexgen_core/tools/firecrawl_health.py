#!/usr/bin/env python3
"""Check Firecrawl search end to end, with a small success cache.

Wrapper che delega a nexgen_core.tools.firecrawl (la logica vive li
come sottocomando interno `health`); firme pubbliche e comportamenti
invariati. Nota: per simulare probe e orologio nei test si agisce su
nexgen_core.tools.firecrawl, non su questo modulo.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from nexgen_core.tools.firecrawl import (
    DEFAULT_QUERY,
    extract_urls,
    is_expected_result,
    probe,
    read_cached_success,
    run_health_check,
    write_success,
)

__all__ = [
    "DEFAULT_QUERY",
    "extract_urls",
    "is_expected_result",
    "probe",
    "read_cached_success",
    "run_health_check",
    "write_success",
    "parse_args",
    "main",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Firecrawl search end to end and cache successful probes."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("FIRECRAWL_API_URL", "http://127.0.0.1:33002"),
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=86400)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_health_check(
        cache=args.cache,
        url=args.url,
        ttl_seconds=args.ttl_seconds,
        timeout=args.timeout,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
