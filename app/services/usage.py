from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func

from app.llm.usage import UsageMeter
from app.models import UsageLog, User
from app.state import AppState


def token_charge_micros(state: AppState, org_id: str, prompt: int, completion: int) -> int:
    """Price of tokens on the platform's own key, in 1e-6 of the currency (price per 1M tokens == micros per token).
    A school's negotiated rates, when set, replace the platform defaults."""
    from app.services.tenancy import org_pricing

    p = org_pricing(state, org_id)
    return int(round(prompt * p["input"] + completion * p["output"]))


def record_usage(state: AppState, user_id: str, kind: str, ref_id: str, meter: UsageMeter, resolved=None) -> None:
    if meter.calls == 0 and meter.cache_hits == 0:
        return
    billing = resolved.billing if resolved is not None else "platform"
    model = resolved.provider.model if resolved is not None else state.provider.model
    # Only tokens spent on the platform's key are ours to charge; a customer's own key is billed by their provider.
    from app.services.tenancy import org_of

    org_id = org_of(state, user_id)
    charge = token_charge_micros(state, org_id, meter.prompt_tokens, meter.completion_tokens) if billing == "platform" else 0
    with state.db.session() as db:
        db.add(UsageLog(user_id=user_id, org_id=org_id, kind=kind, ref_id=ref_id, model=model, calls=meter.calls,
                        prompt_tokens=meter.prompt_tokens, completion_tokens=meter.completion_tokens,
                        cached_prompt_tokens=meter.cached_prompt_tokens, cache_hits=meter.cache_hits,
                        tokens_saved=meter.tokens_saved, billing=billing, charge_micros=charge))


def _today_start() -> dt.datetime:
    now = dt.datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def tokens_used_today(state: AppState, user_id: str) -> int:
    with state.db.session() as db:
        total = (db.query(func.coalesce(func.sum(UsageLog.prompt_tokens + UsageLog.completion_tokens), 0))
                 .filter(UsageLog.user_id == user_id, UsageLog.billing == "platform",
                         UsageLog.created_at >= _today_start()).scalar())
        return int(total or 0)


def enforce_budget(state: AppState, user_id: str) -> None:
    """Refuse new model work once the user's daily token allowance is spent (0 = unlimited)."""
    budget = state.settings.daily_token_budget
    from app.services.ai_settings import resolve_ai  # local import: ai_settings imports state, which imports nothing here

    if resolve_ai(state, user_id).billing != "platform":
        return  # a customer's own key, or no AI at all, never draws on the platform allowance
    if budget and tokens_used_today(state, user_id) >= budget:
        raise HTTPException(429, f"Daily token budget of {budget:,} reached. It resets at 00:00 UTC.")


def usage_summary(state: AppState, user_id: Optional[str] = None, days: int = 30, org_id: Optional[str] = None) -> Dict[str, Any]:
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    with state.db.session() as db:
        q = db.query(
            UsageLog.kind, func.count(UsageLog.id), func.sum(UsageLog.calls), func.sum(UsageLog.prompt_tokens),
            func.sum(UsageLog.completion_tokens), func.sum(UsageLog.cached_prompt_tokens),
            func.sum(UsageLog.cache_hits), func.sum(UsageLog.tokens_saved)).filter(UsageLog.created_at >= since)
        if user_id:
            q = q.filter(UsageLog.user_id == user_id)
        if org_id:
            q = q.filter(UsageLog.org_id == org_id)
        rows = q.group_by(UsageLog.kind).all()
    by_kind = {}
    totals = {"jobs": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0,
              "cache_hits": 0, "tokens_saved": 0}
    for kind, jobs, calls, pt, ct, cpt, hits, saved in rows:
        # PostgreSQL returns SUM() of a bigint as Decimal; keep everything plain ints.
        row = {"jobs": int(jobs), "calls": int(calls or 0), "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
               "cached_prompt_tokens": int(cpt or 0), "cache_hits": int(hits or 0), "tokens_saved": int(saved or 0)}
        by_kind[kind] = row
        for k, v in row.items():
            totals[k] += v
    out: Dict[str, Any] = {"days": days, "totals": totals, "by_kind": by_kind,
                           "daily_budget": state.settings.daily_token_budget}
    if user_id:
        out["used_today"] = tokens_used_today(state, user_id)
    return out


