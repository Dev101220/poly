# ── Polymarket Endpoints ───────────────────────────────────────────────────
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST  = "https://clob.polymarket.com"
CLOB_WS    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── Market Config ──────────────────────────────────────────────────────────
COIN               = "BTC"
MARKET_WINDOW_SEC  = 300      # 5 minutes (use 900 for 15-min)
MARKET_CHECK_SEC   = 30       # how often to poll Gamma for market rollover

# ── Common Trading Parameters ──────────────────────────────────────────────
STARTING_BANKROLL = 1000.00   # USD (paper money)
TRADE_AMOUNT      = 3.00      # USD per trade

# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY CONFIGS
#  Run with:  python bot.py --strat 1   (or 2 or 3)
# ══════════════════════════════════════════════════════════════════════════════

STRATEGIES = {

    1: {
        "name"        : "Penny Hunter",
        "description" : "Buy $1 on any side <= $0.05 anytime during the market window.",
        # --- trigger ---
        "trigger_price"      : 0.05,   # buy when best_ask <= this
        # --- time gate (None = no gate) ---
        "buy_window_start"   : None,   # seconds from market open  (None = no restriction)
        "buy_window_end"     : None,   # seconds from market open  (None = no restriction)
        # --- sell / take-profit (Strategy 3 only) ---
        "take_profit_pct"    : None,   # None = hold to resolution
        "sell_after_sec"     : None,
    },

    2: {
        "name"        : "Early Bird",
        "description" : (
            "Buy $1 on any side < $0.10 but ONLY in the first 2 minutes "
            "of the 5-min window. After 2 minutes, ignore all signals."
        ),
        # --- trigger ---
        "trigger_price"      : 0.10,   # wider than strat-1 (below 10 cents)
        # --- time gate ---
        "buy_window_start"   : 0,      # open from second 0
        "buy_window_end"     : 120,    # close at 2-minute mark (120 s into window)
        # --- sell ---
        "take_profit_pct"    : None,
        "sell_after_sec"     : None,
    },

    3: {
        "name"        : "Late Entry + Quick Exit",
        "description" : (
            "Wait 1 minute (60 s), then buy the cheaper side if either is < $0.50. "
            "After buying, watch for a 10-20% price increase on the position "
            "and sell (log exit) if that threshold is reached before expiry."
        ),
        # --- trigger ---
        "trigger_price"      : 0.50,   # buy whichever side is lower (< 0.50 = below fair)
        # --- time gate: enter only between 60 s and 240 s (minute 1 → minute 4) ---
        "buy_window_start"   : 60,     # 1 minute elapsed
        "buy_window_end"     : 240,    # 4 minutes remaining = 60 s elapsed in 5-min window
        # --- sell: exit when position gains 10-20% ---
        "take_profit_pct"    : 0.1,   # take profit at +10% of entry price
        "sell_after_sec"     : None,   # no forced time-exit (hold to resolution if TP not hit)
    },

}

# ── Log files ──────────────────────────────────────────────────────────────
TRADES_LOG   = "trades_log.txt"
SUMMARY_FILE = "summary.txt"
DEBUG_LOG    = "bot_debug.log"
