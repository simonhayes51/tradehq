# app/services/investment_engine/replay/report.py
"""
Artifact writers for a ReplayResult: machine-readable (JSON timeline +
CSV daily states/signals) and a human-readable Markdown summary. Pure
post-processing over an already-completed ReplayResult - never makes or
influences an engine decision (see engine.py's own post-hoc-only note).
"""
from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.investment_engine.replay.engine import ReplayResult, SuppressedWindow
from app.services.investment_engine.replay.ledger_store import ReplaySignal


def _json_default(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


def falling_knife_stats(result: ReplayResult, cohort_key: str) -> Dict[str, Any]:
    """For every day the cohort sat in CRASHING/OVERHEATED (a day the
    engine deliberately did NOT generate signals), compute how much
    further the cohort's own price index fell between that day and the
    lowest point it reached afterwards in this replay. A successful
    falling-knife avoidance looks like: many suppressed days, each
    followed by a further meaningful decline."""
    windows = [w for w in result.suppressed_windows if w.cohort_key == cohort_key]
    if not windows:
        return {"suppressed_days": 0}

    # cohort index by day, from the daily_states this cohort produced.
    index_by_day = {
        d.day: d.cohort_price_index
        for d in result.daily_states
        if d.cohort_key == cohort_key and d.cohort_price_index is not None
    }
    all_days_sorted = sorted(index_by_day)

    further_falls: List[float] = []
    for w in windows:
        future_days = [d for d in all_days_sorted if d > w.day]
        if not future_days:
            continue
        future_min = min(index_by_day[d] for d in future_days)
        if w.cohort_price_index > 0:
            pct_further = max(0.0, (w.cohort_price_index - future_min) / w.cohort_price_index * 100)
            further_falls.append(pct_further)

    return {
        "suppressed_days": len(windows),
        "avg_further_decline_pct": round(statistics.mean(further_falls), 1) if further_falls else None,
        "max_further_decline_pct": round(max(further_falls), 1) if further_falls else None,
        "first_suppressed_day": windows[0].day.isoformat(),
        "last_suppressed_day": windows[-1].day.isoformat(),
    }


def outcome_stats(signals: List[ReplaySignal]) -> Dict[str, Any]:
    closed = [s for s in signals if s.status == "closed" and s.after_tax_roi_pct is not None]
    if not closed:
        return {"closed_count": 0}
    rois = sorted(s.after_tax_roi_pct for s in closed)
    n = len(rois)
    wins = sum(1 for r in rois if r > 0)
    out: Dict[str, Any] = {
        "closed_count": n,
        "win_rate_pct": round(100.0 * wins / n, 1),
        "median_roi_pct": round(statistics.median(rois), 2),
        "mean_roi_pct": round(statistics.mean(rois), 2),
        "worst_roi_pct": round(rois[0], 2),
        "best_roi_pct": round(rois[-1], 2),
        "p10_roi_pct": round(rois[max(0, int(n * 0.10))], 2),
        "p25_roi_pct": round(rois[max(0, int(n * 0.25))], 2),
    }
    for h in (1, 2, 3, 5, 7):
        vals = [s.forward_returns_pct[h] for s in signals if h in s.forward_returns_pct]
        if vals:
            out[f"forward_{h}d_median_pct"] = round(statistics.median(vals), 2)
            out[f"forward_{h}d_mean_pct"] = round(statistics.mean(vals), 2)
    return out


def write_json_artifact(result: ReplayResult, path: Path) -> None:
    payload = {
        "provider_label": result.provider_label,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "n_cards_total": result.n_cards_total,
        "n_days": result.n_days,
        "config_version": result.config_version,
        "daily_states": [asdict(d) for d in result.daily_states],
        "cohort_size_by_day": {d.isoformat(): v for d, v in result.cohort_size_by_day.items()},
        "classification_events": result.classification_events,
        "suppressed_windows": [asdict(w) for w in result.suppressed_windows],
        "signals": [
            {**asdict(s), "opened_on": s.opened_on.isoformat(),
             "max_hold_on": s.max_hold_on.isoformat(),
             "closed_on": s.closed_on.isoformat() if s.closed_on else None}
            for s in result.ledger.all_signals()
        ],
    }
    path.write_text(json.dumps(payload, default=_json_default, indent=2))


def write_daily_states_csv(result: ReplayResult, path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "fr_offset", "cohort", "strategy", "state", "previous_state",
                          "reason", "cohort_price_index", "drawdown_pct", "severity_class",
                          "momentum_current", "is_decelerating", "deceleration_count",
                          "member_count", "candidate_count", "signals_opened", "signals_closed",
                          "avg_confidence"])
        for d in result.daily_states:
            writer.writerow([d.day.isoformat(), d.fr_offset, d.cohort_key, d.strategy_key, d.state,
                              d.previous_state, d.transition_reason, d.cohort_price_index, d.drawdown_pct,
                              d.severity_class, d.momentum_current, d.is_decelerating, d.deceleration_count,
                              d.member_count, d.candidate_count, d.signals_opened, d.signals_closed,
                              d.avg_confidence])


