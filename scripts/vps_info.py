"""Fetch VPS status through GitHub Actions instead of logging into the box.

Why: the VPS is only reachable via the deploy workflow's SSH secrets, and those
secrets must never be copied onto a laptop. GitHub Actions is the one place with
network access + credentials, so we run diagnostics there and read the result
back through the API.

Usage::

    export GITHUB_TOKEN=...        # repo scope: actions:read
    python scripts/vps_info.py                 # recent runs of vps-info workflow
    python scripts/vps_info.py --artifacts     # download + print latest vps-info.json
    python scripts/vps_info.py --workflow deploy.yml --runs 5

The token is read from ``GITHUB_TOKEN`` or ``GH_TOKEN``; nothing is written to
disk except an optional downloaded artifact under ``data/tmp``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO = os.environ.get("VPS_INFO_REPO", "lurenXYN/market-desk")
API = "https://api.github.com"
ARTIFACT_NAME = "vps-info"


def _token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _api(url: str, *, raw: bool = False) -> Any:
    """GET a GitHub API URL; returns parsed JSON or raw bytes."""
    token = _token()
    if not token:
        raise SystemExit("缺少 GITHUB_TOKEN / GH_TOKEN（需要 actions:read 权限）")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "market-desk-vps-info",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"GitHub API {exc.code}: {detail}")
    return data if raw else json.loads(data.decode("utf-8"))


def summarize_runs(payload: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    """Flatten ``/actions/runs`` into a printable table (pure, unit-tested)."""
    out: list[dict[str, Any]] = []
    for run in (payload.get("workflow_runs") or [])[:limit]:
        out.append(
            {
                "id": run.get("id"),
                "name": run.get("name") or run.get("path"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
                "url": run.get("html_url"),
                "head_sha": str(run.get("head_sha") or "")[:8],
            }
        )
    return out


def latest_artifact_url(runs: list[dict[str, Any]], name: str = ARTIFACT_NAME) -> str | None:
    """First finished run's artifact zip URL (newest first as the API returns)."""
    for run in runs:
        if run.get("status") != "completed" or not run.get("id"):
            continue
        arts = _api(f"{API}/repos/{REPO}/actions/runs/{run['id']}/artifacts")
        for art in arts.get("artifacts") or []:
            if str(art.get("name") or "") == name and not art.get("expired"):
                return str(art.get("archive_download_url") or "") or None
    return None


def read_artifact(url: str) -> dict[str, Any]:
    """Download the artifact zip and return the parsed vps-info.json."""
    blob = _api(url, raw=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for member in zf.namelist():
            if member.endswith("vps-info.json"):
                return json.loads(zf.read(member).decode("utf-8"))
    return {"ok": False, "detail": "artifact 内未找到 vps-info.json"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="通过 GitHub Actions 读取 VPS 状态")
    ap.add_argument("--workflow", default="vps-info.yml", help="工作流文件名（默认 vps-info.yml）")
    ap.add_argument("--runs", type=int, default=5, help="查看最近几次运行")
    ap.add_argument("--artifacts", action="store_true", help="下载并打印最新 vps-info.json")
    args = ap.parse_args(argv)

    runs_payload = _api(
        f"{API}/repos/{REPO}/actions/workflows/{args.workflow}/runs?per_page={max(1, args.runs)}"
    )
    runs = summarize_runs(runs_payload, limit=args.runs)
    if not runs:
        print(f"没有 {args.workflow} 的运行记录。先在 GitHub 上手动触发一次该工作流。")
        return 1
    print(f"# {args.workflow} 最近 {len(runs)} 次运行")
    for r in runs:
        print(f"{r['created_at']}  {str(r['status']):<10} {str(r['conclusion'] or '-'):<10} {r['head_sha']}  {r['url']}")
    if not args.artifacts:
        print("\n加 --artifacts 可下载最新一次成功运行的 vps-info.json")
        return 0
    url = latest_artifact_url(runs)
    if not url:
        print("未找到未过期的 vps-info artifact；请先触发一次工作流并等待其完成。")
        return 1
    info = read_artifact(url)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    try:
        out = Path("data/tmp")
        out.mkdir(parents=True, exist_ok=True)
        (out / "vps-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存到 {out / 'vps-info.json'}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())