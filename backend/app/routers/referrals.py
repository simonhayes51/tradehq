# app/routers/referrals.py
from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Dict, Any
from app.db import get_db
from app.auth.entitlements import compute_entitlements
import secrets
import string
from datetime import datetime, timezone

router = APIRouter(prefix="/api/referrals", tags=["Referrals"])


def generate_referral_code(user_id: str) -> str:
    """Generate a unique referral code for a user."""
    prefix = user_id[:4].upper() if len(user_id) >= 4 else user_id.upper()
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"{prefix}{random_part}"


@router.get("/stats")
async def get_referral_stats(
    req: Request,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return the signed-in user's personal referral code and performance."""
    try:
        ent = await compute_entitlements(req)
        user_id = ent.get("user_id")

        if not user_id:
            raise HTTPException(status_code=401, detail="Not authenticated")

        existing_code = await db.fetchrow(
            """
            SELECT code, created_at
            FROM referral_codes
            WHERE user_id = $1
              AND code_type = 'user'
            """,
            user_id,
        )

        referral_code = None
        if existing_code:
            referral_code = existing_code["code"]
        else:
            referral_code = generate_referral_code(user_id)
            try:
                await db.execute(
                    """
                    INSERT INTO referral_codes (
                        user_id, code, code_type, is_active, created_at, updated_at
                    )
                    VALUES ($1, $2, 'user', TRUE, $3, $3)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    user_id,
                    referral_code,
                    datetime.now(timezone.utc),
                )
            except Exception:
                existing = await db.fetchrow(
                    """
                    SELECT code
                    FROM referral_codes
                    WHERE user_id = $1
                      AND code_type = 'user'
                    """,
                    user_id,
                )
                if existing:
                    referral_code = existing["code"]

        clicks = await db.fetchval(
            "SELECT COUNT(*) FROM referral_clicks WHERE code = $1",
            referral_code,
        ) or 0

        unique_clicks = await db.fetchval(
            """
            SELECT COUNT(DISTINCT ip_address)
            FROM referral_clicks
            WHERE code = $1
            """,
            referral_code,
        ) or 0

        conversions = await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE referred_by = $1",
            referral_code,
        ) or 0

        active_subscriptions = await db.fetchval(
            """
            SELECT COUNT(DISTINCT u.id)
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            WHERE u.referred_by = $1
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
            referral_code,
        )

        referral_list = [
            {
                "user_id": r["id"][:8] + "...",
                "joined_at": r["created_at"].isoformat() if r["created_at"] else None,
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
                "conversion_rate": round((conversions / unique_clicks * 100) if unique_clicks > 0 else 0, 2),
            },
            "rewards": {
                "total_earned": total_rewards,
                "per_conversion": reward_per_conversion,
                "currency": "coins",
            },
            "recent_referrals": referral_list,
            "share_url": f"https://eafc.live/r/{referral_code}",
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
    """Track a referral click and persist attribution through signup."""
    try:
        normalized_code = code.strip().upper()
        if not normalized_code:
            raise HTTPException(status_code=404, detail="Unknown referral code")

        valid = await db.fetchrow(
            """
            SELECT code, code_type, partner_name
            FROM referral_codes
            WHERE code = $1
              AND is_active = TRUE
            """,
            normalized_code,
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
            valid["code"],
            ip_address,
            user_agent,
            datetime.now(timezone.utc),
        )

        # Starlette's signed session cookie survives both email registration and
        # the Discord OAuth round trip. Account creation consumes this once.
        req.session["referral_code"] = valid["code"]
        req.session["referral_recorded_at"] = datetime.now(timezone.utc).isoformat()

        return {
            "ok": True,
            "message": "Click tracked",
            "code": valid["code"],
            "type": valid["code_type"],
        }

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"track-click error: {e}")
        return {"ok": False, "message": "Failed to track click"}
