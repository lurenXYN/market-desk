"""Dress rehearsal: run real refresh rounds on a faked trading day before the market opens.

After a long holiday, features shipped during the break have never seen a live
session. This script copies ``data/desk.db`` to a temp dir, freezes the clock at
a series of times on the target day, and runs ``engine.refresh()`` once per time
point against the live data sources (which return the last session's data).

It checks that every round finishes without swallowed exceptions (the refresh
path logs and continues, so failures are otherwise silent), that the auction
alpha locks after 09:26, that the cross-tab alert feed is built, and that buy
signals land in the copied db with the payload fields recent versions rely on.

Nothing is pushed (Server酱 / Windows toasts are stubbed) and the real db is
never written. Market numbers are stale, so the signals themselves are
meaningless; only the code paths are being exercised.

Usage::

    python scripts/rehearsal.py                     # next trading day, full schedule
    python scripts/rehearsal.py --quick             # fewer rounds
    python scripts/rehearsal.py --day 2026-10-08 --source-day 2026-09-30 --keep

Requires ``pip install time-machine`` (dev only, not in requirements.txt).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from zoneinfo import ZoneInfo

    CN = ZoneInfo("Asia/Shanghai")
except Exception:
    CN = timezone(timedelta(hours=8))
FULL_SCHEDULE = (
    "09:20:10", "09:21:20", "09:22:30", "09:23:40", "09:24:50", "09:26:05",
    "09:31:00", "09:46:00", "10:30:00", "11:20:00", "13:30:00", "14:30:00", "14:58:00",
)
QUICK_SCHEDULE = ("09:21:00", "09:23:00", "09:24:30", "09:26:05", "09:46:00", "10:30:00", "14:30:00")

# Payload keys checked on buy signals: (key, applies-to description).
SIGNAL_KEYS = (
    ("book_open", "全部买卡（v1.9.4 首次出现时冻结的当日亮卡账本）"),
    ("cf", "全部买卡（v1.8.6 反事实逐次记录）"),
    ("stop_atr", "有日线的买卡（v1.8.8 ATR 影子止损）"),
    ("slip_bps", "到价/亮灯且有建议股数的卡（v1.8.6 冲击成本）"),
    ("first_ready_px", "亮过灯的卡（v1.9.4）"),
    ("book_ready", "亮过灯的卡（v1.9.4）"),
)


class _ErrorTap(logging.Handler):
    """Collect ERROR-level records emitted while a round runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", help="Faked trading day (YYYY-MM-DD); default: next trading day")
    ap.add_argument("--source-day", help="Last real session for date-keyed pools; default: previous trading day")
    ap.add_argument("--quick", action="store_true", help="Run a shorter schedule")
    ap.add_argument("--keep", action="store_true", help="Keep the temp db and print its path")
    return ap.parse_args()


def _copy_db(dst_dir: Path) -> Path:
    """Copy the live db with the SQLite backup API (WAL-safe) and return the copy path."""
    src_path = ROOT / "data" / "desk.db"
    if not src_path.exists():
        raise SystemExit(f"找不到 {src_path}")
    dst_path = dst_dir / "desk.db"
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    src.backup(dst)
    src.close()
    dst.close()
    return dst_path


def _pick_days(day_arg: str | None, src_arg: str | None) -> tuple[date, date]:
    """Resolve the faked day and the last real session day from the trading calendar."""
    from market_desk.calendar import is_trading_day

    today = datetime.now(CN).date()
    day = date.fromisoformat(day_arg) if day_arg else None
    if day is None:
        day = today + timedelta(days=1)
        while not is_trading_day(day):
            day += timedelta(days=1)
    src = date.fromisoformat(src_arg) if src_arg else None
    if src is None:
        src = min(today, day - timedelta(days=1))
        while not is_trading_day(src):
            src -= timedelta(days=1)
    if not is_trading_day(day):
        raise SystemExit(f"{day} 不是交易日（calendar.py）")
    return day, src


