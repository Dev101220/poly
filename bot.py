"""
Polymarket BTC 5-Min Paper Trading Bot  —  Multi-Strategy Edition
=================================================================
Usage:
    python bot.py --strat 1       # Penny Hunter  (any time, <= $0.05)
    python bot.py --strat 2       # Early Bird    (first 2 min only, < $0.10)
    python bot.py --strat 3       # Late Entry    (min 1-4, TP + timed SL check)

Strategy descriptions:
  1 - Buy $1 on any side <= $0.05, anytime, hold to resolution.
  2 - Buy $1 on any side < $0.10, but ONLY in the first 2 minutes.
      After the 2-minute mark, all signals are ignored for that window.
  3 - Wait 1 minute into window, buy the lower-priced side.
      Monitor position live; take profit on gain threshold.
      Stop-loss exits whenever the open trade reaches -60% PnL.
      One trade per market window.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

import requests

from config import (
    COIN, STARTING_BANKROLL, TRADE_AMOUNT,
    MARKET_WINDOW_SEC, MARKET_CHECK_SEC,
    GAMMA_HOST,
    TRADES_LOG, SUMMARY_FILE, DEBUG_LOG,
    STRATEGIES,
)
from gamma_client import GammaClient
from websocket_client import MarketWebSocket, OrderbookSnapshot
from order_engine import create_order
from trade_log import setup_logger, log_trade_event, print_summary, save_summary


# ─────────────────────────────────────────────────────────────────────────────
#  Time helpers
# ─────────────────────────────────────────────────────────────────────────────

def seconds_into_window(end_date_str: str) -> float:
    """Seconds elapsed since window opened. Uses end_date - window_sec."""
    try:
        eds = end_date_str.replace("Z", "+00:00")
        end_time = datetime.fromisoformat(eds)
        now = datetime.now(timezone.utc)
        elapsed = MARKET_WINDOW_SEC - (end_time - now).total_seconds()
        return max(0.0, elapsed)
    except Exception:
        return 0.0


def seconds_remaining(end_date_str: str) -> float:
    """Seconds left until market window closes."""
    try:
        eds = end_date_str.replace("Z", "+00:00")
        end_time = datetime.fromisoformat(eds)
        now = datetime.now(timezone.utc)
        return max(0.0, (end_time - now).total_seconds())
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  Global state
# ─────────────────────────────────────────────────────────────────────────────

state = {
    "bankroll"       : STARTING_BANKROLL,
    "traded_markets" : {},    # slug -> trade record
    "current_market" : None,
    "ws"             : None,
    "running"        : True,
    "strategy"       : None,
    "strat_cfg"      : None,
    # Strategy-3 position tracker
    # slug -> {
    #   entry_price, token_id, side, sold, sell_price
    # }
    "open_positions" : {},
}

logger = logging.getLogger("polymarket_bot")
gamma  = GammaClient(host=GAMMA_HOST)


# ─────────────────────────────────────────────────────────────────────────────
#  Market discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover_market():
    info = gamma.get_full_market_info(COIN, window_sec=MARKET_WINDOW_SEC)
    if not info:
        logger.warning(f"No active {COIN} {MARKET_WINDOW_SEC//60}-min market found")
        return None
    if not info.get("accepting_orders"):
        logger.warning(f"Market {info['slug']} not accepting orders")
        return None
    return info


# ─────────────────────────────────────────────────────────────────────────────
#  Order helpers
# ─────────────────────────────────────────────────────────────────────────────

def fire_order(side, price, token_id, market_info, note=""):
    """Paper buy. Updates bankroll + state. Writes log row. Returns record."""
    slug  = market_info["slug"]
    order = create_order(
        token_id=token_id, side=side, price=price,
        amount=TRADE_AMOUNT, market_info=market_info, dry_run=True,
    )
    state["bankroll"] -= TRADE_AMOUNT
    record = {
        "side"        : side.upper(),
        "price"       : price,
        "token_id"    : token_id,
        "timestamp"   : datetime.now(timezone.utc).isoformat(),
        "outcome"     : None,
        "won"         : None,
        "payout"      : None,
        "net"         : -TRADE_AMOUNT,
        "sold_early"  : False,
        "sell_price"  : None,
        "sl_hit"      : False,
        "market_info" : market_info,
        "order"       : order,
    }
    state["traded_markets"][slug] = record
    log_trade_event(
        slug=slug, side=side.upper(), price=price,
        market_info=market_info, order=order, note=note,
        sl_hit=False,
    )
    logger.info(
        f"[BUY] {slug} | {side.upper()} @ ${price:.4f} | "
        f"Shares:{order['size_shares']:.4f} | Payout if win:${order['potential_payout']:.4f} | "
        f"Bankroll:${state['bankroll']:.2f}"
        + (f"  [{note}]" if note else "")
    )
    return record



def log_skip(slug, side, price, market_info, reason):
    logger.info(f"[SKIP] {slug} | {side.upper()} @ ${price:.4f} | {reason}")
    log_trade_event(
        slug=slug, side=side.upper(), price=price,
        market_info=market_info, order=None,
        skipped=True, skip_reason=reason,
        sl_hit=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 1  —  Penny Hunter
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 1  —  Penny Hunter
# ─────────────────────────────────────────────────────────────────────────────

def strat1_check(side, best_ask, token_id, market_info):
    """
    Buy $1 on any side at EXACTLY $0.95.
    Monitor position live; stop-loss exits if trade reaches -70% PnL.
    One trade per market window.
    """
    cfg  = state["strat_cfg"]
    slug = market_info["slug"]

    # ── Monitor open position for SL ─────────────────────────────────────────
    pos = state["open_positions"].get(slug)
    if pos and not pos["sold"] and pos["token_id"] == token_id:
        entry  = pos["entry_price"]
        if entry > 0:
            shares          = TRADE_AMOUNT / entry
            gain            = (best_ask - entry) / entry
            unrealized_pnl  = (best_ask - entry) * shares

            # Stop-loss: exit immediately if trade reaches -70% or worse
            if gain <= -0.70:
                pos["sold"]       = True
                pos["sell_price"] = best_ask

                state["bankroll"] += TRADE_AMOUNT + unrealized_pnl

                trade = state["traded_markets"].get(slug)
                if trade:
                    trade["sold_early"] = True
                    trade["sell_price"] = best_ask
                    trade["net"]        = unrealized_pnl
                    trade["won"]        = False
                    trade["outcome"]    = f"SL @ ${best_ask:.4f} ({gain*100:.1f}%)"
                    trade["sl_hit"]     = True

                logger.info(
                    f"[SELL/SL] {slug} | {pos['side'].upper()} | "
                    f"Entry ${entry:.4f} -> Sell ${best_ask:.4f} | "
                    f"Move {gain*100:.1f}% | PnL ${unrealized_pnl:+.4f} | "
                    f"Bankroll ${state['bankroll']:.2f}"
                )
                log_trade_event(
                    slug=slug,
                    side=pos["side"],
                    price=best_ask,
                    market_info=market_info,
                    order=trade["order"] if trade else None,
                    outcome=f"SL {gain*100:.1f}%",
                    won=False,
                    net=unrealized_pnl,
                    note=(
                        f"SL exit on -70% threshold | "
                        f"entry ${entry:.4f} | move {gain*100:.1f}%"
                    ),
                    sl_hit=True,
                )
        return

    # ── Look for new ENTRY ────────────────────────────────────────────────────
    # Skip if already traded this market
    existing = state["traded_markets"].get(slug)
    if existing:
        if existing["side"] != side.upper():
            log_skip(slug, side, best_ask, market_info,
                     f"Already bought {existing['side']} — one trade per market")
        return

    # Enter ONLY at exactly $0.95 (rounded to 2dp to handle float precision)
    if round(best_ask, 2) != cfg["trigger_price"]:
        return

    fire_order(side, best_ask, token_id, market_info,
               note="Penny Hunter trigger @ exactly $0.95")

    state["open_positions"][slug] = {
        "side"        : side,
        "token_id"    : token_id,
        "entry_price" : best_ask,
        "sold"        : False,
        "sell_price"  : None,
    }
# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 2  —  Early Bird
# ─────────────────────────────────────────────────────────────────────────────

def strat2_check(side, best_ask, token_id, market_info):
    """Buy < $0.10 ONLY in first 2 minutes of window."""
    cfg      = state["strat_cfg"]
    slug     = market_info["slug"]
    elapsed  = seconds_into_window(market_info.get("end_date", ""))

    if best_ask > cfg["trigger_price"]:
        return

    existing = state["traded_markets"].get(slug)
    if existing:
        if existing["side"] != side.upper():
            log_skip(slug, side, best_ask, market_info,
                     f"Already bought {existing['side']} — one trade per market")
        return

    gate_end = cfg["buy_window_end"]
    if elapsed > gate_end:
        log_skip(slug, side, best_ask, market_info,
                 f"Early Bird window closed ({elapsed:.0f}s into window, gate is 0-{gate_end}s)")
        return

    fire_order(side, best_ask, token_id, market_info,
               note=f"Early Bird @ {elapsed:.0f}s into window")


# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY 3  —  Late Entry + Quick Exit + -60% SL
# ─────────────────────────────────────────────────────────────────────────────

def strat3_check(side, best_ask, token_id, market_info):
    """
    Entry:
      - wait 60s, then buy cheaper side (< $0.50) between 60-240s.

    Exit:
      - TP: if position price rises >= take_profit_pct from entry -> sell.
      - SL: if open position reaches -60% or worse from entry -> sell immediately.
    """
    cfg        = state["strat_cfg"]
    slug       = market_info["slug"]
    elapsed    = seconds_into_window(market_info.get("end_date", ""))
    remaining  = seconds_remaining(market_info.get("end_date", ""))
    tp_pct     = cfg["take_profit_pct"]

    pos = state["open_positions"].get(slug)
    if pos and not pos["sold"] and pos["token_id"] == token_id:
        entry = pos["entry_price"]
        if entry > 0:
            shares = TRADE_AMOUNT / entry
            gain = (best_ask - entry) / entry
            unrealized_pnl = (best_ask - entry) * shares
            unrealized_loss = max(0.0, -unrealized_pnl)

            # 1) Take profit can happen anytime before expiry.
            if gain >= tp_pct:
                pos["sold"] = True
                pos["sell_price"] = best_ask

                state["bankroll"] += TRADE_AMOUNT + unrealized_pnl

                trade = state["traded_markets"].get(slug)
                if trade:
                    trade["sold_early"] = True
                    trade["sell_price"] = best_ask
                    trade["net"] = unrealized_pnl
                    trade["won"] = True
                    trade["outcome"] = f"TP @ ${best_ask:.4f} (+{gain*100:.1f}%)"
                    trade["sl_hit"] = False

                logger.info(
                    f"[SELL/TP] {slug} | {pos['side'].upper()} | "
                    f"Entry ${entry:.4f} -> Sell ${best_ask:.4f} | "
                    f"Gain +{gain*100:.1f}% | PnL +${unrealized_pnl:.4f} | "
                    f"Bankroll ${state['bankroll']:.2f}"
                )
                log_trade_event(
                    slug=slug,
                    side=pos["side"],
                    price=best_ask,
                    market_info=market_info,
                    order=trade["order"] if trade else None,
                    outcome=f"TP +{gain*100:.1f}%",
                    won=True,
                    net=unrealized_pnl,
                    note=f"TP exit @ {elapsed:.0f}s | entry ${entry:.4f}",
                    sl_hit=False,
                )
                return

            # 2) Stop-loss: exit immediately if trade reaches -60% or worse.
            if gain <= -0.65:
                pos["sold"] = True
                pos["sell_price"] = best_ask

                state["bankroll"] += TRADE_AMOUNT + unrealized_pnl

                trade = state["traded_markets"].get(slug)
                if trade:
                    trade["sold_early"] = True
                    trade["sell_price"] = best_ask
                    trade["net"] = unrealized_pnl
                    trade["won"] = False
                    trade["outcome"] = f"SL @ ${best_ask:.4f} ({gain*100:.1f}%)"
                    trade["sl_hit"] = True

                logger.info(
                    f"[SELL/SL] {slug} | {pos['side'].upper()} | "
                    f"Entry ${entry:.4f} -> Sell ${best_ask:.4f} | "
                    f"Move {gain*100:.1f}% | PnL ${unrealized_pnl:+.4f} | "
                    f"Bankroll ${state['bankroll']:.2f}"
                )
                log_trade_event(
                    slug=slug,
                    side=pos["side"],
                    price=best_ask,
                    market_info=market_info,
                    order=trade["order"] if trade else None,
                    outcome=f"SL {gain*100:.1f}%",
                    won=False,
                    net=unrealized_pnl,
                    note=(
                        f"SL exit on -60% threshold | "
                        f"entry ${entry:.4f} | move {gain*100:.1f}%"
                    ),
                    sl_hit=True,
                )
                return
        return

    # ── Look for new ENTRY ────────────────────────────────────────────────────
    if state["traded_markets"].get(slug):
        return

    win_start = cfg["buy_window_start"]
    win_end   = cfg["buy_window_end"]

    if elapsed < win_start:
        return

    if elapsed > win_end:
        log_skip(slug, side, best_ask, market_info,
                 f"Late Entry window closed ({elapsed:.0f}s, gate={win_start}-{win_end}s)")
        return

    if best_ask >= cfg["trigger_price"]:
        return

    fire_order(side, best_ask, token_id, market_info,
               note=f"Late Entry @ {elapsed:.0f}s | TP +{tp_pct*100:.0f}% | SL @ -60%")

    state["open_positions"][slug] = {
        "side"          : side,
        "token_id"      : token_id,
        "entry_price"   : best_ask,
        "sold"          : False,
        "sell_price"    : None,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Strategy dispatcher
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_FN = {1: strat1_check, 2: strat2_check, 3: strat3_check}


# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket handler
# ─────────────────────────────────────────────────────────────────────────────

async def on_book_update(snapshot: OrderbookSnapshot):
    market_info = state.get("current_market")
    if not market_info:
        return

    asset = snapshot.asset_id
    side  = None
    for s, tid in market_info.get("token_ids", {}).items():
        if tid == asset:
            side = s
            break
    if not side:
        return

    best_ask = snapshot.best_ask
    logger.debug(
        f"[BOOK] {market_info['slug']} | {side.upper()} "
        f"bid={snapshot.best_bid:.4f} ask={best_ask:.4f}"
    )
    STRATEGY_FN[state["strategy"]](side, best_ask, asset, market_info)


# ─────────────────────────────────────────────────────────────────────────────
#  Resolution checker
# ─────────────────────────────────────────────────────────────────────────────

def check_resolution_sync(slug):
    try:
        r = requests.get(f"{GAMMA_HOST}/markets/slug/{slug}", timeout=8)
        if r.status_code != 200:
            return None
        m = r.json()
        if not (m.get("closed") or m.get("resolved") or not m.get("acceptingOrders")):
            return None
        raw_prices   = m.get("outcomePrices", "[]")
        raw_outcomes = m.get("outcomes", '["Up","Down"]')
        prices   = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
        for i, price in enumerate(prices):
            if float(price) >= 0.99:
                return outcomes[i] if i < len(outcomes) else "Unknown"
        return None
    except Exception as e:
        logger.debug(f"Resolution check error {slug}: {e}")
        return None


async def resolution_loop():
    while state["running"]:
        await asyncio.sleep(60)
        for slug, trade in list(state["traded_markets"].items()):
            if trade.get("won") is not None:
                continue
            if trade.get("sold_early"):
                continue

            resolution = await asyncio.to_thread(check_resolution_sync, slug)
            if not resolution:
                continue

            bought_side = trade["side"].lower()
            resolved    = resolution.lower()
            if resolved == "yes":
                resolved = "up"
            elif resolved == "no":
                resolved = "down"

            won    = resolved == bought_side
            payout = (TRADE_AMOUNT / trade["price"]) if won else 0.0
            net    = payout - TRADE_AMOUNT

            trade.update(outcome=resolution, won=won, payout=payout, net=net, sl_hit=False)
            if won:
                state["bankroll"] += payout

            logger.info(
                f"[RESOLVED] {slug} | Bought {bought_side.upper()} | "
                f"Outcome: {resolution} | {'WIN' if won else 'LOSS'} | "
                f"Net: {'+' if net >= 0 else ''}{net:.4f}$ | "
                f"Bankroll: ${state['bankroll']:.2f}"
            )
            log_trade_event(
                slug=slug, side=bought_side, price=trade["price"],
                market_info=trade["market_info"], order=trade["order"],
                outcome=resolution, won=won, net=net, sl_hit=False,
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Market rollover
# ─────────────────────────────────────────────────────────────────────────────

async def market_check_loop(ws: MarketWebSocket):
    while state["running"]:
        await asyncio.sleep(MARKET_CHECK_SEC)
        new_info = await asyncio.to_thread(discover_market)
        if not new_info:
            continue
        old_info = state.get("current_market")
        if old_info and old_info["slug"] == new_info["slug"]:
            continue
        old_slug = old_info["slug"] if old_info else "none"
        logger.info(f"[ROLLOVER] {old_slug} -> {new_info['slug']}")
        state["current_market"] = new_info
        new_tids = list(new_info["token_ids"].values())
        await ws.subscribe(new_tids, replace=True)
        logger.info(f"Re-subscribed to tokens: {new_tids}")


# ─────────────────────────────────────────────────────────────────────────────
#  Stats printer
# ─────────────────────────────────────────────────────────────────────────────

async def stats_loop():
    while state["running"]:
        await asyncio.sleep(120)
        trades   = state["traded_markets"]
        resolved = [t for t in trades.values() if t.get("won") is not None]
        wins     = [t for t in resolved if t["won"]]
        early    = [t for t in trades.values() if t.get("sold_early")]
        sl_hits  = [t for t in trades.values() if t.get("sl_hit")]
        logger.info(
            f"[STATS] S{state['strategy']} | "
            f"Bankroll:${state['bankroll']:.2f} | "
            f"Trades:{len(trades)} | Resolved:{len(resolved)} | "
            f"Wins:{len(wins)} | Losses:{len(resolved)-len(wins)} | "
            f"Early exits:{len(early)} | SL hits:{len(sl_hits)} | "
            f"P&L:{state['bankroll']-STARTING_BANKROLL:+.4f}$"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Shutdown
# ─────────────────────────────────────────────────────────────────────────────

def shutdown(signum, frame):
    logger.info("\nShutdown signal. Saving summary…")
    state["running"] = False
    save_summary(state)
    print_summary(state)
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Polymarket BTC 5-Min Paper Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            f"  --strat {n}  {cfg['name']}: {cfg['description']}"
            for n, cfg in STRATEGIES.items()
        ),
    )
    parser.add_argument(
        "--strat", type=int, choices=[1, 2, 3], default=1,
        metavar="N",
        help="Strategy number (1, 2, or 3). See --help for details.",
    )
    args = parser.parse_args()

    strat_num = args.strat
    strat_cfg = STRATEGIES[strat_num]
    state["strategy"]  = strat_num
    state["strat_cfg"] = strat_cfg

    setup_logger(DEBUG_LOG)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("=" * 65)
    logger.info(f"  Polymarket {COIN} {MARKET_WINDOW_SEC//60}-Min Paper Bot")
    logger.info(f"  Strategy {strat_num}: {strat_cfg['name']}")
    logger.info(f"  {strat_cfg['description']}")
    logger.info(f"  Bankroll : ${STARTING_BANKROLL:.2f} (paper money)")
    logger.info(f"  Logs     : {TRADES_LOG}  {SUMMARY_FILE}")
    logger.info("=" * 65)

    logger.info(f"Fetching current {COIN} {MARKET_WINDOW_SEC//60}-min market…")
    market_info = discover_market()

    if not market_info:
        for attempt in range(6):
            logger.warning(f"No market found — retry {attempt+1}/6 in 30s…")
            await asyncio.sleep(30)
            market_info = discover_market()
            if market_info:
                break

        if not market_info:
            logger.error(
                "No active market after retries.\n"
                "  TIP: Change MARKET_WINDOW_SEC = 900 in config.py to use 15-min markets."
            )
            sys.exit(1)

    state["current_market"] = market_info
    token_ids = list(market_info["token_ids"].values())
    elapsed   = seconds_into_window(market_info.get("end_date", ""))
    remaining = seconds_remaining(market_info.get("end_date", ""))

    logger.info(f"Market  : {market_info['slug']}")
    logger.info(f"Question: {market_info['question']}")
    logger.info(f"Ends at : {market_info['end_date']}")
    logger.info(f"Window  : {elapsed:.0f}s elapsed / {remaining:.0f}s remaining")
    logger.info(
        f"Prices  : UP=${market_info['prices'].get('up', 0):.4f}  "
        f"DOWN=${market_info['prices'].get('down', 0):.4f}"
    )

    ws = MarketWebSocket()
    state["ws"] = ws
    ws.on_book(on_book_update)

    @ws.on_connect
    def _conn():
        logger.info("[WS] Connected to CLOB WebSocket")

    @ws.on_disconnect
    def _disc():
        logger.warning("[WS] Disconnected — auto-reconnecting…")

    await ws.subscribe(token_ids)

    await asyncio.gather(
        ws.run(auto_reconnect=True),
        market_check_loop(ws),
        resolution_loop(),
        stats_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
