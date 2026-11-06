"""Yahoo Finance provider (via the `yfinance` package).

Yahoo Finance is NOT an official, guaranteed market data API. This provider is
treated as a best-effort source of *historical* end-of-day data. Requests are
retried, cached by the pipeline (incremental updates), and failures are surfaced
to provider health monitoring. Respect Yahoo's terms of service.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from app.core.config import get_settings
from app.providers.base import (
    OHLCV_COLUMNS,
    HistoryResult,
    MarketDataProvider,
    ProviderDataError,
    ProviderUnavailable,
    Quote,
    SymbolMetadata,
    SymbolNotFound,
)

log = logging.getLogger(__name__)

_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividend",
    "Stock Splits": "split_ratio",
}

_EXCHANGE_MAP = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "ASE": "NYSEAMERICAN",
    "AMEX": "NYSEAMERICAN",
    "PCX": "ARCA",
    "ARCX": "ARCA",
    "BTS": "BATS",
    "SNP": "INDEX",
    "DJI": "INDEX",
    "WCB": "INDEX",
    "NIM": "INDEX",
    "CXI": "INDEX",
}


def to_yahoo_symbol(symbol: str) -> str:
    # Class shares: platform uses BRK-B (Yahoo convention). Accept BRK.B as input too.
    return symbol.upper().replace(".", "-")


def normalize_yf_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a yfinance history frame to OHLCV_COLUMNS indexed by session date."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    df = df.rename(columns=_RENAME)
    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York")
    df = df.copy()
    df.index = [ts.date() for ts in idx]
    df = df[OHLCV_COLUMNS]
    # yfinance reports split_ratio 0 when no split; store NULL instead.
    df["split_ratio"] = df["split_ratio"].where(df["split_ratio"].fillna(0) != 0)
    df["dividend"] = df["dividend"].where(df["dividend"].fillna(0) != 0)
    df = df[~pd.Index(df.index).duplicated(keep="last")]
    return df.sort_index()


class YahooFinanceProvider(MarketDataProvider):
    name = "yahoo"
    capabilities = frozenset({"daily_history", "quote", "metadata", "dividends", "splits"})

    def __init__(self, max_retries: int | None = None) -> None:
        self.max_retries = get_settings().provider_max_retries if max_retries is None else max_retries

    def _yf(self):
        import yfinance as yf

        return yf

    def _with_retries(self, fn, what: str):
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except (SymbolNotFound, ProviderDataError):
                raise
            except Exception as exc:  # yfinance raises a variety of exceptions
                msg = str(exc)
                if "No data found" in msg or "delisted" in msg.lower():
                    raise SymbolNotFound(f"yahoo: {what}: {msg[:200]}") from exc
                if attempt > self.max_retries:
                    raise ProviderUnavailable(f"yahoo: {what} failed: {type(exc).__name__}: {msg[:200]}") from exc
                time.sleep(min(2 ** (attempt - 1), 8))

    def get_daily_history(self, symbol: str, start: date, end: date) -> HistoryResult:
        return self.get_daily_history_batch([symbol], start, end)[symbol]

    def get_daily_history_batch(self, symbols: list[str], start: date, end: date) -> dict[str, HistoryResult]:
        yf = self._yf()
        ysyms = [to_yahoo_symbol(s) for s in symbols]
        retrieved = datetime.now(UTC)

        def _download():
            return yf.download(
                tickers=ysyms,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
                interval="1d",
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                progress=False,
                threads=True,
                multi_level_index=True,
            )

        raw = self._with_retries(_download, f"download {','.join(ysyms[:5])}")
        if raw is None or raw.dropna(how="all").empty:
            # yfinance reports network failures as "no data / possibly delisted";
            # distinguish an outage from genuinely unknown symbols with a probe.
            if not self.reachable():
                raise ProviderUnavailable("yahoo: no data returned and probe symbol failed (provider unreachable)")
        out: dict[str, HistoryResult] = {}
        for sym, ysym in zip(symbols, ysyms, strict=True):
            try:
                sub = raw[ysym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                sub = pd.DataFrame()
            sub = sub.dropna(how="all")
            frame = normalize_yf_frame(sub)
            if not frame.empty and pd.isna(frame["close"].iloc[-1]):
                frame = self._repair_tail(ysym, frame, end)
            warnings: list[str] = []
            if frame.empty:
                warnings.append("no rows returned")
            out[sym] = HistoryResult(sym, self.name, frame, adjusted_included=True, retrieved_at=retrieved, warnings=warnings)
        return out

    def _repair_tail(self, ysym: str, frame: pd.DataFrame, end: date) -> pd.DataFrame:
        """Multi-ticker downloads sometimes return the newest bar with an empty close.
        Re-fetch the last days for that ticker alone and fill only the missing values."""
        try:
            hist = self._yf().Ticker(ysym).history(start=(end - timedelta(days=10)).isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d", auto_adjust=False, actions=True)
        except Exception as exc:  # noqa: BLE001 - validation will drop the incomplete bar
            log.warning("yahoo: tail repair failed for %s: %s", ysym, exc)
            return frame
        fix = normalize_yf_frame(hist)
        if fix.empty:
            return frame
        return frame.fillna(fix.reindex(index=frame.index, columns=frame.columns))

    PROBE_SYMBOL = "SPY"

    def reachable(self) -> bool:
        """Cheap health probe (cached 5 minutes)."""
        from app.core.cache import cache_get_json, cache_set_json

        cached = cache_get_json("yahoo:probe")
        if cached is not None:
            return bool(cached)
        try:
            t = self._yf().Ticker(self.PROBE_SYMBOL)
            ok = not t.history(period="5d", interval="1d", auto_adjust=False).empty
        except Exception:
            ok = False
        cache_set_json("yahoo:probe", ok, 300)
        return ok

    def health_check(self) -> bool:
        return self.reachable()

    def get_quote(self, symbol: str) -> Quote:
        """Latest completed daily bar. Yahoo quotes are not guaranteed real time, so
        this provider reports END_OF_DAY data only."""
        yf = self._yf()
        t = yf.Ticker(to_yahoo_symbol(symbol))
        hist = self._with_retries(lambda: t.history(period="10d", interval="1d", auto_adjust=False), f"quote {symbol}")
        frame = normalize_yf_frame(hist)
        from app.market.calendar import latest_completed_session

        last_session = latest_completed_session()
        frame = frame[[d <= last_session for d in frame.index]]
        if frame.empty:
            raise SymbolNotFound(f"yahoo: no quote for {symbol}")
        last = frame.iloc[-1]
        prev = frame.iloc[-2]["close"] if len(frame) > 1 else None
        price = float(last["close"])
        change = price - float(prev) if prev is not None else None
        return Quote(
            symbol=symbol,
            provider=self.name,
            price=price,
            change=change,
            change_pct=(change / float(prev) * 100) if change is not None and prev else None,
            previous_close=float(prev) if prev is not None else None,
            as_of=datetime.now(UTC),
            trading_day=frame.index[-1],
            data_status="END_OF_DAY",
            volume=int(last["volume"]) if pd.notna(last["volume"]) else None,
        )

    def get_metadata(self, symbol: str) -> SymbolMetadata:
        yf = self._yf()
        t = yf.Ticker(to_yahoo_symbol(symbol))
        info = self._with_retries(lambda: t.info, f"info {symbol}") or {}
        if not info or (info.get("quoteType") is None and info.get("shortName") is None):
            raise SymbolNotFound(f"yahoo: no metadata for {symbol}")
        qt = (info.get("quoteType") or "").upper()
        asset_type = {"EQUITY": "EQUITY", "ETF": "ETF", "INDEX": "INDEX", "MUTUALFUND": "FUND"}.get(qt, qt or None)
        return SymbolMetadata(
            symbol=symbol,
            provider=self.name,
            name=info.get("longName") or info.get("shortName"),
            exchange=_EXCHANGE_MAP.get(info.get("exchange", ""), info.get("exchange")),
            asset_type=asset_type,
            currency=info.get("currency"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            shares_outstanding=info.get("sharesOutstanding"),
            extra={k: info.get(k) for k in ("forwardPE", "trailingPE", "marketCap", "enterpriseToEbitda") if info.get(k)},
        )
