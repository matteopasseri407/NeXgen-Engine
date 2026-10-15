#!/usr/bin/env python3
"""Local client for the self-hosted Firecrawl instance (search and scraping).

Removes the dependency on curl and jq on Linux and Windows, using only the
Python standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t

DEFAULT_QUERY = '"Example Domain" site:example.com'


def read_cached_success(path: Path, ttl_seconds: int, now: float) -> dict[str, Any] | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        checked_at = float(cached["checked_at"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if cached.get("ok") is True and 0 <= now - checked_at < ttl_seconds:
        return cached
    return None


def extract_urls(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if isinstance(data, dict):
        rows = data.get("web", [])
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    return [
        row["url"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("url"), str)
    ]


def is_expected_result(url: str) -> bool:
    try:
        hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return hostname == "example.com" or hostname.endswith(".example.com")


def write_success(path: Path, now: float, result_count: int) -> None:
    # mkstemp makes the new file 0600, and an existing one keeps its mode.
    atomic_write_text(path, json.dumps(
        {"ok": True, "checked_at": now, "result_count": result_count},
        separators=(",", ":"),
    ) + "\n")


def probe(api_url: str, api_key: str, timeout: float) -> tuple[bool, int, str]:
    body = json.dumps({"query": DEFAULT_QUERY, "limit": 1}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/v2/search",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, 0, f"request failed: {type(exc).__name__}"

    if not isinstance(payload, dict) or payload.get("success") is not True:
        return False, 0, "Firecrawl returned an unsuccessful response"
    urls = extract_urls(payload)
    if not any(is_expected_result(url) for url in urls):
        return False, len(urls), "search returned no result from example.com"
    return True, len(urls), "ok"


def run_health_check(cache: Path, url: str, ttl_seconds: int, timeout: float, force: bool) -> int:
    now = time.time()
    if not force:
        cached = read_cached_success(cache, ttl_seconds, now)
        if cached:
            print(
                json.dumps(
                    {
                        "status": "cached",
                        "result_count": int(cached.get("result_count", 0)),
                    },
                    separators=(",", ":"),
                )
            )
            return 0

    api_key = os.environ.get("FIRECRAWL_API_KEY", "local-self-hosted")
    ok, result_count, detail = probe(url, api_key, timeout)
    if not ok:
        print(
            json.dumps(
                {"status": "failed", "result_count": result_count, "detail": detail},
                separators=(",", ":"),
            )
        )
        return 1

    write_success(cache, now, result_count)
    print(
        json.dumps(
            {"status": "live", "result_count": result_count},
            separators=(",", ":"),
        )
    )
    return 0


class FirecrawlClient:
    """Client for Firecrawl's local APIs."""

    def __init__(self, api_url: str | None = None, api_key: str | None = None) -> None:
        self.api_url = (api_url or os.environ.get("FIRECRAWL_API_URL") or "http://127.0.0.1:33002").rstrip("/")
        self.api_key = api_key or os.environ.get("FIRECRAWL_API_KEY") or "local-self-hosted"

    def _request(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.api_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", "replace")
            try:
                return json.loads(err_body)
            except Exception:  # noqa: BLE001 - provider failure falls back, never raises
                return {"success": False, "error": f"HTTP {exc.code}: {err_body}"}
        except Exception as exc:  # noqa: BLE001 - provider failure falls back, never raises
            return {"success": False, "error": str(exc)}

    def check_status(self) -> dict[str, Any]:
        """Checks whether the service responds, without asking it to do work.

        A reachability check that kicks off a real scrape consumes an
        actual call and can fail for reasons that have nothing to do with
        "is the service alive". Querying the root is enough.
        """
        req = urllib.request.Request(f"{self.api_url}/", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"success": True, "http_status": resp.status}
        except urllib.error.HTTPError as exc:
            # Even a 3xx or a 4xx means someone answered.
            return {"success": exc.code < 500, "http_status": exc.code}
        except Exception as exc:  # noqa: BLE001 - provider failure falls back, never raises
            return {"success": False, "error": str(exc)}

    def scrape(self, url: str, formats: list[str] | None = None) -> dict[str, Any]:
        """Scrapes a URL."""
        formats_list = formats or ["markdown"]
        payload = {"url": url, "formats": formats_list}
        return self._request("/v2/scrape", payload=payload)

    def search(
        self,
        query: str,
        limit: int = 20,
        sources: list[str] | None = None,
        scrape: bool = False,
        scrape_formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Runs a web search through Firecrawl."""
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if sources:
            payload["sources"] = sources
        if scrape:
            payload["scrapeOptions"] = {"formats": scrape_formats or ["markdown"]}

        return self._request("/v2/search", payload=payload)


def _format_results(res: dict[str, Any]) -> str:
    """Makes results readable by a human, not a JSON blob."""
    data = res.get("data", res)
    entries = data.get("web") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return json.dumps(data, indent=2)
    lines: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {item.get('title') or '(untitled)'}")
        if item.get("url"):
            lines.append(f"  {item['url']}")
        if item.get("description"):
            lines.append(f"  {item['description']}")
    return "\n".join(lines) if lines else json.dumps(data, indent=2)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(prog="nexgen tool firecrawl", description=t("Firecrawl Local CLI (v2)"))
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help=t("Check the service status"))

    # scrape
    p_scrape = subparsers.add_parser("scrape", help=t("Scrape a URL"))
    p_scrape.add_argument("url", help=t("URL to fetch"))
    p_scrape.add_argument("-f", "--format", default="markdown", help=t("Comma-separated formats (markdown, links)"))
    p_scrape.add_argument("--json", action="store_true", help=t("Raw JSON output"))
    p_scrape.add_argument("-o", "--output", help=t("Save the output to a file"))

    # search
    p_search = subparsers.add_parser("search", help=t("Run a web search"))
    p_search.add_argument("query", nargs="+", help=t("Search terms"))
    p_search.add_argument("--limit", type=int, default=20, help=t("Maximum number of results"))
    p_search.add_argument("--sources", help=t("Comma-separated sources (web, news, images)"))
    p_search.add_argument("--scrape", action="store_true", help=t("Also fetch the content of the results"))
    p_search.add_argument("--scrape-formats", default="markdown", help=t("Scraping formats"))
    p_search.add_argument("--json", action="store_true", help=t("Raw JSON output"))
    p_search.add_argument("-o", "--output", help=t("Save the output to a file"))

    # health (end-to-end search probe with a small success cache)
    p_health = subparsers.add_parser("health", help=t("Verify Firecrawl search end to end"))
    p_health.add_argument(
        "--url",
        default=os.environ.get("FIRECRAWL_API_URL", "http://127.0.0.1:33002"),
    )
    p_health.add_argument("--cache", type=Path, required=True)
    p_health.add_argument("--ttl-seconds", type=int, default=86400)
    p_health.add_argument("--timeout", type=float, default=20.0)
    p_health.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    client = FirecrawlClient()

    if args.command == "status":
        res = client.check_status()
        # Whoever runs `status` is diagnosing: they need to know where it
        # knocked and with which key, not just whether it answered.
        print(f"url:  {client.api_url}")
        print(f"auth: {'FIRECRAWL_API_KEY' if os.environ.get('FIRECRAWL_API_KEY') else t('local default')}")
        if res.get("http_status"):
            print(f"http: {res['http_status']}")
        if res.get("success"):
            print(t("status: up"))
            return 0
        print(t("status: unreachable ({error})", error=res.get("error") or t("unknown error")))
        return 1

    elif args.command == "scrape":
        formats = [f.strip() for f in args.format.split(",") if f.strip()]
        res = client.scrape(args.url, formats=formats)
        # With a single format requested, print that format, not always
        # markdown: asking for `--format links` and getting markdown is a lie.
        if args.json or len(formats) != 1:
            output_str = json.dumps(res, indent=2)
        else:
            extracted = res.get("data", {}).get(formats[0])
            output_str = extracted if isinstance(extracted, str) else json.dumps(res, indent=2)
        if args.output:
            Path(args.output).write_text(output_str, encoding="utf-8")
        else:
            print(output_str)
        return 0 if res.get("success", True) else 1

    elif args.command == "search":
        sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
        scrape_fmts = [f.strip() for f in args.scrape_formats.split(",") if f.strip()]
        query = " ".join(args.query)
        res = client.search(query, limit=args.limit, sources=sources, scrape=args.scrape, scrape_formats=scrape_fmts)
        if args.json:
            output_str = json.dumps(res, indent=2)
        else:
            output_str = _format_results(res)
        if args.output:
            Path(args.output).write_text(output_str, encoding="utf-8")
        else:
            print(output_str)
        return 0 if res.get("success", True) else 1

    elif args.command == "health":
        return run_health_check(
            cache=args.cache,
            url=args.url,
            ttl_seconds=args.ttl_seconds,
            timeout=args.timeout,
            force=args.force,
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
