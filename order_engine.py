"""
Order Engine — DRY RUN
======================
create_order() is the ONLY function that would submit a real order.
Currently it is 100% dry-run: it builds the order, signs it if credentials
are available, then LOGS instead of posting.

To go live: set DRY_RUN = False in config and uncomment post_order().
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("polymarket_bot")

# ── Optional real signing via py-clob-client ──────────────────────────────────
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY as CLOB_BUY

    _PK     = os.getenv("POLY_PRIVATE_KEY", "")
    _FUNDER = os.getenv("POLY_SAFE_ADDRESS", "")

    if _PK and _FUNDER:
        _client = ClobClient(
            "https://clob.polymarket.com",
            key=_PK,
            chain_id=137,
            signature_type=1,
            funder=_FUNDER,
        )
        _client.set_api_creds(_client.create_or_derive_api_creds())
        logger.info("py-clob-client ready (DRY RUN — orders will NOT be posted)")
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

    # ── Try to sign (still NOT submitted) ────────────────────────────────────
    if CLOB_AVAILABLE and _client and token_id:
        try:
            args = OrderArgs(token_id=token_id, price=price, size=size, side=CLOB_BUY)
            signed = _client.create_order(args)
            order["signed"]  = True
            order["status"]  = "SIGNED_DRY_RUN"
            order["order_id_preview"] = str(signed)[:40] + "…"
            logger.info(f"[SIGNED] {slug} | {side.upper()} @ ${price:.4f} → NOT posted (dry_run=True)")

            if not dry_run:
                # ══════════════════════════════════════════════════════
                # LIVE MODE — uncomment when ready
                # ══════════════════════════════════════════════════════
                # resp = _client.post_order(signed, OrderType.GTC)
                # order["submitted"] = True
                # order["status"]    = resp.get("status", "SUBMITTED")
                # order["order_id"]  = resp.get("orderID", "")
                # logger.info(f"[LIVE ORDER] {slug} | {resp}")
                # ══════════════════════════════════════════════════════
                logger.warning("dry_run=False but post_order() is commented out for safety")

        except Exception as e:
            logger.warning(f"Signing failed ({e}) → paper-only")
            order["status"] = "PAPER_ONLY"
    else:
        logger.info(f"[PAPER] create_order: {slug} | {side.upper()} @ ${price:.4f} | "
                    f"size={size} shares | payout=${potential_payout:.4f} if wins")

    return order