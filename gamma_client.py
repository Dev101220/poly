"""
Gamma API Client
================
Directly adapted from discountry/polymarket-trading-bot with 5-min support added.

Polymarket 15-min slug pattern : {coin}-updown-15m-{unix_ts_rounded_to_900}
Polymarket 5-min slug pattern  : {coin}-updown-5m-{unix_ts_rounded_to_300}

API endpoint: GET https://gamma-api.polymarket.com/markets/slug/{slug}
Response field  clobTokenIds : JSON array ["<up_token_id>", "<down_token_id>"]
Response field  outcomes     : JSON array ["Up", "Down"]
Response field  outcomePrices: JSON array ["0.52", "0.48"]
Response field  acceptingOrders: bool
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

logger = logging.getLogger("polymarket_bot")


class GammaClient:

    COIN_SLUGS_15M = {
        "BTC": "btc-updown-15m",
        "ETH": "eth-updown-15m",
        "SOL": "sol-updown-15m",
        "XRP": "xrp-updown-15m",
    }

    # 5-min markets — same pattern, 300-second windows
    COIN_SLUGS_5M = {
        "BTC": "btc-updown-5m",
        "ETH": "eth-updown-5m",
        "SOL": "sol-updown-5m",
        "XRP": "xrp-updown-5m",
    }

    def __init__(self, host: str = "https://gamma-api.polymarket.com", timeout: int = 10):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polymarket-paper-bot/1.0"})

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        url = f"{self.host}/markets/slug/{slug}"
        try:
            r = self.session.get(url, timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            logger.debug(f"Slug {slug} -> HTTP {r.status_code}")
            return None
        except Exception as e:
            logger.debug(f"Slug {slug} -> error: {e}")
            return None

    def get_current_market(self, coin: str, window_sec: int = 900) -> Optional[Dict[str, Any]]:
        """
        Fetch the current active market for a given window size (300=5min, 900=15min).
        Tries current window, then next, then previous.
        """
        coin = coin.upper()
        slugs = self.COIN_SLUGS_5M if window_sec == 300 else self.COIN_SLUGS_15M
        if coin not in slugs:
            raise ValueError(f"Unknown coin {coin}")

        prefix = slugs[coin]
        now = int(datetime.now(timezone.utc).timestamp())
        current_ts = (now // window_sec) * window_sec

        for delta in [0, window_sec, -window_sec, window_sec * 2, -window_sec * 2]:
            slug = f"{prefix}-{current_ts + delta}"
            market = self.get_market_by_slug(slug)
            if market and market.get("acceptingOrders"):
                logger.info(f"Found active market: {slug}")
                return market

        return None

    def parse_token_ids(self, market: Dict[str, Any]) -> Dict[str, str]:
        """Returns {"up": token_id, "down": token_id}"""
        raw_ids = market.get("clobTokenIds", "[]")
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids

        raw_outcomes = market.get("outcomes", '["Up","Down"]')
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes

        result = {}
        for i, outcome in enumerate(outcomes):
            if i < len(ids):
                result[outcome.lower()] = ids[i]
        return result

    def parse_prices(self, market: Dict[str, Any]) -> Dict[str, float]:
        """Returns {"up": 0.52, "down": 0.48}"""
        raw_prices = market.get("outcomePrices", '["0.5","0.5"]')
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices

        raw_outcomes = market.get("outcomes", '["Up","Down"]')
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes

        result = {}
        for i, outcome in enumerate(outcomes):
            if i < len(prices):
                result[outcome.lower()] = float(prices[i])
        return result

    def get_full_market_info(self, coin: str, window_sec: int = 900) -> Optional[Dict[str, Any]]:
        """
        High-level call: returns dict ready for the bot to use.
        """
        market = self.get_current_market(coin, window_sec)
        if not market:
            return None

        token_ids = self.parse_token_ids(market)
        prices    = self.parse_prices(market)

        return {
            "slug"            : market.get("slug", ""),
            "question"        : market.get("question", ""),
            "end_date"        : market.get("endDate", ""),
            "condition_id"    : market.get("conditionId", ""),
            "token_ids"       : token_ids,          # {"up": "...", "down": "..."}
            "prices"          : prices,              # {"up": 0.52, "down": 0.48}
            "accepting_orders": market.get("acceptingOrders", False),
            "polymarket_url"  : f"https://polymarket.com/event/{market.get('slug','')}",
            "raw"             : market,
        }