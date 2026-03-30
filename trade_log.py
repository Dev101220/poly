"""
Trade Logger
============
Writes every event to trades_log.txt (one row per event).
Saves a full P&L summary table to summary.txt on shutdown.
"""

import logging
import os
from datetime import datetime, timezone

from config import TRADES_LOG, SUMMARY_FILE, STARTING_BANKROLL, TRADE_AMOUNT

logger = logging.getLogger("polymarket_bot")
_header_done = False
_row_n       = [0]


def setup_logger(debug_log: str = "bot_debug.log"):
    root = logging.getLogger("polymarket_bot")
    if root.handlers:
        return root  # already configured
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.FileHandler(debug_log, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return root


# ── trades_log.txt ─────────────────────────────────────────────────────────────

def _write_header():
    global _header_done
    if _header_done:
        return
    with open(TRADES_LOG, "w", encoding="utf-8") as f:
        f.write("=" * 150 + "\n")
        f.write("  POLYMARKET BTC 5-MIN PAPER TRADING BOT — TRADE LOG\n")
        f.write(f"  Started  : {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"  Bankroll : ${STARTING_BANKROLL:.2f} (paper money)\n")
        f.write("=" * 150 + "\n")

        hdr = (
            f"{'#':<5} {'UTC Time':<22} {'Slug':<38} {'Side':<6} "
            f"{'Ask $':<8} {'Shares':<8} {'Payout':<10} "
            f"{'Outcome':<10} {'Result':<8} {'Net $':<12} "
            f"{'Status':<18} {'Market Link'}  Notes"
        )
        f.write(hdr + "\n")
        f.write("-" * 150 + "\n")
    _header_done = True


def log_trade_event(
    *,
    slug: str,
    side: str,
    price: float,
    market_info: dict,
    order: dict,
    outcome: str = None,
    won: bool = None,
    net: float = None,
    skipped: bool = False,
    skip_reason: str = "",
    note: str = "",
):
    _write_header()
    _row_n[0] += 1
    n   = _row_n[0]
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    url = market_info.get("polymarket_url", f"https://polymarket.com/event/{slug}")

    shares = order.get("size_shares", round(TRADE_AMOUNT / price, 4)) if order else "-"
    payout = order.get("potential_payout", "-")                        if order else "-"
    status = order.get("status", "SKIPPED")                            if order else "SKIPPED"

    if skipped:
        result_s  = "SKIP"
        outcome_s = "-"
        net_s     = "-"
        row_note  = f"  <- {skip_reason}" if skip_reason else ""
    elif won is None:
        result_s  = "OPEN"
        outcome_s = "PENDING"
        net_s     = f"-${TRADE_AMOUNT:.2f}"
        row_note  = f"  | {note}" if note else ""
    elif won:
        result_s  = "WIN"
        outcome_s = outcome or "?"
        net_s     = f"+${net:.4f}"
        row_note  = f"  | {note}" if note else ""
    else:
        result_s  = "LOSS"
        outcome_s = outcome or "?"
        net_s     = f"-${TRADE_AMOUNT:.2f}"
        row_note  = f"  | {note}" if note else ""

    row = (
        f"{n:<5} {ts:<22} {slug[:37]:<38} {side[:5]:<6} "
        f"${price:<7.4f} {str(shares):<8} ${str(payout):<9} "
        f"{outcome_s:<10} {result_s:<8} {net_s:<12} "
        f"{status:<18} {url}{row_note}"
    )

    with open(TRADES_LOG, "a", encoding="utf-8") as f:
        f.write(row + "\n")


def log_skipped(state, market_info, reason, price=None):
    slug = market_info.get("slug", "unknown")
    side = market_info.get("outcome", "-")
    p    = price if price else 0.0
    log_trade_event(
        slug=slug, side=side, price=p,
        market_info=market_info, order=None,
        skipped=True, skip_reason=reason,
    )


# ── summary ────────────────────────────────────────────────────────────────────

def build_summary(state: dict) -> str:
    trades   = state["traded_markets"]
    bankroll = state["bankroll"]
    strat_n  = state.get("strategy", "?")
    strat_cfg = state.get("strat_cfg", {})

    all_t    = list(trades.values())
    resolved = [t for t in all_t if t.get("won") is not None]
    pending  = [t for t in all_t if t.get("won") is None and not t.get("sold_early")]
    wins     = [t for t in resolved if t["won"]]
    losses   = [t for t in resolved if not t["won"]]
    early    = [t for t in all_t if t.get("sold_early")]

    pnl      = bankroll - STARTING_BANKROLL
    win_rate = (len(wins) / len(resolved) * 100) if resolved else 0.0
    avg_win  = (sum(t["net"] for t in wins)   / len(wins))   if wins   else 0.0
    avg_loss = (sum(t["net"] for t in losses) / len(losses)) if losses else 0.0
    total_net = sum(t["net"] for t in resolved if t.get("net") is not None)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append("=" * 100)
    lines.append("  POLYMARKET BTC 5-MIN BOT — SUMMARY")
    lines.append(f"  Generated  : {now}")
    lines.append(f"  Strategy   : {strat_n} — {strat_cfg.get('name','')}")
    lines.append(f"  Description: {strat_cfg.get('description','')}")
    lines.append("=" * 100)
    lines.append(f"  Starting Bankroll   : ${STARTING_BANKROLL:.2f}")
    lines.append(f"  Current Bankroll    : ${bankroll:.2f}")
    lines.append(f"  Total P&L           : {'+'if pnl>=0 else ''}{pnl:.4f}$")
    lines.append(f"  Trades placed       : {len(all_t)}")
    lines.append(f"  Resolved            : {len(resolved)}")
    lines.append(f"  Pending             : {len(pending)}")
    lines.append(f"  Early exits (Strat3): {len(early)}")
    lines.append(f"  Wins                : {len(wins)}")
    lines.append(f"  Losses              : {len(losses)}")
    lines.append(f"  Win Rate            : {win_rate:.1f}%")
    lines.append(f"  Avg Win Net         : +${avg_win:.4f}")
    lines.append(f"  Avg Loss Net        : ${avg_loss:.4f}")
    lines.append(f"  Total Net (resolved): {'+'if total_net>=0 else ''}{total_net:.4f}$")
    lines.append("")
    lines.append(
        f"  {'#':<4} {'Slug':<38} {'Side':<6} {'Price':<8} "
        f"{'Outcome':<14} {'Result':<8} {'Net':<12} Link"
    )
    lines.append("  " + "-" * 115)

    for i, (slug, t) in enumerate(trades.items(), 1):
        side    = t.get("side", "-")
        price   = t.get("price", 0.0)
        outcome = t.get("outcome") or ("SOLD_EARLY" if t.get("sold_early") else "PENDING")
        won     = t.get("won")
        net     = t.get("net", -TRADE_AMOUNT)
        url     = t.get("market_info", {}).get(
                      "polymarket_url", f"https://polymarket.com/event/{slug}")

        if t.get("sold_early"):
            result_s = "SOLD"
            net_s    = f"+${net:.4f}"
        elif won is None:
            result_s = "OPEN"
            net_s    = f"-${TRADE_AMOUNT:.2f} (open)"
        elif won:
            result_s = "WIN"
            net_s    = f"+${net:.4f}"
        else:
            result_s = "LOSS"
            net_s    = f"-${TRADE_AMOUNT:.2f}"

        lines.append(
            f"  {i:<4} {slug[:37]:<38} {side[:5]:<6} ${price:<7.4f} "
            f"{outcome[:13]:<14} {result_s:<8} {net_s:<12} {url}"
        )

    lines.append("=" * 100)
    return "\n".join(lines)


def print_summary(state: dict):
    print("\n" + build_summary(state) + "\n")


def save_summary(state: dict):
    txt = build_summary(state)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(txt)
    logging.getLogger("polymarket_bot").info(f"Summary saved -> {SUMMARY_FILE}")