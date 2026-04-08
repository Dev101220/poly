# ── Polymarket Endpoints ───────────────────────────────────────────────────
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST  = "https://clob.polymarket.com"
CLOB_WS    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── Market Config ──────────────────────────────────────────────────────────
COIN               = "BTC"
MARKET_WINDOW_SEC  = 300      # 5 minutes (use 900 for 15-min)
MARKET_CHECK_SEC   = 10       # how often to poll Gamma for market rollover

# ── Common Trading Parameters ──────────────────────────────────────────────
STARTING_BANKROLL = 1000.00   # USD (paper money)
TRADE_AMOUNT      = 1.0      # USD per trade

# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY CONFIGS
#  Run with:  python bot.py --strat 1   (or 2, 3, 4, 5)
# ══════════════════════════════════════════════════════════════════════════════

STRATEGIES = {

    1: {
        "name"             : "Penny Hunter",
        "description"      : "Buy $1 on any side at exactly $0.95. SL at -70%.",
        "trigger_price"    : 0.96,   # exact entry price
        "buy_window_start" : None,
        "buy_window_end"   : None,
        "take_profit_pct"  : None,   # hold to resolution
        "sell_after_sec"   : None,
        "stop_loss_pct"    : -0.40,  # exit at -70%
    },

    2: {
        "name"        : "Early Bird",
        "description" : (
            "Buy $1 on any side < $0.10 but ONLY in the first 2 minutes "
            "of the 5-min window. After 2 minutes, ignore all signals."
        ),
        # --- trigger ---
        "trigger_price"      : 0.05,   # wider than strat-1 (below 10 cents)
        # --- time gate ---
        "buy_window_start"   : 240,     # open from second 0
        "buy_window_end"     : 300,    # close at 2-minute mark (120 s into window)
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
        "buy_window_start"   : 13,     # 1 minute elapsed
        "buy_window_end"     : 600,    # 4 minutes remaining = 60 s elapsed in 5-min window
        # --- sell: exit when position gains 10-20% ---
        "take_profit_pct"    : 0.08,   # take profit at +10% of entry price
        "sell_after_sec"     : None,   # no forced time-exit (hold to resolution if TP not hit)
    },

    4: {
        "name"        : "Dusk Sniper",
        "description" : (
            "Buy at exactly $0.93. Hold to resolution. "
            "SL-A: if token ask drops below $0.60 in the last 20s, exit. "
            "SL-B: if token ask drops below $0.50 at ANY time, exit immediately."
        ),
        # --- entry ---
        "trigger_price"       : 0.90,   # exact ask price required for entry

        # --- stop-loss thresholds ---
        "sl_late_price"       : 0.60,   # SL-A: only active in last 20 s
        "sl_late_window_sec"  : 20,     # seconds before expiry to arm SL-A
        "sl_hard_price"       : 0.50,   # SL-B: always active, immediate exit

        # --- unused by this strat ---
        "buy_window_start"    : None,
        "buy_window_end"      : None,
        "take_profit_pct"     : None,
        "sell_after_sec"      : None,
    },

    5: {
        "name"        : "Last Minute Lurker",
        "description" : (
            "ETH 5-min market only. Enter ONLY in the last 70 seconds of the window. "
            "Buy any side whose ask is between $0.75 and $0.90 (inclusive). "
            "One trade per window. Hold to resolution. "
            "SL: if position drops -17% from entry price at any time after entry, exit immediately."
        ),
        # --- coin override (ETH instead of BTC) ---
        "coin"               : "BTC",

        # --- time gate: only enter when <= 70 s remain ---
        "entry_window_sec"   :60,       # arm entry when remaining <= this value

        # --- price band ---
        "trigger_price_low"  : 0.85,     # minimum ask to consider entry
        "trigger_price_high" : 0.90,     # maximum ask to consider entry

        # --- stop-loss ---
        "stop_loss_pct"      : -0.17,    # exit immediately at -17% from entry

        # --- unused fields (kept for schema consistency) ---
        "trigger_price"      : None,
        "buy_window_start"   : None,
        "buy_window_end"     : None,
        "take_profit_pct"    : None,
        "sell_after_sec"     : None,
        "sl_late_price"      : None,
        "sl_late_window_sec" : None,
        "sl_hard_price"      : None,
    },

}

# ── Log files ──────────────────────────────────────────────────────────────
TRADES_LOG   = "trades_log.txt"
SUMMARY_FILE = "summary.txt"
DEBUG_LOG    = "bot_debug.log"