def _boxes(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every verdict box that carries a card list."""
    out = []
    for val in (verdict or {}).values():
        if isinstance(val, dict) and isinstance(val.get("items"), list):
            out.append(val)
    return out


def _round_line(snap: dict[str, Any], feed: dict[str, Any]) -> dict[str, Any]:
    """Summarize one snapshot for the per-round table."""
    cards = [it for box in _boxes(snap.get("verdict") or {}) for it in box["items"] if isinstance(it, dict)]
    alpha = ((snap.get("auction") or {}).get("alpha")) or {}
    narr = ((snap.get("radar") or {}).get("narrative")) or {}
    metrics = snap.get("metrics") or {}
    health = snap.get("health") or {}
    src = str((snap.get("boards_source") or {}).get("source") or "-")
    return {
        "boards": len(snap.get("hot_boards") or []),
        "src": {"eastmoney": "东财", "sina": "新浪"}.get(src, src[:4]),
        "health": f"{health.get('score', '-')}{'降' if health.get('degraded') else ''}",
        "failed": list(health.get("failed_sources") or []),
        "zt": metrics.get("zt_count", metrics.get("zt")),
        "narr": f"{len(narr.get('clusters') or [])}/{narr.get('concepts', '-')}" if narr.get("ok") else "-",
        "live": snap.get("live"),
        "cards": len(cards),
        "ready": sum(1 for c in cards if c.get("ready")),
        "near": sum(1 for c in cards if c.get("near_entry")),
        "fail": sum(1 for c in cards if c.get("confirm_fail")),
        "alpha": "锁" if alpha.get("locked") else ("样本" if alpha else "-"),
        "absorb": sum(1 for c in cards if c.get("absorption")),
        "slip": sum(1 for c in cards if c.get("slippage")),
        "atr": sum(1 for c in cards if c.get("stop_atr") is not None),
        "warn": len(snap.get("warnings") or []),
        "feed": f"{len(feed.get('toasts') or [])}/{len(feed.get('stops') or [])}",
        "mainline": ((snap.get("verdict") or {}).get("mainline") or {}).get("name") or "-",
    }


def _db_checks(db_path: Path, day: date) -> tuple[list[tuple[str, bool, str]], dict[str, int]]:
    """Inspect rows written for the faked day in the copied db."""
    dash, compact = day.isoformat(), day.strftime("%Y%m%d")
    con = sqlite3.connect(db_path)
    out: list[tuple[str, bool, str]] = []
    rows = con.execute(
        "SELECT signal_type, payload FROM signals WHERE trade_date IN (?, ?)", (dash, compact)
    ).fetchall()
    buys = []
    for stype, raw in rows:
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            p = {}
        if str(stype).startswith("buy") or p.get("action") == "buy" or "plan_price" in p:
            buys.append(p)
    out.append(("signals 写入", bool(rows), f"{len(rows)} 条（买卡 {len(buys)}）"))
    key_counts = {k: sum(1 for p in buys if k in p) for k, _ in SIGNAL_KEYS}
    for tbl in ("auction_lock", "board_daily", "narrative_shadow", "crowd_shadow", "etf_pulse_log"):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE trade_date IN (?, ?)", (dash, compact)).fetchone()[0]
        except sqlite3.Error as exc:
            out.append((f"{tbl}", False, f"查询失败: {exc}"))
            continue
        out.append((f"{tbl}", n > 0, f"{n} 行"))
    con.close()
    return out, key_counts


async def _run(args: argparse.Namespace) -> int:
    """Run the rehearsal and return the process exit code."""
    try:
        import time_machine
    except ImportError:
        print("需要先安装: .venv\\Scripts\\python.exe -m pip install time-machine")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="desk-rehearsal-"))
    db_path = _copy_db(tmp)

    import market_desk.config as cfg

    cfg.DATA_DIR, cfg.DB_PATH = tmp, db_path
    import market_desk.db as desk_db

    desk_db.DATA_DIR, desk_db.DB_PATH = tmp, db_path
    import market_desk.backup_store as backup_store

    backup_store.DB_PATH, backup_store.BACKUP_DIR = db_path, tmp / "backup"

    day, src_day = _pick_days(args.day, args.source_day)
    desk_db.init_db()

    from market_desk import notify, settings
    import market_desk.engine.alerts as eng_alerts
    import market_desk.engine.refresh as eng_refresh
    from market_desk.engine import engine

    settings.get_settings(refresh=True)
    if isinstance(settings._CACHE, dict):
        settings._CACHE.update(toast_enabled=False, news_radar_enabled=False)
    notify.notify_serverchan = lambda *a, **k: False
    eng_alerts.notify_windows = lambda *a, **k: False
    engine._maybe_capture_pick_scores = lambda now: None
    try:
        engine._load_learned_aliases()
    except Exception:
        logging.getLogger("rehearsal").warning("learned board alias load failed", exc_info=True)

    src_compact = src_day.strftime("%Y%m%d")
    real_zt, real_zb = eng_refresh.fetch_zt_pool, eng_refresh.fetch_zb_pool

    async def _zt(client, _d):
        return await real_zt(client, src_compact)

    async def _zb(client, _d):
        return await real_zb(client, src_compact)

    eng_refresh.fetch_zt_pool, eng_refresh.fetch_zb_pool = _zt, _zb

    tap = _ErrorTap()
    logging.getLogger().addHandler(tap)
    logging.getLogger().setLevel(logging.WARNING)

    schedule = QUICK_SCHEDULE if args.quick else FULL_SCHEDULE
    print(f"演练日 {day}（行情来自 {src_day} 收盘），临时库 {db_path}")
    print(
        f"{'时间':<9}{'秒':>5} {'盘中':<4}{'健康':>5} {'板块源':<6}{'热板':>4}{'涨停':>5}{'卡':>4}{'亮':>4}{'近':>4}"
        f"{'拦':>4} {'竞价':<4}{'承接':>4}{'冲击':>4}{'ATR':>4} {'叙事簇/概念':<10}{'告警':>4} {'提醒/止损':<9} 主线"
    )
    round_errors: list[tuple[str, logging.LogRecord]] = []
    data_gaps: list[str] = []
    alpha_locked = False
    feed_ok = True
    for hhmmss in schedule:
        hh, mm, ss = (int(x) for x in hhmmss.split(":"))
        at = datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=CN)
        before = len(tap.records)
        t0 = time.monotonic()
        with time_machine.travel(at, tick=True):
            try:
                await engine.refresh()
            except Exception as exc:  # refresh() itself should never raise
                logging.getLogger("rehearsal").exception("refresh raised: %s", exc)
            snap = engine.snapshot or {}
            try:
                feed = engine.slice_snapshot("desk").get("alert_feed") or {}
            except Exception:
                logging.getLogger("rehearsal").exception("slice_snapshot failed")
                feed, feed_ok = {}, False
        took = time.monotonic() - t0
        for rec in tap.records[before:]:
            round_errors.append((hhmmss, rec))
        line = _round_line(snap, feed)
        if hhmmss >= "09:26" and line["alpha"] == "锁":
            alpha_locked = True
        print(
            f"{hhmmss:<9}{took:>5.0f} {('是' if line['live'] else '否'):<4}{line['health']:>5} {line['src']:<6}"
            f"{line['boards']:>4}{str(line['zt']):>5}{line['cards']:>4}{line['ready']:>4}{line['near']:>4}"
            f"{line['fail']:>4} {line['alpha']:<4}{line['absorb']:>4}{line['slip']:>4}{line['atr']:>4} "
            f"{line['narr']:<10}{line['warn']:>4} {line['feed']:<9} {line['mainline']}"
        )
        if not line["boards"] or line["failed"]:
            data_gaps.append(f"[{hhmmss}] 热板 {line['boards']} 个，失败源 {line['failed'] or '无'}")
        if snap.get("trade_date") != day.isoformat():
            round_errors.append((hhmmss, logging.makeLogRecord({"msg": f"trade_date={snap.get('trade_date')}"})))

    logging.getLogger().removeHandler(tap)

    print("\n== 吞掉的异常（log.exception）==")
    if not round_errors:
        print("无")
    seen: set[str] = set()
    for hhmmss, rec in round_errors:
        key = f"{rec.name}:{rec.getMessage()}"
        if key in seen:
            continue
        seen.add(key)
        exc = ""
        if rec.exc_info and rec.exc_info[1] is not None:
            exc = f" -> {type(rec.exc_info[1]).__name__}: {rec.exc_info[1]}"
        print(f"[{hhmmss}] {rec.name}: {rec.getMessage()}{exc}")

    print("\n== 数据源缺口（降级不写错误日志，单列）==")
    print("\n".join(data_gaps) if data_gaps else "无")

    checks, key_counts = _db_checks(db_path, day)
    checks.insert(0, ("竞价 alpha 09:26 后锁定", alpha_locked, ""))
    checks.insert(1, ("提醒源 alert_feed 可构建", feed_ok, ""))
    print("\n== 落库检查（临时库）==")
    for name, ok, note in checks:
        print(f"{'OK ' if ok else '-- '} {name} {note}")
    print("\n== 买卡 payload 字段覆盖 ==")
    for key, desc in SIGNAL_KEYS:
        print(f"{key_counts.get(key, 0):>4}  {key:<15} {desc}")

    if args.keep:
        print(f"\n临时库保留在 {db_path}")
    else:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if round_errors or data_gaps else 0


def main() -> None:
    """Entry point."""
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