def usage_by_user(state: AppState, days: int = 30, org_id: Optional[str] = None) -> list:
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    with state.db.session() as db:
        rows = (db.query(UsageLog.user_id, func.sum(UsageLog.prompt_tokens), func.sum(UsageLog.completion_tokens),
                         func.sum(UsageLog.tokens_saved), func.count(UsageLog.id))
                .filter(UsageLog.created_at >= since, *([UsageLog.org_id == org_id] if org_id else []))
                .group_by(UsageLog.user_id).all())
        names = {u.id: u.email for u in db.query(User).filter(User.id.in_([r[0] for r in rows] or [""])).all()}
    return sorted(({"user_id": uid, "email": names.get(uid, ""), "prompt_tokens": int(pt or 0), "completion_tokens": int(ct or 0),
                    "tokens_saved": int(sv or 0), "jobs": int(n)} for uid, pt, ct, sv, n in rows),
                  key=lambda r: -(r["prompt_tokens"] + r["completion_tokens"]))


def _month_bounds(month: Optional[str]) -> Tuple[dt.datetime, dt.datetime, str]:
    now = dt.datetime.utcnow()
    try:
        y, m = (int(x) for x in (month or f"{now.year}-{now.month:02d}").split("-"))
        start = dt.datetime(y, m, 1)
    except (ValueError, TypeError):
        raise HTTPException(422, "Month must look like 2026-10.")
    end = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start, end, f"{y}-{m:02d}"


def billing_summary(state: AppState, user_id: Optional[str] = None, month: Optional[str] = None,
                    org_id: Optional[str] = None) -> Dict[str, Any]:
    """What the platform would invoice for a month. With no platform tokens the invoice is the fee alone."""
    start, end, label = _month_bounds(month)
    s = state.settings
    from app.services.tenancy import org_pricing

    price = org_pricing(state, org_id or "")
    with state.db.session() as db:
        q = db.query(UsageLog.billing, func.count(UsageLog.id), func.sum(UsageLog.prompt_tokens),
                     func.sum(UsageLog.completion_tokens), func.sum(UsageLog.charge_micros),
                     func.sum(UsageLog.tokens_saved)).filter(UsageLog.created_at >= start, UsageLog.created_at < end)
        if user_id:
            q = q.filter(UsageLog.user_id == user_id)
        if org_id:
            q = q.filter(UsageLog.org_id == org_id)
        rows = {b: (int(n or 0), int(pt or 0), int(ct or 0), int(ch or 0), int(sv or 0)) for b, n, pt, ct, ch, sv in q.group_by(UsageLog.billing).all()}
    plat = rows.get("platform", (0, 0, 0, 0, 0))
    byok = rows.get("byok", (0, 0, 0, 0, 0))
    token_charge = plat[3] / 1_000_000
    fee = 0.0 if user_id else price["fee"]  # the flat fee belongs to the school, not to one teacher
    out = {
        "month": label, "currency": s.currency, "scope": "user" if user_id else "workspace",
        "platform": {"jobs": plat[0], "prompt_tokens": plat[1], "completion_tokens": plat[2],
                     "tokens": plat[1] + plat[2], "charge": round(token_charge, 4)},
        "own_key": {"jobs": byok[0], "tokens": byok[1] + byok[2], "charge": 0.0,
                    "note": "Billed by your own AI provider, not by ExamForge."},
        "tokens_saved_by_cache": plat[4] + byok[4],
        "platform_fee": round(fee, 4), "total": round(fee + token_charge, 4),
        "pricing": {"input_per_million": price["input"], "output_per_million": price["output"],
                    "platform_fee_monthly": price["fee"], "configured": bool(price["input"] or price["output"] or price["fee"])},
    }
    out["summary"] = ("No platform AI tokens were used, so only the platform fee applies." if not plat[1] + plat[2]
                      else "Platform AI tokens are charged at the rates shown; your own key is never charged by us.")
    return out


def billing_by_user(state: AppState, month: Optional[str] = None, org_id: Optional[str] = None) -> list:
    start, end, _ = _month_bounds(month)
    with state.db.session() as db:
        rows = (db.query(UsageLog.user_id, UsageLog.billing, func.sum(UsageLog.prompt_tokens + UsageLog.completion_tokens),
                         func.sum(UsageLog.charge_micros))
                .filter(UsageLog.created_at >= start, UsageLog.created_at < end, *([UsageLog.org_id == org_id] if org_id else []))
                .group_by(UsageLog.user_id, UsageLog.billing).all())
        names = {u.id: u.email for u in db.query(User).filter(User.id.in_([r[0] for r in rows] or [""])).all()}
    agg: Dict[str, Dict[str, Any]] = {}
    for uid, billing, tokens, charge in rows:
        e = agg.setdefault(uid, {"user_id": uid, "email": names.get(uid, ""), "platform_tokens": 0, "own_key_tokens": 0, "charge": 0.0})
        if billing == "platform":
            e["platform_tokens"] += int(tokens or 0)
            e["charge"] += int(charge or 0) / 1_000_000
        elif billing == "byok":
            e["own_key_tokens"] += int(tokens or 0)
    return sorted(agg.values(), key=lambda r: -r["charge"])
