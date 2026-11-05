"""SEC EDGAR public data APIs (no API key).

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

Fair-access requirements honoured here:
  * descriptive User-Agent with contact info (SEC_USER_AGENT),
  * <= 10 requests/second (SEC_MIN_INTERVAL_SECONDS spacing),
  * caching of ticker map, submissions and company facts.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.config import get_settings
from app.providers.base import MarketDataProvider, SymbolNotFound
from app.providers.http import get_json

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"

RELEVANT_FORMS = {"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "20-F", "40-F"}


def pad_cik(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


class SecEdgarProvider(MarketDataProvider):
    name = "sec_edgar"
    capabilities = frozenset({"fundamentals", "filings", "cik_mapping"})

    def __init__(self) -> None:
        s = get_settings()
        self.headers = {"User-Agent": s.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        self.min_interval = s.sec_min_interval_seconds

    def _get(self, url: str, ttl: int) -> Any:
        return get_json(
            url,
            headers=self.headers,
            throttle_key="sec_edgar",
            min_interval=self.min_interval,
            cache_ttl=ttl,
            cache_prefix="sec",
        )

    def ticker_map(self) -> dict[str, dict]:
        """{TICKER: {cik, title}} using SEC's ticker file (refreshed daily)."""
        raw = self._get(TICKER_MAP_URL, ttl=24 * 3600)
        out: dict[str, dict] = {}
        for row in raw.values():
            t = str(row["ticker"]).upper().replace(".", "-")
            out[t] = {"cik": pad_cik(row["cik_str"]), "title": row.get("title")}
        return out

    def lookup_cik(self, symbol: str) -> str | None:
        entry = self.ticker_map().get(symbol.upper().replace(".", "-"))
        return entry["cik"] if entry else None

    def get_submissions(self, cik: str) -> dict:
        return self._get(SUBMISSIONS_URL.format(cik=pad_cik(cik)), ttl=6 * 3600)

    def get_company_facts(self, cik: str) -> dict:
        return self._get(COMPANY_FACTS_URL.format(cik=pad_cik(cik)), ttl=12 * 3600)

    def recent_filings(self, cik: str, forms: set[str] | None = None, since: date | None = None) -> list[dict]:
        forms = forms or RELEVANT_FORMS
        sub = self.get_submissions(cik)
        recent = (sub.get("filings") or {}).get("recent") or {}
        if not recent:
            raise SymbolNotFound(f"sec_edgar: no filings for CIK {cik}")
        out: list[dict] = []
        n = len(recent.get("accessionNumber", []))
        for i in range(n):
            form = recent["form"][i]
            if form not in forms:
                continue
            filed = date.fromisoformat(recent["filingDate"][i])
            if since and filed < since:
                continue
            acc = recent["accessionNumber"][i]
            doc = recent.get("primaryDocument", [""] * n)[i]
            report = recent.get("reportDate", [""] * n)[i]
            out.append(
                {
                    "accession_number": acc,
                    "form": form,
                    "filing_date": filed,
                    "report_date": date.fromisoformat(report) if report else None,
                    "primary_document": doc,
                    "url": ARCHIVE_URL.format(cik_int=int(cik), acc_nodash=acc.replace("-", ""), doc=doc) if doc else None,
                }
            )
        return out
