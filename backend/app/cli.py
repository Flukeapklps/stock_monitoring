"""Operational CLI.

    python -m app.cli bootstrap                 # admin user, exchanges
    python -m app.cli load AAPL MSFT --years 10 # initial/incremental load
    python -m app.cli daily                     # run the daily pipeline now
    python -m app.cli fundamentals              # SEC fundamentals + filings
    python -m app.cli report daily|weekly|monthly
    python -m app.cli dispatch                  # send queued LINE notifications
"""

from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    lp = sub.add_parser("load")
    lp.add_argument("symbols", nargs="*")
    lp.add_argument("--years", type=int, default=None)
    lp.add_argument("--full", action="store_true")
    lp.add_argument("--no-analyze", action="store_true")
    sub.add_parser("daily")
    sub.add_parser("fundamentals")
    rp = sub.add_parser("report")
    rp.add_argument("kind", choices=["daily", "weekly", "monthly"])
    sub.add_parser("dispatch")
    args = p.parse_args()
    configure_logging(get_settings().log_level)

    from app.main import bootstrap

    bootstrap()
    from app.services import jobs

    if args.cmd == "bootstrap":
        print("ok")
    elif args.cmd == "load":
        from sqlalchemy import select

        from app.analysis.service import analyze_stocks, default_benchmark
        from app.db.session import session_scope
        from app.models import Stock
        from app.pipeline.ingestion import update_symbols

        with session_scope() as db:
            syms = args.symbols or jobs.tracked_universe(db)
            rep = update_symbols(db, syms, full_refresh=args.full, years=args.years)
            print(json.dumps(rep.summary(), default=str, indent=1))
            if not args.no_analyze:
                stocks = list(db.scalars(select(Stock).where(Stock.symbol.in_([s.upper().replace(".", "-") for s in syms]))))
                res = analyze_stocks(db, stocks, benchmark=default_benchmark())
                print(json.dumps({r.symbol: [r.status, r.primary_signal, None if not r.score else round(r.score.total or 0, 1)] for r in res}, indent=1))
    elif args.cmd == "daily":
        print(json.dumps(jobs.run_daily_update(), default=str, indent=1))
    elif args.cmd == "fundamentals":
        print(json.dumps(jobs.run_fundamentals_update(), default=str, indent=1))
    elif args.cmd == "report":
        print(jobs.run_report(args.kind))
    elif args.cmd == "dispatch":
        print(jobs.run_dispatch())


if __name__ == "__main__":
    main()
