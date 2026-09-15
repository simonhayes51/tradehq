# app/routers/referrals.py
from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.db import get_db
from app.auth.entitlements import compute_entitlements
import secrets
import string
import re
from datetime import datetime, timezone

router = APIRouter(prefix="/api/referrals", tags=["Referrals"])

PARTNER_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


def generate_referral_code(user_id: str) -> str:
    """Generate a unique referral code for a user."""
    prefix = user_id[:4].upper() if len(user_id) >= 4 else user_id.upper()
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"{prefix}{random_part}"


async def require_referral_admin(req: Request) -> Dict[str, Any]:
    """Use the same entitlement-backed admin decision as the rest of Admin."""
    ent = await compute_entitlements(req)
    if not ent.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not ent.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return ent


class PartnerReferralCreate(BaseModel):
    partner_name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=3, max_length=32)
    is_active: bool = True


class PartnerReferralUpdate(BaseModel):
    partner_name: str | None = Field(default=None, min_length=2, max_length=120)
    is_active: bool | None = None


def _normalise_partner_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not PARTNER_CODE_RE.fullmatch(code):
        raise HTTPException(
            status_code=400,
            detail="Code must be 3-32 characters using only A-Z, 0-9, _ or -",
        )
    return code


@router.get("/stats")
async def get_referral_stats(
    req: Request,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Generate unique referral codes, track clicks and conversions,
    calculate rewards earned.
    """
    try:
        ent = await compute_entitlements(req)
        user_id = ent.get("user_id")

        if not user_id:
            raise HTTPException(status_code=401, detail="Not authenticated")

        existing_code = await db.fetchrow(
            "SELECT code, created_at FROM referral_codes WHERE user_id = $1",
            user_id
        )

        referral_code = None
        if existing_code:
            referral_code = existing_code["code"]
        else:
            referral_code = generate_referral_code(user_id)

            try:
                await db.execute(
                    """
                    INSERT INTO referral_codes (user_id, code, created_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    user_id, referral_code, datetime.utcnow()
                )
            except Exception:
                existing = await db.fetchrow(
                    "SELECT code FROM referral_codes WHERE user_id = $1",
                    user_id
                )
                if existing:
                    referral_code = existing["code"]

        clicks = await db.fetchval(
            "SELECT COUNT(*) FROM referral_clicks WHERE code = $1",
            referral_code
        ) or 0

        unique_clicks = await db.fetchval(
            "SELECT COUNT(DISTINCT ip_address) FROM referral_clicks WHERE code = $1",
            referral_code
        ) or 0

        conversions = await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE referred_by = $1",
            referral_code
        ) or 0

        active_subscriptions = await db.fetchval(
            """
            SELECT COUNT(DISTINCT u.id)
            FROM users u
            JOIN subscriptions s ON s.user_id=u.id
            WHERE u.referred_by=$1
              AND s.status IN ('active', 'trialing', 'trial', 'past_due')
              AND (s.current_period_end IS NULL OR s.current_period_end > NOW())
            """,
            referral_code,
        ) or 0

        reward_per_conversion = 100
        total_rewards = conversions * reward_per_conversion

        recent_referrals = await db.fetch(
            """
            SELECT id, created_at
            FROM users
            WHERE referred_by = $1
            ORDER BY created_at DESC
            LIMIT 10
            """,
            referral_code
        )

        referral_list = [
            {
                "user_id": r["id"][:8] + "...",
                "joined_at": r["created_at"].isoformat() if r["created_at"] else None
            }
            for r in recent_referrals
        ]

        return {
            "ok": True,
            "referral_code": referral_code,
            "stats": {
                "total_clicks": clicks,
                "unique_clicks": unique_clicks,
                "conversions": conversions,
                "active_subscriptions": active_subscriptions,
                "conversion_rate": round((conversions / unique_clicks * 100) if unique_clicks > 0 else 0, 2)
            },
            "rewards": {
                "total_earned": total_rewards,
                "per_conversion": reward_per_conversion,
                "currency": "coins"
            },
            "recent_referrals": referral_list,
            "share_url": f"https://eafc.live/r/{referral_code}"
        }

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"referral-stats error: {e}")
        raise HTTPException(status_code=500, detail=f"Referral stats failed: {str(e)}")


