"""Market data ingestion pipeline.

Universe -> symbol validation -> collection -> raw validation -> normalization ->
storage -> weekly/monthly aggregation -> indicator calculation.

Updates are incremental: only sessions after the last stored bar (plus a small
overlap used to detect provider revisions / dividend re-adjustments) are fetched.
A full history refresh is triggered automatically when adjusted prices change.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.indicators.compute import compute_daily, compute_monthly, compute_weekly, row_to_json
from app.market import calendar
from app.models import Stock
from app.pipeline import repository as repo
from app.pipeline.quality import detect_adjustment_change, validate_ohlcv
from app.pipeline.resample import to_monthly, to_weekly
from app.providers import registry
from app.providers.base import HistoryResult, ProviderError, SymbolNotFound

log = logging.getLogger(__name__)
SYMBOL_RE = re.compile(r"^\^?[A-Z][A-Z0-9\-]{0,9}$")
OVERLAP_DAYS = 10


class InvalidSymbol(ValueError):
    pass


@dataclass
class SymbolResult:
    symbol: str
    status: str  # UPDATED | UP_TO_DATE | FAILED | NO_DATA
    provider: str | None = None
    rows_written: int = 0
    first_new_date: date | None = None
    latest_date: date | None = None
    full_refresh: bool = False
    issues: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class IngestionReport:
    latest_session: date
    results: list[SymbolResult] = field(default_factory=list)

    def summary(self) -> dict:
        counts: dict[str, int] = defaultdict(int)
        for r in self.results:
            counts[r.status] += 1
        return {"latest_session": self.latest_session.isoformat(), "counts": dict(counts), "results": [r.__dict__ for r in self.results]}


def validate_symbol_format(symbol: str) -> str:
    s = repo.normalize_symbol(symbol)
    if not SYMBOL_RE.match(s):
        raise InvalidSymbol(f"invalid symbol format: {symbol!r}")
    return s


class ValidationUnavailable(RuntimeError):
    """Providers are unreachable, so a new symbol cannot be validated right now."""


def _require_market_data(db: Session, sym: str) -> None:
    """A new symbol must be known to at least one provider (metadata or recent bars)."""
    end = calendar.latest_completed_session()
    start = end - timedelta(days=14)
    unreachable = False
    for p in registry.price_providers():
        try:
            res = p.get_daily_history(sym, start, end)
            registry.record_success(db, p.name)
            if not res.frame.empty:
                return
        except SymbolNotFound:
            continue
        except ProviderError as exc:
            registry.record_failure(db, p.name, exc)
            unreachable = True
    if unreachable:
        raise ValidationUnavailable(f"could not validate {sym}: market data providers unavailable")
    raise InvalidSymbol(f"unknown symbol: {sym}")


def ensure_stock(db: Session, symbol: str, *, lookup_metadata: bool = True, validate: bool = True) -> Stock:
    """Return the stock row, creating it after validating with a provider."""
    sym = validate_symbol_format(symbol)
    stock = repo.get_stock(db, sym)
    if stock:
        return stock
    exchanges = repo.ensure_exchanges(db)
    meta = None
    if lookup_metadata:
        for p in registry.price_providers():
            if not p.supports("metadata"):
                continue
            try:
                meta = p.get_metadata(sym)
                registry.record_success(db, p.name)
                break
            except SymbolNotFound:
                continue
            except ProviderError as exc:
                registry.record_failure(db, p.name, exc)
                continue
    if meta is None and validate:
        _require_market_data(db, sym)
    asset_type = (meta.asset_type if meta and meta.asset_type else ("INDEX" if sym.startswith("^") else "EQUITY"))
    stock = Stock(
        symbol=sym,
        name=meta.name if meta else None,
        exchange_id=exchanges.get(meta.exchange) if meta and meta.exchange else (exchanges["INDEX"] if sym.startswith("^") else None),
        asset_type=asset_type,
        currency=(meta.currency or "USD") if meta else "USD",
        sector=meta.sector if meta else None,
        industry=meta.industry if meta else None,
        shares_outstanding=meta.shares_outstanding if meta else None,
        metadata_source=meta.provider if meta else None,
        is_tracked=True,
    )
    db.add(stock)
    db.flush()
    return stock


def _fetch(db: Session, symbols: list[str], start: date, end: date) -> dict[str, HistoryResult | Exception]:
    """Batch fetch from the highest-priority provider, falling back per symbol."""
    providers = registry.price_providers()
    out: dict[str, HistoryResult | Exception] = {}
    if not providers:
        err = ProviderError("no price provider configured")
        return {s: err for s in symbols}
    primary, fallbacks = providers[0], providers[1:]
    try:
        batch = primary.get_daily_history_batch(symbols, start, end)
        registry.record_success(db, primary.name)
    except ProviderError as exc:
        registry.record_failure(db, primary.name, exc)
        batch = {}
    for s in symbols:
        res = batch.get(s)
        if res is not None and not res.frame.empty:
            out[s] = res
            continue
        # fallback providers, in priority order
        last_exc: Exception = SymbolNotFound(f"{primary.name}: no rows for {s}")
        found: HistoryResult | None = None
        for p in fallbacks:
            try:
                r = p.get_daily_history(s, start, end)
                registry.record_success(db, p.name)
                if not r.frame.empty:
                    found = r
                    break
            except ProviderError as exc:
                registry.record_failure(db, p.name, exc)
                last_exc = exc
        out[s] = found or (res if res is not None else last_exc)
    return out


def update_symbols(
    db: Session,
    symbols: list[str],
    *,
    full_refresh: bool = False,
    now: datetime | None = None,
    years: int | None = None,
) -> IngestionReport:
    settings = get_settings()
    now = now or datetime.now(UTC)
    latest = calendar.latest_completed_session(now)
    years = years or settings.initial_history_years
    report = IngestionReport(latest_session=latest)

    stocks: dict[str, Stock] = {}
    for s in symbols:
        try:
            stocks[repo.normalize_symbol(s)] = ensure_stock(db, s)
        except (InvalidSymbol, ValidationUnavailable) as exc:
            report.results.append(SymbolResult(symbol=s, status="FAILED", error=str(exc)))
    db.commit()

    # group by fetch start date so batch downloads share a window
    plan: dict[date, list[str]] = defaultdict(list)
    meta: dict[str, tuple[date | None, bool]] = {}
    for sym, stock in stocks.items():
        last = repo.latest_bar_date(db, stock.id)
        if full_refresh or last is None:
            start = latest - timedelta(days=int(365.25 * years))
            meta[sym] = (last, True)
        elif last >= latest:
            report.results.append(SymbolResult(symbol=sym, status="UP_TO_DATE", latest_date=last))
            continue
        else:
            start = last - timedelta(days=OVERLAP_DAYS)
            meta[sym] = (last, False)
        plan[start].append(sym)

    for start, syms in sorted(plan.items()):
        for i in range(0, len(syms), 25):
            chunk = syms[i : i + 25]
            fetched = _fetch(db, chunk, start, latest)
            for sym in chunk:
                res = fetched[sym]
                last, is_full = meta[sym]
                try:
                    r = _process_symbol(db, stocks[sym], res, last, is_full, latest, years)
                except Exception as exc:  # isolate one symbol's failure
                    db.rollback()
                    log.exception("ingestion failed for %s", sym)
                    r = SymbolResult(symbol=sym, status="FAILED", error=f"{type(exc).__name__}: {exc}")
                report.results.append(r)
                db.commit()
    repo.system_log(db, "INFO", "ingestion", "update_symbols", f"processed {len(symbols)} symbols", report.summary()["counts"])
    db.commit()
    return report


def _process_symbol(
    db: Session,
    stock: Stock,
    res: HistoryResult | Exception,
    last: date | None,
    is_full: bool,
    latest: date,
    years: int,
) -> SymbolResult:
    sym = stock.symbol
    if isinstance(res, Exception):
        repo.log_quality(db, stock.id, [_issue("PROVIDER_FAILURE", "ERROR", str(res)[:500])], None)
        return SymbolResult(symbol=sym, status="FAILED", error=str(res)[:500])

    stored = repo.load_bars(db, "DAILY", stock.id, start=(last - timedelta(days=OVERLAP_DAYS * 2)) if last else None) if last else pd.DataFrame()
    prev_close = None
    if last is not None and not stored.empty:
        before = stored[stored.index < min(res.frame.index)] if not res.frame.empty else stored
        prev_close = float(before["close"].iloc[-1]) if not before.empty else None

    report = validate_ohlcv(
        res.frame,
        asset_type=stock.asset_type,
        latest_session=latest,
        abnormal_threshold=get_settings().abnormal_move_threshold,
        previous_close=prev_close,
    )
    frame = report.frame
    if frame.empty:
        repo.log_quality(db, stock.id, report.issues, res.provider)
        return SymbolResult(symbol=sym, status="NO_DATA", provider=res.provider, issues=[i.message for i in report.issues])

    # Detect re-based adjusted history (new dividend/split) -> full refresh
    if not is_full and not stored.empty:
        provider_changed = bool(set(stored["provider"].dropna()) - {res.provider})
        new_rows = frame[frame.index > last]
        has_action = (new_rows["dividend"].notna().any() or new_rows["split_ratio"].notna().any()) if not new_rows.empty else False
        if res.adjusted_included and (has_action or detect_adjustment_change(stored["adj_close"], frame["adj_close"])):
            log.info("%s: adjusted history changed; refreshing full history", sym)
            provider = registry.get_provider(res.provider)
            full_start = latest - timedelta(days=int(365.25 * years))
            try:
                full_res = provider.get_daily_history(sym, full_start, latest)
                return _process_symbol(db, stock, full_res, None, True, latest, years)
            except ProviderError as exc:
                report.issues.append(_issue("ADJUSTMENT_REFRESH_FAILED", "WARNING", str(exc)[:300]))
        if provider_changed:
            report.issues.append(
                _issue("PROVIDER_MIX", "WARNING", f"new bars from {res.provider}; stored history from {sorted(set(stored['provider'].dropna()))}")
            )
    if not res.adjusted_included:
        report.issues.append(_issue("NO_ADJUSTED_CLOSE", "INFO", f"{res.provider} returned unadjusted prices only"))

    written = repo.upsert_bars(db, "DAILY", stock.id, frame, res.provider, latest, res.retrieved_at)
    repo.log_quality(db, stock.id, report.issues, res.provider)

    first_new = min(frame.index) if is_full else min([d for d in frame.index if d > last] or [max(frame.index)])
    recalc_from = None if is_full else first_new
    rebuild_aggregates_and_indicators(db, stock, latest, recalc_from=recalc_from)
    stock.last_price_update = datetime.now(UTC)
    return SymbolResult(
        symbol=sym,
        status="UPDATED",
        provider=res.provider,
        rows_written=written,
        first_new_date=first_new,
        latest_date=max(frame.index),
        full_refresh=is_full,
        issues=[i.message for i in report.issues],
    )


def rebuild_aggregates_and_indicators(db: Session, stock: Stock, latest: date, recalc_from: date | None = None) -> None:
    """Recompute weekly/monthly bars and indicators. Only rows on/after `recalc_from`
    are rewritten (indicator values for earlier bars cannot change because no
    indicator looks forward)."""
    daily = repo.load_bars(db, "DAILY", stock.id)
    if daily.empty:
        return
    provider = str(daily["provider"].iloc[-1])
    weekly = to_weekly(daily, latest)
    monthly = to_monthly(daily, latest)

    def since(df: pd.DataFrame, d: date | None, pad_days: int) -> pd.DataFrame:
        if d is None:
            return df
        return df[df.index >= d - timedelta(days=pad_days)]

    repo.upsert_bars(db, "WEEKLY", stock.id, since(weekly, recalc_from, 7), provider, latest)
    repo.upsert_bars(db, "MONTHLY", stock.id, since(monthly, recalc_from, 31), provider, latest)

    ind_d = compute_daily(daily)
    ind_w = compute_weekly(weekly)
    ind_m = compute_monthly(monthly)
    repo.upsert_indicators(db, stock.id, "DAILY", [(d, row_to_json(r)) for d, r in since(ind_d, recalc_from, 0).iterrows()])
    repo.upsert_indicators(db, stock.id, "WEEKLY", [(d, row_to_json(r)) for d, r in since(ind_w, recalc_from, 7).iterrows()])
    repo.upsert_indicators(db, stock.id, "MONTHLY", [(d, row_to_json(r)) for d, r in since(ind_m, recalc_from, 31).iterrows()])


def _issue(check: str, sev: str, msg: str):
    from app.pipeline.quality import QualityIssue

    return QualityIssue(check, sev, msg)
