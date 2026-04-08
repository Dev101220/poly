"""
Order Engine
============
create_order() builds, signs, and (when dry_run=False) SUBMITS a real order.

dry_run is controlled by LIVE_MODE in config.py:
  LIVE_MODE = False  →  paper only  (default, safe)
  LIVE_MODE = True   →  real orders are posted to Polymarket CLOB

Credentials are read from (in priority order):
  1. Environment variables: POLY_PRIVATE_KEY, POLY_SAFE_ADDRESS
  2. config.py:             POLY_PRIVATE_KEY, POLY_SAFE_ADDRESS
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("polymarket_bot")

# ── Credentials: env vars take priority, config.py as fallback ───────────────
try:
    from config import POLY_PRIVATE_KEY as _CFG_PK, POLY_SAFE_ADDRESS as _CFG_FUNDER
except ImportError:
    _CFG_PK = ""
    _CFG_FUNDER = ""

_PK     = os.getenv("POLY_PRIVATE_KEY",  _CFG_PK)
_FUNDER = os.getenv("POLY_SAFE_ADDRESS", _CFG_FUNDER)

# ── Optional real signing via py-clob-client ──────────────────────────────────
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY as CLOB_BUY

    if _PK and _FUNDER:
        _client = ClobClient(
            "https://clob.polymarket.com",
            key=_PK,
            chain_id=137,
            signature_type=1,
            funder=_FUNDER,
        )
        _client.set_api_creds(_client.create_or_derive_api_creds())
        logger.info("py-clob-client ready (credentials loaded)")
    else:
        _client = None
        logger.info("No wallet credentials set → full paper mode")

    CLOB_AVAILABLE = True

except ImportError:
    _client = None
    CLOB_AVAILABLE = False
    logger.info("py-clob-client not installed → full paper mode")


def create_order(
    *,
    token_id: str,
    side: str,          # "up" or "down"
    price: float,       # best_ask price that triggered
    amount: float,      # USDC to spend (e.g. 1.0)
    market_info: dict,  # from GammaClient.get_full_market_info()
    dry_run: bool = True,
) -> dict:
    """
    Build, (optionally) sign, and LOG a limit order.
    NEVER calls post_order() — this is the dry-run firewall.

    Returns a dict with all order details for the log.
    """
    slug     = market_info.get("slug", "")
    question = market_info.get("question", slug)
    url      = market_info.get("polymarket_url", f"https://polymarket.com/event/{slug}")
    end_date = market_info.get("end_date", "")
    ts       = datetime.now(timezone.utc).isoformat()

    # Shares bought = amount / price  (each share pays $1 if outcome wins)
    size           = round(amount / price, 6)
    potential_payout = round(size * 1.0, 4)

    order = {
        "timestamp"       : ts,
        "token_id"        : token_id,
        "side"            : side.upper(),
        "price"           : price,
        "amount_usdc"     : amount,
        "size_shares"     : size,
        "potential_payout": potential_payout,
        "slug"            : slug,
        "question"        : question,
        "polymarket_url"  : url,
        "end_date"        : end_date,
        "order_type"      : "GTC",
        "signed"          : False,
        "submitted"       : False,
        "status"          : "PAPER_ONLY",
        "dry_run"         : dry_run,
    }

    # ── Try to sign ──────────────────────────────────────────────────────────
    if CLOB_AVAILABLE and _client and token_id:
        try:
            args   = OrderArgs(token_id=token_id, price=price, size=size, side=CLOB_BUY)
            signed = _client.create_order(args)
            order["signed"] = True
            order["status"] = "SIGNED_DRY_RUN"
            order["order_id_preview"] = str(signed)[:40] + "…"

            if not dry_run:
                # ── LIVE MODE: submit the order ───────────────────────────
                resp = _client.post_order(signed, OrderType.GTC)
                order["submitted"] = True
                order["status"]    = resp.get("status", "SUBMITTED")
                order["order_id"]  = resp.get("orderID", "")
                logger.info(
                    f"[LIVE ORDER SUBMITTED] {slug} | {side.upper()} @ ${price:.4f} | "
                    f"orderID={order['order_id']} | status={order['status']}"
                )
            else:
                logger.info(
                    f"[SIGNED/DRY-RUN] {slug} | {side.upper()} @ ${price:.4f} → NOT posted"
                )

        except Exception as e:
            logger.warning(f"Order failed ({e}) → paper-only fallback")
            order["status"] = "PAPER_ONLY"
    else:
        logger.info(
            f"[PAPER] {slug} | {side.upper()} @ ${price:.4f} | "
            f"size={size} shares | payout=${potential_payout:.4f} if wins"
        )

    return order
