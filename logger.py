"""
Logger & Summary Module
========================
Handles:
 - Structured logging to console + trades_log.txt
 - Trade row appended to log file on every order / skip / resolution
 - Summary table printed to console and saved to summary.txt
"""

import logging
import os
from datetime import datetime, timezone
from config import LOG_FILE, SUMMARY_FILE, STARTING_BANKROLL, TRADE_AMOUNT, TRIGGER_PRICE


# ── Setup ──────────────────────────────────────────────────────────────────────

def setup_logger():
    logger = logging.getLogger("polymarket_bot")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler (debug level — every WS message)
    fh = logging.FileHandler("bot_debug.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ── Trade Log File ─────────────────────────────────────────────────────────────

_HEADER_WRITTEN = False

def _ensure_header():
    global _HEADER_WRITTEN
    if _HEADER_WRITTEN:
        return
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("=" * 130 + "\n")
            f.write("  POLYMARKET BTC 5-MIN PAPER TRADING BOT — TRADE LOG\n")
            f.write(f"  Started  : {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"  Strategy : Buy ${TRADE_AMOUNT} when any side <= ${TRIGGER_PRICE}\n")
            f.write(f"  Bankroll : ${STARTING_BANKROLL:.2f} (paper)\n")
            f.write("=" * 130 + "\n\n")
            cols = (
                f"{'#':<5} {'Time (UTC)':<22} {'Slug':<45} {'Side':<8} "
                f"{'Price':<8} {'Size':<8} {'Payout':<10} {'Outcome':<10} "
                f"{'Result':<8} {'Net $':<10} {'Status':<16} {'Market Link'}"
            )
            f.write(cols + "\n")
            f.write("-" * 130 + "\n")
    _HEADER_WRITTEN = True


_trade_counter = [0]

def log_trade(state, slug, side, price, market_info, order_result,
              outcome=None, won=None, net=None, skipped=False, skip_reason=""):
    """Append one row to trades_log.txt."""
    _ensure_header()
    _trade_counter[0] += 1
    n = _trade_counter[0]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    poly_url = market_info.get("polymarket_url", f"https://polymarket.com/event/{slug}")
    size = order_result.get("size_shares", round(TRADE_AMOUNT / price, 2)) if order_result else "—"
    payout = order_result.get("potential_payout", "—") if order_result else "—"
    status = order_result.get("status", "PAPER_ONLY") if order_result else "SKIPPED"

    if skipped:
        result_str = "SKIP"
        outcome_str = "—"
        net_str = "—"
    elif won is None:
        result_str = "PENDING"
        outcome_str = "PENDING"
        net_str = f"-${TRADE_AMOUNT:.2f}"
    elif won:
        result_str = "WIN"
        outcome_str = outcome or "—"
        net_str = f"+${net:.4f}"
    else:
        result_str = "LOSS"
        outcome_str = outcome or "—"
        net_str = f"-${TRADE_AMOUNT:.2f}"

    skip_note = f" [{skip_reason}]" if skipped and skip_reason else ""

    row = (
        f"{n:<5} {ts:<22} {slug[:44]:<45} {side[:7]:<8} "
        f"${price:<7.4f} {str(size):<8} ${str(payout):<9} {outcome_str:<10} "
        f"{result_str:<8} {net_str:<10} {status:<16} {poly_url}{skip_note}"
    )

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(row + "\n")


def log_skipped(state, market_info, reason, price=None):
    """Log a skipped market event."""
    slug = market_info.get("slug", "unknown")
    side = market_info.get("outcome", "—")
    p = price if price else 0.0
    log_trade(state, slug, side, p, market_info, None, skipped=True, skip_reason=reason)


# ── Summary ────────────────────────────────────────────────────────────────────

def _build_summary(state) -> str:
    trades = state["traded_markets"]
    bankroll = state["bankroll"]

    total = len(trades)
    resolved = [t for t in trades.values() if t.get("won") is not None]
    pending  = [t for t in trades.values() if t.get("won") is None]
    won_list = [t for t in resolved if t["won"]]
    lost_list = [t for t in resolved if not t["won"]]

    pnl        = bankroll - STARTING_BANKROLL
    win_rate   = (len(won_list) / len(resolved) * 100) if resolved else 0
    avg_win    = (sum(t["net"] for t in won_list)  / len(won_list))  if won_list  else 0
    avg_loss   = (sum(t["net"] for t in lost_list) / len(lost_list)) if lost_list else 0
    total_net  = sum(t["net"] for t in resolved if t["net"] is not None)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append("=" * 90)
    lines.append("  POLYMARKET BTC 5-MIN BOT — LIVE SUMMARY")
    lines.append(f"  Generated : {now}")
    lines.append("=" * 90)
    lines.append(f"  Starting Bankroll : ${STARTING_BANKROLL:.2f}")
    lines.append(f"  Current Bankroll  : ${bankroll:.2f}")
    lines.append(f"  Total P&L         : {'+'if pnl>=0 else ''}{pnl:.4f}$")
    lines.append(f"  Total Trades      : {total}")
    lines.append(f"  Resolved          : {len(resolved)}")
    lines.append(f"  Pending           : {len(pending)}")
    lines.append(f"  Won               : {len(won_list)}")
    lines.append(f"  Lost              : {len(lost_list)}")
    lines.append(f"  Win Rate          : {win_rate:.1f}%")
    lines.append(f"  Avg Win Net       : +${avg_win:.4f}")
    lines.append(f"  Avg Loss Net      : ${avg_loss:.4f}")
    lines.append(f"  Total Net (resolved): {'+'if total_net>=0 else ''}{total_net:.4f}$")
    lines.append("")
    lines.append(f"  {'#':<4} {'Slug':<42} {'Side':<8} {'Price':<8} {'Outcome':<12} {'Result':<8} {'Net':<10} {'Link'}")
    lines.append("  " + "-" * 130)

    for i, (slug, t) in enumerate(trades.items(), 1):
        side     = t.get("side", "—")
        price    = t.get("price", 0)
        outcome  = t.get("outcome") or "PENDING"
        won      = t.get("won")
        net      = t.get("net", -TRADE_AMOUNT)
        url      = t.get("market_info", {}).get("polymarket_url", f"https://polymarket.com/event/{slug}")

        if won is None:
            result = "PENDING"
            net_s  = f"-${TRADE_AMOUNT:.2f} (open)"
        elif won:
            result = "WIN"
            net_s  = f"+${net:.4f}"
        else:
            result = "LOSS"
            net_s  = f"-${TRADE_AMOUNT:.2f}"

        lines.append(f"  {i:<4} {slug[:41]:<42} {side[:7]:<8} ${price:<7.4f} {outcome:<12} {result:<8} {net_s:<10} {url}")

    lines.append("=" * 90)
    return "\n".join(lines)


def print_summary(state):
    print("\n" + _build_summary(state) + "\n")


def save_summary(state):
    summary = _build_summary(state)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(summary)
    logging.getLogger("polymarket_bot").info(f"Summary saved to {SUMMARY_FILE}")