def write_signals_csv(result: ReplayResult, path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "card_id", "strategy", "cohort", "opened_on", "entry_price", "max_buy",
                          "target_price", "expected_roi_pct", "confidence", "liquidity", "investability",
                          "status", "closed_on", "close_price", "outcome", "after_tax_roi_pct", "exit_reason",
                          "fwd_1d_pct", "fwd_2d_pct", "fwd_3d_pct", "fwd_5d_pct", "fwd_7d_pct"])
        for s in result.ledger.all_signals():
            writer.writerow([s.id, s.source_card_id, s.strategy_key, s.cohort_key, s.opened_on.isoformat(),
                              s.entry_price, s.max_buy, s.target_price, s.expected_roi_pct, s.confidence,
                              s.liquidity, s.investability, s.status,
                              s.closed_on.isoformat() if s.closed_on else "", s.close_price, s.outcome,
                              s.after_tax_roi_pct, s.exit_reason,
                              s.forward_returns_pct.get(1), s.forward_returns_pct.get(2),
                              s.forward_returns_pct.get(3), s.forward_returns_pct.get(5),
                              s.forward_returns_pct.get(7)])


def build_markdown_report(result: ReplayResult) -> str:
    lines: List[str] = []
    lines.append("# FC26 PRODUCTION ENGINE REPLAY\n")
    lines.append(f"- Data source: **{result.provider_label}**")
    lines.append(f"- Period: {result.start.isoformat()} .. {result.end.isoformat()} ({result.n_days} days)")
    lines.append(f"- Cards in dataset: {result.n_cards_total}")
    lines.append(f"- Engine config version: `{result.config_version}`\n")

    all_signals = result.ledger.all_signals()
    for cohort_key, strategy_key, title in [
        ("premium_meta", "PREMIUM_POST_CRASH_REBOUND", "Premium/Meta timeline"),
        ("desirable_starter", "LAUNCH_STARTER_ACCUMULATION", "Starter accumulation timeline"),
    ]:
        rows = [d for d in result.daily_states if d.cohort_key == cohort_key and d.strategy_key == strategy_key]
        sigs = [s for s in all_signals if s.strategy_key == strategy_key]
        lines.append(f"## {title}\n")
        if not rows:
            lines.append("_No days with sufficient cohort coverage were observed._\n")
            continue
        lines.append("| Date | FR offset | State | Drawdown | Severity | Signals opened |")
        lines.append("|---|---|---|---|---|---|")
        prev_state = None
        for d in rows:
            if d.state != prev_state:
                dd_str = f"{d.drawdown_pct:.1f}%" if d.drawdown_pct is not None else "—"
                sev_str = d.severity_class or "—"
                lines.append(f"| {d.day} | FR{d.fr_offset:+d} | {d.state} | {dd_str} | {sev_str} | {d.signals_opened} |")
            prev_state = d.state
        lines.append("")
        fk = falling_knife_stats(result, cohort_key)
        if fk.get("suppressed_days"):
            lines.append(
                f"**Falling-knife check**: {fk['suppressed_days']} cohort-days suppressed while "
                f"CRASHING/OVERHEATED (first {fk['first_suppressed_day']}, last {fk['last_suppressed_day']}). "
                f"Average further decline after a suppressed day: {fk.get('avg_further_decline_pct')}%; "
                f"worst case: {fk.get('max_further_decline_pct')}%.\n"
            )
        else:
            lines.append("**Falling-knife check**: cohort never entered CRASHING/OVERHEATED in this replay.\n")

        stats = outcome_stats(sigs)
        lines.append(f"**Signal outcomes** ({stats.get('closed_count', 0)} closed):")
        if stats.get("closed_count"):
            lines.append(
                f"- win rate {stats['win_rate_pct']}%, median ROI {stats['median_roi_pct']}%, "
                f"mean ROI {stats['mean_roi_pct']}%, p25 {stats['p25_roi_pct']}%, p10 {stats['p10_roi_pct']}%, "
                f"worst {stats['worst_roi_pct']}%, best {stats['best_roi_pct']}%"
            )
            fwd = ", ".join(
                f"{h}d={stats[f'forward_{h}d_median_pct']}%" for h in (1, 2, 3, 5, 7)
                if f"forward_{h}d_median_pct" in stats
            )
            if fwd:
                lines.append(f"- median forward return by horizon (post-hoc only): {fwd}")
        lines.append("")

    lines.append("## Cohort bootstrap\n")
    days_sorted = sorted(result.cohort_size_by_day)
    if days_sorted:
        first, mid, last = days_sorted[0], days_sorted[len(days_sorted) // 2], days_sorted[-1]
        for label, d in [("first day", first), ("midpoint", mid), ("last day", last)]:
            lines.append(f"- {label} ({d}): {result.cohort_size_by_day[d]}")
    lines.append(f"- total classification events: {len(result.classification_events)}\n")

    return "\n".join(lines)


def write_all_artifacts(result: ReplayResult, out_dir: Path) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "replay_result.json",
        "daily_states_csv": out_dir / "daily_states.csv",
        "signals_csv": out_dir / "signals.csv",
        "report_md": out_dir / "REPLAY_REPORT.md",
    }
    write_json_artifact(result, paths["json"])
    write_daily_states_csv(result, paths["daily_states_csv"])
    write_signals_csv(result, paths["signals_csv"])
    paths["report_md"].write_text(build_markdown_report(result))
    return paths
