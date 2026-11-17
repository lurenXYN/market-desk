"""Pull the daily VPS ``desk.db`` snapshot to this machine and keep 30 days.

The VPS is only reachable through GitHub Actions (deploy secrets live there), so
``.github/workflows/vps-db-snapshot.yml`` makes a consistent copy with sqlite's
backup API, gzip-compresses it and uploads it as the ``vps-db`` artifact. This
script downloads the newest one, verifies it, and prunes files older than 30
days under ``data/vps-db``.

Auth: ``gh`` CLI (``gh auth token``) or ``GITHUB_TOKEN``/``GH_TOKEN``.
Network: proxy environment variables are deliberately ignored — they made
``api.github.com`` unreachable (HTTP 000) on this box.

Usage::

    python scripts/pull_vps_db.py                 # newest snapshot + prune
    python scripts/pull_vps_db.py --keep-days 45  # custom retention
    python scripts/pull_vps_db.py --dry-run       # show what would happen
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

REPO = os.environ.get("VPS_DB_REPO", "lurenXYN/market-desk")
WORKFLOW = os.environ.get("VPS_DB_WORKFLOW", "vps-db-snapshot.yml")
ARTIFACT = "vps-db"
API = "https://api.github.com"
DEST_DIR = Path("data/vps-db")
DEFAULT_KEEP_DAYS = 30


class _NoAuthOnStorageRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the GitHub Bearer header once the download redirects to storage.

    artifact 下载会 302 到 Azure Blob；把 GitHub token 带过去会被拒 401
    ("Server failed to authenticate the request")。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and "api.github.com" not in urllib.parse.urlsplit(newurl).netloc:
            for key in list(new.headers):
                if key.lower() == "authorization":
                    new.remove_header(key)
        return new


def _opener() -> urllib.request.OpenerDirector:
    """Opener that ignores HTTP(S)_PROXY (they break api.github.com here)."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoAuthOnStorageRedirect()
    )


def token() -> str:
    env = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=20, check=False
        )
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _api(url: str, *, tok: str, raw: bool = False) -> Any:
    if not tok:
        raise SystemExit("缺少 GitHub 凭据：先 `gh auth login`，或设置 GITHUB_TOKEN")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "market-desk-pull-vps-db",
        },
    )
    with _opener().open(req, timeout=120) as resp:
        data = resp.read()
    return data if raw else json.loads(data.decode("utf-8"))


def newest_artifact(tok: str) -> dict[str, Any] | None:
    """Newest non-expired ``vps-db`` artifact across recent runs."""
    runs = _api(f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=10", tok=tok)
    for run in runs.get("workflow_runs") or []:
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        arts = _api(f"{API}/repos/{REPO}/actions/runs/{run['id']}/artifacts", tok=tok)
        for art in arts.get("artifacts") or []:
            if art.get("name") == ARTIFACT and not art.get("expired"):
                return {
                    "run_id": run.get("id"),
                    "created_at": run.get("created_at"),
                    "artifact_id": art.get("id"),
                    "size_in_bytes": art.get("size_in_bytes"),
                    "url": art.get("archive_download_url"),
                }
    return None


def snapshot_day(name: str) -> date | None:
    """``desk-2026-11-17.db.gz`` → date(2026, 11, 17)."""
    stem = Path(name).name
    for suffix in (".db.gz", ".db"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if not stem.startswith("desk-"):
        return None
    try:
        return date.fromisoformat(stem[len("desk-"):])
    except ValueError:
        return None


def prune_old(files: Iterable[Path], *, keep_days: int = DEFAULT_KEEP_DAYS, today: date | None = None) -> list[Path]:
    """Delete snapshots older than ``keep_days``; returns the removed paths."""
    cutoff = (today or date.today()) - timedelta(days=max(1, int(keep_days)))
    removed: list[Path] = []
    for path in files:
        day = snapshot_day(path.name)
        if day is None or day >= cutoff:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def verify_db(path: Path) -> dict[str, Any]:
    """Integrity + shape check so a truncated artifact never looks like data."""
    import sqlite3

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        counts: dict[str, int | None] = {}
        for table in ("signals", "market_bar_daily", "decision_trace"):
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = None
    finally:
        conn.close()
    return {"ok": integrity == "ok", "integrity": integrity, "tables": tables, "row_counts": counts}


def _gh_download(run_id: int, out_dir: Path, artifact: str = ARTIFACT) -> bool:
    """Use the gh CLI when present: it handles artifact auth/redirects itself."""
    try:
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY")}
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["gh", "run", "download", str(run_id), "-n", artifact, "-D", str(out_dir)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            check=False,
        )
        return proc.returncode == 0 and any(out_dir.glob("*.db.gz"))
    except (OSError, subprocess.SubprocessError):
        return False


def pull(*, tok: str, dest: Path = DEST_DIR, keep_days: int = DEFAULT_KEEP_DAYS, dry_run: bool = False) -> dict[str, Any]:
    art = newest_artifact(tok)
    if not art:
        return {"ok": False, "detail": "没有可用的 vps-db artifact（先触发一次 vps-db-snapshot 工作流）"}
    tmp_dir = dest / ".incoming"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    raw_gz: bytes | None = None
    gz_name = ""
    if not dry_run and _gh_download(int(art["run_id"]), tmp_dir):
        gz_files = sorted(tmp_dir.glob("*.db.gz"))
        gz_name = gz_files[0].name
        raw_gz = gz_files[0].read_bytes()
    else:
        blob = _api(art["url"], tok=tok, raw=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            gz_names = [n for n in zf.namelist() if n.endswith(".db.gz")]
            if not gz_names:
                return {"ok": False, "detail": "artifact 内没有 .db.gz"}
            gz_name = gz_names[0]
            raw_gz = zf.read(gz_name)
    day = snapshot_day(gz_name) or date.today()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"desk-{day.isoformat()}.db"
    if dry_run:
        return {"ok": True, "dry_run": True, "artifact": art, "target": str(target)}
    if target.exists():
        return {
            "ok": True,
            "skipped": "already exists",
            "target": str(target),
            "artifact": art,
            "verify": verify_db(target),
        }
    target.write_bytes(gzip.decompress(raw_gz))
    result = {"ok": True, "target": str(target), "artifact": art, "verify": verify_db(target)}
    removed = prune_old(list(dest.glob("desk-*.db")), keep_days=keep_days)
    result["pruned"] = [str(p) for p in removed]
    result["kept_days"] = keep_days
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拉取 VPS desk.db 每日快照并保留最近 N 天")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    ap.add_argument("--dest", default=str(DEST_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tok = token()
    out = pull(tok=tok, dest=Path(args.dest), keep_days=args.keep_days, dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())