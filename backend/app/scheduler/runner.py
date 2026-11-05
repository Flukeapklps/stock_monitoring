"""APScheduler-based background worker (exchange-calendar aware).

All cron times are America/New_York. Each job re-checks the NYSE calendar so
holidays are skipped and weekly/monthly jobs run after the *last session* of the
week/month (e.g. Thursday before Good Friday).
"""

from __future__ import annotations

import logging
import signal
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.market import calendar
from app.services import jobs

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")


def _today_ny():
    return datetime.now(UTC).astimezone(NY).date()


def daily_update_job() -> None:
    today = _today_ny()
    if not calendar.is_trading_day(today):
        log.info("daily update skipped: %s is not a trading day", today)
        return
    log.info("daily update starting for session %s", today)
    log.info("daily update result: %s", jobs.run_daily_update())


def fundamentals_job() -> None:
    if calendar.is_trading_day(_today_ny()):
        jobs.run_fundamentals_update()


def daily_report_job() -> None:
    if calendar.is_trading_day(_today_ny()):
        jobs.run_report("daily", _today_ny())


def weekly_report_job() -> None:
    today = _today_ny()
    if calendar.is_last_session_of_week(today):
        jobs.run_report("weekly", today)


def monthly_report_job() -> None:
    today = _today_ny()
    if calendar.is_last_session_of_month(today):
        jobs.run_report("monthly", today)


def dispatch_job() -> None:
    try:
        jobs.run_dispatch()
    except Exception:
        log.exception("notification dispatch failed")


def _cron(hhmm: str) -> CronTrigger:
    h, m = hhmm.split(":")
    return CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m), timezone=NY)


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=NY, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    sched.add_job(daily_update_job, _cron(s.daily_job_time), id="daily_update", name="Daily price update + analysis + alerts")
    sched.add_job(daily_report_job, _cron(s.line_daily_report_time), id="daily_report", name="LINE daily report")
    sched.add_job(weekly_report_job, _cron(s.line_weekly_report_time), id="weekly_report", name="LINE weekly report (last session of week)")
    sched.add_job(monthly_report_job, _cron(s.monthly_job_time), id="monthly_report", name="LINE monthly report (last session of month)")
    sched.add_job(fundamentals_job, _cron(s.sec_filings_job_time), id="fundamentals", name="SEC fundamentals & filings")
    sched.add_job(dispatch_job, IntervalTrigger(seconds=s.notification_dispatch_seconds), id="dispatch", name="LINE notification queue")
    return sched


def main() -> None:
    from app.core.logging import configure_logging
    from app.main import bootstrap

    s = get_settings()
    configure_logging(s.log_level, s.log_dir)
    bootstrap()
    if not s.scheduler_enabled:
        log.warning("SCHEDULER_ENABLED=false; worker idle")
        signal.pause()
        return
    sched = build_scheduler()
    for j in sched.get_jobs():
        log.info("scheduled job %-14s %s", j.id, j.trigger)
    sched.start()


if __name__ == "__main__":
    main()
