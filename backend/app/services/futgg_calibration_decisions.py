"""Durable audit log for FUT.GG calibration decisions.

Writing here records a human/engineering decision only. It deliberately has no
import of futgg_config and no mechanism to mutate a running engine.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

_ALLOWED_STATUSES = frozenset({"proposed", "accepted", "rejected", "rolled_back"})
_ALLOWED_HORIZONS = frozenset({"24h", "48h", "7d"})


async def record_decision(
    pool,
    *,
    parameter: str,
    from_engine_version: str,
    rationale: str,
    status: str = "proposed",
    to_engine_version: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
    horizon: Optional[str] = None,
    window_days: Optional[int] = None,
    lower_bucket: Optional[str] = None,
    higher_bucket: Optional[str] = None,
    lower_sample: Optional[int] = None,
    higher_sample: Optional[int] = None,
    profitable_delta_pp: Optional[float] = None,
    p_value: Optional[float] = None,
    adjusted_alpha: Optional[float] = None,
    config_fingerprint: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    source_commit: Optional[str] = None,
) -> Dict[str, Any]:
    parameter = str(parameter or "").strip()
    from_engine_version = str(from_engine_version or "").strip()
    to_engine_version = str(to_engine_version).strip() if to_engine_version is not None else None
    source_commit = str(source_commit).strip() if source_commit is not None else None
    rationale = str(rationale or "").strip()
    status = str(status or "proposed").strip().lower()

    if not parameter:
        raise ValueError("parameter is required")
    if not from_engine_version:
        raise ValueError("from_engine_version is required")
    if not rationale:
        raise ValueError("rationale is required")
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {sorted(_ALLOWED_STATUSES)}")
    if horizon is not None and horizon not in _ALLOWED_HORIZONS:
        raise ValueError(f"horizon must be one of {sorted(_ALLOWED_HORIZONS)}")
    if window_days is not None and int(window_days) <= 0:
        raise ValueError("window_days must be positive")

    # futgg_config's contract says a behavioural configuration change MUST bump
    # ENGINE_VERSION. Do not let the audit trail legitimise the exact opposite.
    if status == "accepted":
        if not to_engine_version:
            raise ValueError("accepted decisions require to_engine_version")
        if to_engine_version == from_engine_version:
            raise ValueError("accepted behavioural changes must bump engine_version")
        if not source_commit:
            raise ValueError("accepted decisions require source_commit")
        if old_value == new_value:
            raise ValueError("accepted decisions require a changed value")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO futgg_calibration_decisions (
                parameter, from_engine_version, to_engine_version,
                old_value, new_value, horizon, window_days,
                lower_bucket, higher_bucket, lower_sample, higher_sample,
                profitable_delta_pp, p_value, adjusted_alpha,
                config_fingerprint, rationale, evidence, status, source_commit
            ) VALUES (
                $1,$2,$3,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb,$18,$19
            )
            RETURNING *
            """,
            parameter,
            from_engine_version,
            to_engine_version,
            json.dumps(old_value),
            json.dumps(new_value),
            horizon,
            int(window_days) if window_days is not None else None,
            lower_bucket,
            higher_bucket,
            int(lower_sample) if lower_sample is not None else None,
            int(higher_sample) if higher_sample is not None else None,
            profitable_delta_pp,
            p_value,
            adjusted_alpha,
            config_fingerprint,
            rationale,
            json.dumps(evidence or {}),
            status,
            source_commit,
        )
    return _serialise(dict(row))


async def list_decisions(pool, *, limit: int = 100) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM futgg_calibration_decisions
            ORDER BY decided_at DESC, id DESC
            LIMIT $1
            """,
            max(1, min(int(limit), 500)),
        )
    return [_serialise(dict(row)) for row in rows]


def _serialise(row: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("decided_at", "created_at"):
        value = row.get(key)
        if value is not None and hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    for key in ("profitable_delta_pp", "p_value", "adjusted_alpha"):
        if row.get(key) is not None:
            row[key] = float(row[key])
    return row
