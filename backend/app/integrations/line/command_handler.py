"""Text command parsing and execution for the LINE bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.line import message_builder as mb
from app.integrations.line.notification_service import COMMAND_ALIASES, get_preferences, set_preference
from app.models import LineUser, Portfolio, Stock, User, Watchlist, WatchlistItem
from app.pipeline import repository as repo
from app.pipeline.ingestion import InvalidSymbol, ValidationUnavailable, ensure_stock, validate_symbol_format
from app.services import jobs

log = logging.getLogger(__name__)

COMMANDS = {"help", "market", "watchlist", "stock", "signal", "score", "watch", "unwatch", "portfolio", "top", "settings", "test", "notify", "link", "start"}
NEEDS_SYMBOL = {"stock", "signal", "score", "watch", "unwatch"}


@dataclass
class ParsedCommand:
    name: str
    args: list[str]
    raw: str


def parse_command(text: str) -> ParsedCommand | None:
    """Case-insensitive; accepts '/stock aapl', 'STOCK AAPL', '／stock AAPL'."""
    if not text:
        return None
    t = text.strip().replace("／", "/")
    if not t:
        return None
    parts = t.split()
    head = parts[0].lstrip("/").lower()
    if head not in COMMANDS:
        return None
    return ParsedCommand(head, parts[1:], t)


def _symbol_arg(cmd: ParsedCommand) -> str:
    if not cmd.args:
        raise InvalidSymbol(f"Usage: /{cmd.name} SYMBOL (e.g. /{cmd.name} AAPL)")
    return validate_symbol_format(cmd.args[0])


def _known_stock(db: Session, symbol: str) -> Stock | None:
    return repo.get_stock(db, symbol)


def handle(db: Session, lu: LineUser, text: str) -> list[dict]:
    cmd = parse_command(text)
    if cmd is None:
        return [mb.text_message("Unknown command. Type /help to see what I can do.")]
    try:
        return _dispatch(db, lu, cmd)
    except InvalidSymbol as exc:
        return [mb.text_message(f"⚠️ {exc}")]
    except ValidationUnavailable:
        return [mb.text_message("⚠️ Market data providers are unavailable right now; please try again later.")]


def _dispatch(db: Session, lu: LineUser, cmd: ParsedCommand) -> list[dict]:
    _, general = get_preferences(db, lu)
    tz = general.get("timezone") or "America/New_York"
    name = cmd.name
    if name in ("help", "start"):
        return [mb.text_message(mb.HELP_TEXT)]
    if name == "market":
        return mb.market_message(db)
    if name == "top":
        return mb.top_message(db)
    if name == "watchlist":
        return mb.watchlist_message(db, _watchlist_stocks(db, lu))
    if name == "portfolio":
        ps = list(db.scalars(select(Portfolio).where(Portfolio.user_id == lu.user_id))) if lu.user_id else []
        return mb.portfolio_message(db, ps)
    if name == "settings":
        prefs, general = get_preferences(db, lu)
        return mb.settings_message(prefs, general)
    if name == "notify":
        if len(cmd.args) != 2 or cmd.args[0].lower() not in COMMAND_ALIASES or cmd.args[1].lower() not in ("on", "off"):
            return [mb.text_message("Usage: /notify <type> on|off\nTypes: " + ", ".join(COMMAND_ALIASES))]
        set_preference(db, lu, COMMAND_ALIASES[cmd.args[0].lower()], enabled=cmd.args[1].lower() == "on")
        return [mb.text_message(f"✅ {cmd.args[0].lower()} notifications turned {cmd.args[1].lower()}.")]
    if name == "test":
        from app.integrations.line import flex_templates as ft

        card = ft.simple_card("✅ Test notification", ["LINE integration is working.", "You will receive alerts for symbols on your watchlist and portfolio."], color=ft.GREEN, footer_note="Stock Monitor test message")
        return mb.validated([ft.flex("Stock Monitor test notification", card)])
    if name == "link":
        return _link(db, lu, cmd)

    symbol = _symbol_arg(cmd)
    if name == "watch":
        stock = _known_stock(db, symbol)
        is_new = stock is None
        if stock is None:
            stock = ensure_stock(db, symbol)  # validates with providers
        wl = jobs.default_watchlist(db, lu.user_id)
        added = jobs.add_to_watchlist(db, wl, stock)
        db.commit()
        if is_new or repo.latest_bar_date(db, stock.id) is None:
            jobs.submit_symbol_load(stock.symbol)
            return [mb.text_message(f"✅ Added {stock.symbol}{' (' + stock.name + ')' if stock.name else ''} to your watchlist. Historical data is loading — try /stock {stock.symbol} in a few minutes.")]
        return [mb.text_message(f"✅ {stock.symbol} {'added to' if added else 'is already on'} your watchlist.")]
    if name == "unwatch":
        stock = _known_stock(db, symbol)
        if not stock or not lu.user_id:
            return [mb.text_message(f"{symbol} is not on your watchlist.")]
        items = db.scalars(select(WatchlistItem).join(Watchlist).where(Watchlist.user_id == lu.user_id, WatchlistItem.stock_id == stock.id)).all()
        for it in items:
            db.delete(it)
        db.commit()
        return [mb.text_message(f"🗑 Removed {symbol} from your watchlist." if items else f"{symbol} is not on your watchlist.")]

    stock = _known_stock(db, symbol)
    if stock is None:
        return [mb.text_message(f"{symbol} is not tracked yet. Add it with /watch {symbol}")]
    if name == "stock":
        return mb.stock_card(db, stock, tz=tz)
    if name == "signal":
        return mb.signal_message(db, stock)
    if name == "score":
        return mb.score_message(db, stock)
    return [mb.text_message("Unknown command. Type /help")]


def _watchlist_stocks(db: Session, lu: LineUser) -> list[Stock]:
    if not lu.user_id:
        return []
    q = select(Stock).join(WatchlistItem, WatchlistItem.stock_id == Stock.id).join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id).where(Watchlist.user_id == lu.user_id).distinct()
    return list(db.scalars(q))


def _link(db: Session, lu: LineUser, cmd: ParsedCommand) -> list[dict]:
    if not cmd.args:
        return [mb.text_message("Usage: /link CODE — generate the code under Settings → LINE on the web dashboard.")]
    code = cmd.args[0].strip().upper()
    user = db.scalar(select(User).where(User.line_link_code == code, User.role != "line"))
    if not user:
        return [mb.text_message("⚠️ Invalid or expired link code.")]
    old_user_id = lu.user_id
    target = jobs.default_watchlist(db, user.id)
    if old_user_id and old_user_id != user.id:
        old = db.get(User, old_user_id)
        if old and old.role == "line":
            for item in db.scalars(select(WatchlistItem).join(Watchlist).where(Watchlist.user_id == old_user_id)).all():
                jobs.add_to_watchlist(db, target, item.stock)
    lu.user_id = user.id
    user.line_link_code = None
    db.commit()
    return [mb.text_message(f"🔗 Linked to web account {user.email}. Your watchlists and portfolios are now shared.")]
