#!/usr/bin/env python3
"""Client locale per l'istanza self-hosted di Firecrawl (ricerca e scraping).

Elimina la dipendenza da curl e jq su Linux e Windows usando solo Python standard.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class FirecrawlClient:
    """Client per le API locali di Firecrawl."""

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
            except Exception:
                return {"success": False, "error": f"HTTP {exc.code}: {err_body}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def check_status(self) -> dict[str, Any]:
        """Verifica se il servizio risponde, senza chiedergli di lavorare.

        Un controllo di raggiungibilità che avvia uno scrape vero consuma una
        chiamata reale e può fallire per ragioni che non c'entrano con
        "il servizio è vivo". Basta interrogare la radice.
        """
        req = urllib.request.Request(f"{self.api_url}/", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"success": True, "http_status": resp.status}
        except urllib.error.HTTPError as exc:
            # Anche un 3xx o un 4xx significa che qualcuno ha risposto.
            return {"success": exc.code < 500, "http_status": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def scrape(self, url: str, formats: list[str] | None = None) -> dict[str, Any]:
        """Esegue lo scrape di un URL."""
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
        """Esegue una ricerca web tramite Firecrawl."""
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
    """Rende i risultati leggibili da un umano, non un blob JSON."""
    data = res.get("data", res)
    entries = data.get("web") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return json.dumps(data, indent=2)
    lines: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {item.get('title') or '(senza titolo)'}")
        if item.get("url"):
            lines.append(f"  {item['url']}")
        if item.get("description"):
            lines.append(f"  {item['description']}")
    return "\n".join(lines) if lines else json.dumps(data, indent=2)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Firecrawl Local CLI (v2)")
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="Controlla lo stato del servizio")

    # scrape
    p_scrape = subparsers.add_parser("scrape", help="Esegue lo scrape di un URL")
    p_scrape.add_argument("url", help="URL da scaricare")
    p_scrape.add_argument("-f", "--format", default="markdown", help="Formati separati da virgola (markdown, links)")
    p_scrape.add_argument("--json", action="store_true", help="Output JSON grezzo")
    p_scrape.add_argument("-o", "--output", help="Salva l'output su file")

    # search
    p_search = subparsers.add_parser("search", help="Esegue una ricerca web")
    p_search.add_argument("query", nargs="+", help="Termini di ricerca")
    p_search.add_argument("--limit", type=int, default=20, help="Numero massimo di risultati")
    p_search.add_argument("--sources", help="Sorgenti separate da virgola (web, news, images)")
    p_search.add_argument("--scrape", action="store_true", help="Scarica anche il contenuto dei risultati")
    p_search.add_argument("--scrape-formats", default="markdown", help="Formati di scraping")
    p_search.add_argument("--json", action="store_true", help="Output JSON grezzo")
    p_search.add_argument("-o", "--output", help="Salva l'output su file")

    args = parser.parse_args(argv)
    client = FirecrawlClient()

    if args.command == "status":
        res = client.check_status()
        # Chi lancia `status` sta diagnosticando: gli serve sapere dove ha
        # bussato e con quale chiave, non solo se ha risposto.
        print(f"url:  {client.api_url}")
        print(f"auth: {'FIRECRAWL_API_KEY' if os.environ.get('FIRECRAWL_API_KEY') else 'default locale'}")
        if res.get("http_status"):
            print(f"http: {res['http_status']}")
        if res.get("success"):
            print("stato: attivo")
            return 0
        print(f"stato: non raggiungibile ({res.get('error', 'errore sconosciuto')})")
        return 1

    elif args.command == "scrape":
        formats = [f.strip() for f in args.format.split(",") if f.strip()]
        res = client.scrape(args.url, formats=formats)
        # Con un solo formato richiesto si stampa quel formato, non sempre
        # markdown: chiedere `--format links` e ricevere markdown è una bugia.
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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
