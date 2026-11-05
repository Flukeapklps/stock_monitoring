"""Flex Message templates (dark investment-terminal style).

Layout of the stock alert card:
    📈 AAPL — ACCUMULATION WATCH        (title, signal colour)
    Apple Inc.
    $XXX.XX                             (large, green/red by daily change)
    +1.23% · Close 2026-11-04 · END_OF_DAY
    ─────────────
    Investment Score        85/100
    Long-term Trend        [BULLISH]
    RSI (14)                 48.5
    MACD                  Bullish
    SMA 50 / SMA 200 ...
    ─────────────
    Analysis
    • reason ...
    Data: END_OF_DAY · Yahoo Finance · generated time
    [ View Stock Details ↗ ]   (green)
"""

from __future__ import annotations

from typing import Any

BG = "#0B0F14"
CARD = "#111827"
TEXT = "#F9FAFB"
MUTED = "#9CA3AF"
SUBTLE = "#6B7280"
SEP = "#1F2937"
GREEN = "#22C55E"
GREEN_BG = "#052E16"
RED = "#EF4444"
RED_BG = "#450A0A"
YELLOW = "#EAB308"
YELLOW_BG = "#422006"
GRAY_BG = "#1F2937"

SIGNAL_STYLE = {
    "STRONG_LONG_TERM_TREND": ("🚀", GREEN, GREEN_BG, "STRONG LONG-TERM TREND"),
    "BULLISH": ("📈", GREEN, GREEN_BG, "BULLISH"),
    "ACCUMULATION_WATCH": ("📈", GREEN, GREEN_BG, "ACCUMULATION WATCH"),
    "NEUTRAL": ("⏸️", MUTED, GRAY_BG, "NEUTRAL"),
    "OVERBOUGHT": ("⚠️", YELLOW, YELLOW_BG, "OVERBOUGHT"),
    "WEAKENING": ("⚠️", YELLOW, YELLOW_BG, "WEAKENING"),
    "TREND_WEAKENING": ("📉", RED, RED_BG, "TREND WEAKENING"),
    "BEARISH": ("📉", RED, RED_BG, "BEARISH"),
    "PRICE_ALERT": ("🔔", YELLOW, YELLOW_BG, "PRICE ALERT"),
    "FUNDAMENTAL": ("📄", YELLOW, YELLOW_BG, "FUNDAMENTAL ALERT"),
    "PORTFOLIO": ("💼", YELLOW, YELLOW_BG, "PORTFOLIO ALERT"),
}
TREND_COLORS = {"BULLISH": (GREEN, GREEN_BG), "BEARISH": (RED, RED_BG), "NEUTRAL": (MUTED, GRAY_BG)}


def style_for(signal: str) -> tuple[str, str, str, str]:
    return SIGNAL_STYLE.get(signal, ("📊", MUTED, GRAY_BG, signal.replace("_", " ")))


def money(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def pct(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def num(v: float | None, nd: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"


def text(t: str, **kw) -> dict:
    return {"type": "text", "text": t if t else " ", **kw}


def separator(margin: str = "lg") -> dict:
    return {"type": "separator", "margin": margin, "color": SEP}


def row(label: str, value: str, value_color: str = TEXT, bold: bool = True) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "contents": [
            text(label, size="sm", color=MUTED, flex=5, gravity="center"),
            text(value, size="sm", color=value_color, align="end", weight="bold" if bold else "regular", flex=5, wrap=True, gravity="center"),
        ],
    }


def badge_row(label: str, badge: str, color: str, bg: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "contents": [
            text(label, size="sm", color=MUTED, flex=5, gravity="center"),
            {
                "type": "box",
                "layout": "horizontal",
                "flex": 5,
                "contents": [
                    {"type": "filler"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": bg,
                        "cornerRadius": "12px",
                        "paddingStart": "10px",
                        "paddingEnd": "10px",
                        "paddingTop": "3px",
                        "paddingBottom": "3px",
                        "flex": 0,
                        "contents": [text(badge, size="xs", color=color, weight="bold", align="center")],
                    },
                ],
            },
        ],
    }


def bullet_list(items: list[str], color: str = TEXT, limit: int = 6) -> list[dict]:
    out = []
    for it in items[:limit]:
        out.append(
            {
                "type": "box",
                "layout": "baseline",
                "margin": "sm",
                "spacing": "sm",
                "contents": [text("•", size="sm", color=MUTED, flex=0), text(it[:200], size="sm", color=color, wrap=True, flex=1)],
            }
        )
    return out


def header_block(icon: str, symbol: str, signal_label: str, color: str, company: str | None) -> list[dict]:
    blocks = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                text("Stock Monitor", size="xs", color=MUTED, flex=1),
                text("LONG-TERM ALERT", size="xxs", color=SUBTLE, align="end", flex=1, gravity="center"),
            ],
        },
        text(f"{icon} {symbol} — {signal_label}", size="lg", weight="bold", color=TEXT, wrap=True, margin="md"),
    ]
    if company:
        blocks.append(text(company, size="xs", color=MUTED, wrap=True))
    return blocks


def buttons(links: list[tuple[str, str]], primary_color: str = GREEN) -> dict | None:
    """links: [(label, url)]; only HTTPS links are rendered."""
    items = []
    for i, (label, url) in enumerate(links):
        if not url or not url.startswith("https://"):
            continue
        items.append(
            {
                "type": "button",
                "style": "primary" if i == 0 else "link",
                "color": primary_color if i == 0 else MUTED,
                "height": "sm",
                "action": {"type": "uri", "label": label[:40], "uri": url},
            }
        )
    if not items:
        return None
    return {"type": "box", "layout": "vertical", "spacing": "sm", "contents": items, "backgroundColor": BG}