@router.post("/track-click")
async def track_referral_click(
    code: str,
    req: Request,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Track a referral click and preserve attribution through signup."""
    try:
        code = (code or "").strip().upper()
        valid = await db.fetchrow(
            "SELECT user_id FROM referral_codes WHERE code=$1 AND COALESCE(is_active, TRUE)=TRUE",
            code
        )
        if not valid:
            raise HTTPException(status_code=404, detail="Unknown referral code")

        ip_address = req.client.host if req.client else "unknown"
        user_agent = req.headers.get("user-agent", "")

        await db.execute(
            """
            INSERT INTO referral_clicks (code, ip_address, user_agent, clicked_at)
            VALUES ($1, $2, $3, $4)
            """,
            code, ip_address, user_agent, datetime.utcnow()
        )

        req.session["referral_code"] = code
        req.session["referral_recorded_at"] = datetime.now(timezone.utc).isoformat()

        return {"ok": True, "message": "Click tracked"}

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"track-click error: {e}")
        return {"ok": False, "message": "Failed to track click"}


# ----------------------- Admin partner referrals -----------------------

@router.get("/admin/partners")
async def list_partner_referrals(
    req: Request,
    db=Depends(get_db),
    admin=Depends(require_referral_admin),
) -> Dict[str, Any]:
    """List partner codes with live acquisition and subscription stats."""
    rows = await db.fetch(
        """
        SELECT
            rc.code,
            rc.partner_name,
            rc.is_active,
            rc.created_at,
            rc.updated_at,
            COUNT(DISTINCT rclick.id) AS total_clicks,
            COUNT(DISTINCT NULLIF(rclick.ip_address, '')) AS unique_clicks,
            COUNT(DISTINCT u.id) AS signups,
            COUNT(DISTINCT CASE
                WHEN s.status IN ('active', 'trialing', 'trial', 'past_due')
                 AND (s.current_period_end IS NULL OR s.current_period_end > NOW())
                THEN u.id END
            ) AS active_subscriptions
        FROM referral_codes rc
        LEFT JOIN referral_clicks rclick ON rclick.code = rc.code
        LEFT JOIN users u ON u.referred_by = rc.code
        LEFT JOIN subscriptions s ON s.user_id = u.id
        WHERE rc.code_type = 'partner'
        GROUP BY rc.code, rc.partner_name, rc.is_active, rc.created_at, rc.updated_at
        ORDER BY rc.created_at DESC, rc.code ASC
        """
    )

    partners = []
    for row in rows:
        unique_clicks = int(row["unique_clicks"] or 0)
        signups = int(row["signups"] or 0)
        partners.append({
            "code": row["code"],
            "partner_name": row["partner_name"] or row["code"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "total_clicks": int(row["total_clicks"] or 0),
            "unique_clicks": unique_clicks,
            "signups": signups,
            "active_subscriptions": int(row["active_subscriptions"] or 0),
            "conversion_rate": round((signups / unique_clicks * 100) if unique_clicks else 0, 2),
            "share_url": f"https://eafc.live/r/{row['code']}",
        })

    return {"ok": True, "partners": partners}


@router.post("/admin/partners")
async def create_partner_referral(
    payload: PartnerReferralCreate,
    req: Request,
    db=Depends(get_db),
    admin=Depends(require_referral_admin),
) -> Dict[str, Any]:
    """Create a reusable partner referral code from the browser Admin area."""
    code = _normalise_partner_code(payload.code)
    partner_name = payload.partner_name.strip()

    existing = await db.fetchrow("SELECT code FROM referral_codes WHERE code=$1", code)
    if existing:
        raise HTTPException(status_code=409, detail="That referral code already exists")

    row = await db.fetchrow(
        """
        INSERT INTO referral_codes (
            user_id, code, code_type, partner_name, is_active, created_at, updated_at
        )
        VALUES (NULL, $1, 'partner', $2, $3, NOW(), NOW())
        RETURNING code, partner_name, is_active, created_at, updated_at
        """,
        code, partner_name, payload.is_active,
    )

    return {
        "ok": True,
        "partner": {
            "code": row["code"],
            "partner_name": row["partner_name"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "total_clicks": 0,
            "unique_clicks": 0,
            "signups": 0,
            "active_subscriptions": 0,
            "conversion_rate": 0,
            "share_url": f"https://eafc.live/r/{row['code']}",
        },
    }


@router.patch("/admin/partners/{code}")
async def update_partner_referral(
    code: str,
    payload: PartnerReferralUpdate,
    req: Request,
    db=Depends(get_db),
    admin=Depends(require_referral_admin),
) -> Dict[str, Any]:
    """Rename or enable/disable a partner code without deleting its history."""
    code = _normalise_partner_code(code)
    current = await db.fetchrow(
        "SELECT code, partner_name, is_active FROM referral_codes WHERE code=$1 AND code_type='partner'",
        code,
    )
    if not current:
        raise HTTPException(status_code=404, detail="Partner referral code not found")

    partner_name = payload.partner_name.strip() if payload.partner_name is not None else current["partner_name"]
    is_active = payload.is_active if payload.is_active is not None else bool(current["is_active"])

    row = await db.fetchrow(
        """
        UPDATE referral_codes
        SET partner_name=$2, is_active=$3, updated_at=NOW()
        WHERE code=$1 AND code_type='partner'
        RETURNING code, partner_name, is_active, updated_at
        """,
        code, partner_name, is_active,
    )

    return {
        "ok": True,
        "partner": {
            "code": row["code"],
            "partner_name": row["partner_name"],
            "is_active": bool(row["is_active"]),
            "updated_at": row["updated_at"].isoformat(),
            "share_url": f"https://eafc.live/r/{row['code']}",
        },
    }
