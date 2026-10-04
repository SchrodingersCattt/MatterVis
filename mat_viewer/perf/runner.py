"""Manifest-driven end-to-end benchmark runner."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .bench_pipeline import build_pipeline_report
from .manifest import fixture_paths, load_manifest

SCHEMA = "mattervis.perf.suite/v1"


def _timing_row(fixture: str, stage: str, timing: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture": fixture,
        "stage": stage,
        "status": "ok",
        "repeat": timing.get("repeat"),
        "mean_ms": timing.get("mean_ms"),
        "median_ms": timing.get("median_ms"),
        "min_ms": timing.get("min_ms"),
        "max_ms": timing.get("max_ms"),
        "bytes": None,
        "count": None,
    }


def _rows_for_report(entry: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    fixture = str(entry["id"])
    rows: list[dict[str, Any]] = []
    for stage, timing in (report.get("stages") or {}).items():
        if isinstance(timing, dict) and "mean_ms" in timing:
            rows.append(_timing_row(fixture, stage, timing))
        elif isinstance(timing, dict):
            for phase, phase_timing in timing.items():
                if isinstance(phase_timing, dict) and "mean_ms" in phase_timing:
                    rows.append(_timing_row(fixture, f"{stage}.{phase}", phase_timing))
    figure = report.get("figure") or {}
    if figure:
        rows.append({
            "fixture": fixture,
            "stage": "figure.json_encode",
            "status": "ok",
            "repeat": (figure.get("json_encode") or {}).get("repeat"),
            "mean_ms": (figure.get("json_encode") or {}).get("mean_ms"),
            "median_ms": (figure.get("json_encode") or {}).get("median_ms"),
            "min_ms": (figure.get("json_encode") or {}).get("min_ms"),
            "max_ms": (figure.get("json_encode") or {}).get("max_ms"),
            "bytes": figure.get("json_bytes"),
            "count": figure.get("traces"),
        })
    for extension, result in (report.get("exports") or {}).items():
        rows.append({
            "fixture": fixture,
            "stage": f"export.{extension}",
            "status": result.get("status"),
            "repeat": 1,
            "mean_ms": result.get("duration_ms"),
            "median_ms": result.get("duration_ms"),
            "min_ms": result.get("duration_ms"),
            "max_ms": result.get("duration_ms"),
            "bytes": result.get("bytes"),
            "count": None,
        })
    return rows


def browser_probe(*, requested: bool, url: str | None = None) -> dict[str, Any]:
    """Measure optional headless first paint against an already running server."""
    if not requested:
        return {"status": "disabled"}
    if not url:
        return {"status": "skipped", "reason": "--browser-url was not supplied"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "skipped", "reason": "Playwright is not installed"}
    import time

    started = time.perf_counter()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            dom_content_loaded_ms = (time.perf_counter() - started) * 1000.0
            page.locator("#crystal-graph").wait_for(state="attached", timeout=120_000)
            first_graph_ms = (time.perf_counter() - started) * 1000.0
            browser.close()
        return {
            "status": "ok",
            "url": url,
            "dom_content_loaded_ms": dom_content_loaded_ms,
            "first_graph_ms": first_graph_ms,
        }
    except Exception as exc:  # pragma: no cover - requires external browser
        return {"status": "error", "url": url, "error": f"{type(exc).__name__}: {exc}"}


def run_suite(
    *,
    manifest_path: str | Path | None = None,
    fixture_ids: set[str] | None = None,
    repeat: int = 1,
    smoke: bool = False,
    include_exports: bool = False,
    browser: bool = False,
    browser_url: str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    entries = fixture_paths(manifest)
    if fixture_ids:
        entries = [entry for entry in entries if entry["id"] in fixture_ids]
    if smoke:
        entries = entries[:1]
    if not entries:
        raise ValueError("benchmark selection is empty")

    reports: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for entry in entries:
        report = build_pipeline_report(
            Path(entry["path"]),
            repeat=max(1, int(repeat)),
            include_unit_cell=True,
            include_figure=True,
            include_exports=include_exports and not smoke,
        )
        report["manifest_entry"] = {
            key: entry[key]
            for key in ("id", "category", "size_class", "source", "license", "sha256")
            if key in entry
        }
        reports.append(report)
        rows.extend(_rows_for_report(entry, report))

    return {
        "schema": SCHEMA,
        "manifest": {
            "schema": manifest["schema"],
            "path": manifest.get("path"),
            "entries": len(entries),
        },
        "options": {
            "repeat": max(1, int(repeat)),
            "smoke": bool(smoke),
            "include_exports": bool(include_exports and not smoke),
        },
        "browser": browser_probe(requested=browser, url=browser_url),
        "reports": reports,
        "rows": rows,
    }


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "fixture", "stage", "status", "repeat", "mean_ms", "median_ms",
        "min_ms", "max_ms", "bytes", "count",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MatterVis manifest benchmark suite.")
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--fixture", action="append", dest="fixtures", default=[])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--smoke", action="store_true", help="Run the stable short CI subset.")
    parser.add_argument("--exports", action="store_true", help="Measure HTML/PNG/SVG/PDF export when available.")
    parser.add_argument("--browser", action="store_true", help="Request the optional browser benchmark stage.")
    parser.add_argument("--browser-url", default=None, help="Running Dash URL for the optional browser stage.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args(argv)
    suite = run_suite(
        manifest_path=args.manifest,
        fixture_ids=set(args.fixtures) or None,
        repeat=max(1, args.repeat),
        smoke=args.smoke,
        include_exports=args.exports,
        browser=args.browser,
        browser_url=args.browser_url,
    )
    encoded = json.dumps(suite, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.csv:
        write_csv(suite["rows"], args.csv)
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