def stock_alert_bubble(d: dict[str, Any]) -> dict:
    """d keys: symbol, company, signal, price, change_pct, trade_date, score, trend, rsi, macd_state,
    sma50, sma200, return_1y, reasons, data_status, provider_label, generated_at, timeframe, links"""
    icon, color, _bg, label = style_for(d["signal"])
    price_color = GREEN if (d.get("change_pct") or 0) >= 0 else RED
    trend = d.get("trend") or "NEUTRAL"
    tcolor, tbg = TREND_COLORS.get(trend, (MUTED, GRAY_BG))
    price = d.get("price")
    sma50, sma200 = d.get("sma50"), d.get("sma200")
    sma200_txt = money(sma200)
    if price is not None and sma200 is not None:
        sma200_txt += " · above" if price > sma200 else " · below"
    sma50_txt = money(sma50)
    if price is not None and sma50 is not None:
        sma50_txt += f" ({(price / sma50 - 1) * 100:+.1f}%)"
    macd_state = d.get("macd_state") or "n/a"
    macd_color = GREEN if macd_state.lower().startswith("bull") else (RED if macd_state.lower().startswith("bear") else TEXT)

    body = header_block(icon, d["symbol"], label, color, d.get("company"))
    body += [
        text(money(price), size="3xl", weight="bold", color=price_color, margin="md"),
        text(
            f"{pct(d.get('change_pct'))} today · Close {d.get('trade_date') or 'n/a'} · {d.get('data_status') or 'n/a'}",
            size="xs",
            color=MUTED,
            wrap=True,
        ),
        separator(),
        row("Investment Score", "n/a" if d.get("score") is None else f"{d['score']:.0f}/100"),
        badge_row("Long-term Trend", trend, tcolor, tbg),
        row("RSI (14)", num(d.get("rsi"))),
        row("MACD", macd_state, macd_color),
        row("SMA 50", sma50_txt),
        row("SMA 200", sma200_txt),
        row("1Y Return", pct(d.get("return_1y")), GREEN if (d.get("return_1y") or 0) >= 0 else RED),
        separator(),
        text("Analysis", size="md", weight="bold", color=TEXT, margin="lg"),
        *bullet_list(d.get("reasons") or ["No qualifying conditions recorded."]),
        text(
            f"Data: {d.get('data_status') or 'n/a'} · {d.get('provider_label') or 'n/a'} · {d.get('timeframe') or 'DAILY'}",
            size="xxs",
            color=SUBTLE,
            margin="lg",
            wrap=True,
        ),
        text(f"Generated: {d.get('generated_at') or ''}", size="xxs", color=SUBTLE, wrap=True),
        text("Automated technical analysis, not a guaranteed investment recommendation.", size="xxs", color=SUBTLE, wrap=True, margin="sm"),
    ]
    bubble: dict = {
        "type": "bubble",
        "size": "mega",
        "body": {"type": "box", "layout": "vertical", "backgroundColor": BG, "paddingAll": "20px", "contents": body},
        "styles": {"body": {"backgroundColor": BG}, "footer": {"backgroundColor": BG}},
    }
    f = buttons(d.get("links") or [])
    if f:
        bubble["footer"] = f
    return bubble


def alt_text_for_alert(d: dict) -> str:
    _icon, _c, _b, label = style_for(d["signal"])
    score = "n/a" if d.get("score") is None else "{:.0f}/100".format(d["score"])
    return f"{d['symbol']} {label}: {money(d.get('price'))} ({pct(d.get('change_pct'))}), score {score}"[:400]


def flex(alt: str, contents: dict) -> dict:
    return {"type": "flex", "altText": alt[:1500], "contents": contents}


def simple_card(title: str, lines: list[str], *, subtitle: str | None = None, color: str = TEXT, footer_note: str | None = None, links: list[tuple[str, str]] | None = None) -> dict:
    body = [text(title, size="lg", weight="bold", color=color, wrap=True)]
    if subtitle:
        body.append(text(subtitle, size="xs", color=MUTED, wrap=True))
    body.append(separator("md"))
    body += bullet_list(lines, limit=12)
    if footer_note:
        body.append(text(footer_note, size="xxs", color=SUBTLE, wrap=True, margin="lg"))
    bubble: dict = {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "backgroundColor": BG, "paddingAll": "20px", "contents": body}}
    f = buttons(links or [])
    if f:
        bubble["footer"] = f
        bubble["styles"] = {"footer": {"backgroundColor": BG}}
    return bubble


def table_card(title: str, subtitle: str | None, rows: list[tuple[str, str, str]], *, sections: list[tuple[str, list[str]]] | None = None, footer_note: str | None = None, links: list[tuple[str, str]] | None = None) -> dict:
    """rows: (label, value, color)."""
    body = [text(title, size="lg", weight="bold", color=TEXT, wrap=True)]
    if subtitle:
        body.append(text(subtitle, size="xs", color=MUTED, wrap=True))
    body.append(separator("md"))
    for label, value, color in rows[:20]:
        body.append(row(label, value, color))
    for heading, items in sections or []:
        if not items:
            continue
        body.append(separator())
        body.append(text(heading, size="sm", weight="bold", color=TEXT, margin="lg"))
        body += bullet_list(items, limit=8)
    if footer_note:
        body.append(text(footer_note, size="xxs", color=SUBTLE, wrap=True, margin="lg"))
    bubble: dict = {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "backgroundColor": BG, "paddingAll": "20px", "contents": body}}
    f = buttons(links or [])
    if f:
        bubble["footer"] = f
        bubble["styles"] = {"footer": {"backgroundColor": BG}}
    return bubble


def change_color(v: float | None) -> str:
    if v is None:
        return MUTED
    return GREEN if v >= 0 else RED
