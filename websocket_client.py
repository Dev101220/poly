"""
WebSocket Client
================
Taken from discountry/polymarket-trading-bot (MIT).
Connects to: wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribe message format:
    {"assets_ids": ["<token_id1>", ...], "type": "market", "operation": "subscribe"}

Incoming message types:
    event_type == "book"             -> full orderbook snapshot
    event_type == "price_change"     -> price_changes array with best_bid / best_ask per change
    event_type == "last_trade_price" -> last matched trade price
"""

import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Set, Union, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

logger = logging.getLogger("polymarket_bot")

WSS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _load_websockets():
    try:
        from websockets.asyncio.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
        return ws_connect, ConnectionClosed
    except ImportError:
        import websockets
        return websockets.connect, websockets.exceptions.ConnectionClosed


@dataclass
class OrderbookLevel:
    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    asset_id: str
    market: str
    timestamp: int
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    hash: str = ""

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask < 1:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask or 0.5

    @classmethod
    def from_message(cls, msg: Dict[str, Any]) -> "OrderbookSnapshot":
        bids = sorted(
            [OrderbookLevel(float(b["price"]), float(b["size"])) for b in msg.get("bids", [])],
            key=lambda x: x.price, reverse=True
        )
        asks = sorted(
            [OrderbookLevel(float(a["price"]), float(a["size"])) for a in msg.get("asks", [])],
            key=lambda x: x.price
        )
        return cls(
            asset_id=msg.get("asset_id", ""),
            market=msg.get("market", ""),
            timestamp=int(msg.get("timestamp", 0)),
            bids=bids,
            asks=asks,
            hash=msg.get("hash", ""),
        )


@dataclass
class PriceChange:
    asset_id: str
    price: float
    size: float
    side: str
    best_bid: float
    best_ask: float

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PriceChange":
        return cls(
            asset_id=d.get("asset_id", ""),
            price=float(d.get("price", 0)),
            size=float(d.get("size", 0)),
            side=d.get("side", ""),
            best_bid=float(d.get("best_bid", 0)),
            best_ask=float(d.get("best_ask", 1)),
        )


BookCallback        = Callable[["OrderbookSnapshot"], Union[None, Awaitable[None]]]
PriceChangeCallback = Callable[[str, List[PriceChange]], Union[None, Awaitable[None]]]


class MarketWebSocket:
    """
    WebSocket client for Polymarket CLOB market data.
    Usage:
        ws = MarketWebSocket()

        @ws.on_book
        async def handle_book(snapshot: OrderbookSnapshot):
            print(snapshot.best_ask)

        await ws.subscribe(["token_id_1", "token_id_2"])
        await ws.run()
    """

    def __init__(self, url: str = WSS_MARKET_URL, reconnect_interval: float = 5.0):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self._ws_connect, self._conn_closed = _load_websockets()
        self._ws = None
        self._running = False
        self._subscribed: Set[str] = set()
        self._orderbooks: Dict[str, OrderbookSnapshot] = {}
        self._on_book: Optional[BookCallback] = None
        self._on_price_change: Optional[PriceChangeCallback] = None
        self._on_connect: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None

    # ── decorators ───────────────────────────────────────────────────────────

    def on_book(self, fn: BookCallback) -> BookCallback:
        self._on_book = fn
        return fn

    def on_price_change(self, fn: PriceChangeCallback) -> PriceChangeCallback:
        self._on_price_change = fn
        return fn

    def on_connect(self, fn: Callable) -> Callable:
        self._on_connect = fn
        return fn

    def on_disconnect(self, fn: Callable) -> Callable:
        self._on_disconnect = fn
        return fn

    # ── public helpers ───────────────────────────────────────────────────────

    def get_orderbook(self, asset_id: str) -> Optional[OrderbookSnapshot]:
        return self._orderbooks.get(asset_id)

    def get_mid_price(self, asset_id: str) -> float:
        ob = self._orderbooks.get(asset_id)
        return ob.mid_price if ob else 0.0

    def get_best_ask(self, asset_id: str) -> float:
        ob = self._orderbooks.get(asset_id)
        return ob.best_ask if ob else 1.0

    def get_best_bid(self, asset_id: str) -> float:
        ob = self._orderbooks.get(asset_id)
        return ob.best_bid if ob else 0.0

    @property
    def is_connected(self) -> bool:
        if not self._ws:
            return False
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except Exception:
            try:
                return bool(self._ws.open)
            except Exception:
                return False

    # ── subscribe / unsubscribe ───────────────────────────────────────────────

    async def subscribe(self, asset_ids: List[str], replace: bool = False) -> bool:
        if replace:
            self._subscribed = set(asset_ids)
        else:
            self._subscribed.update(asset_ids)

        if not self.is_connected:
            return False

        msg = {
            "assets_ids": asset_ids,
            "type": "market",
            "operation": "subscribe" if not replace else "subscribe",
        }
        try:
            await self._ws.send(json.dumps(msg))
            logger.info(f"Subscribed to {len(asset_ids)} tokens")
            return True
        except Exception as e:
            logger.error(f"Subscribe failed: {e}")
            return False

    async def subscribe_more(self, asset_ids: List[str]) -> bool:
        return await self.subscribe(asset_ids, replace=False)

    # ── message handling ──────────────────────────────────────────────────────

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        etype = msg.get("event_type", "")

        if etype == "book":
            snap = OrderbookSnapshot.from_message(msg)
            self._orderbooks[snap.asset_id] = snap
            logger.debug(f"[WS book] {snap.asset_id[:16]}… bid={snap.best_bid:.4f} ask={snap.best_ask:.4f}")
            await self._run_cb(self._on_book, snap)

        elif etype == "price_change":
            market = msg.get("market", "")
            changes = [PriceChange.from_dict(pc) for pc in msg.get("price_changes", [])]
            # Also update orderbook cache from price_change data
            for pc in changes:
                ob = self._orderbooks.get(pc.asset_id)
                if ob:
                    if pc.side == "BUY":
                        ob.bids = [OrderbookLevel(pc.best_bid, 0)] + [l for l in ob.bids if l.price != pc.best_bid]
                    else:
                        ob.asks = [OrderbookLevel(pc.best_ask, 0)] + [l for l in ob.asks if l.price != pc.best_ask]
            await self._run_cb(self._on_price_change, market, changes)

        elif etype == "last_trade_price":
            logger.debug(f"[WS trade] {msg.get('asset_id','')[:16]}… price={msg.get('price')}")

    async def _run_cb(self, fn, *args):
        if not fn:
            return
        try:
            r = fn(*args)
            if asyncio.iscoroutine(r):
                await r
        except Exception as e:
            logger.error(f"Callback error: {e}")

    # ── main loop ─────────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        msg_count = 0
        while self._running and self.is_connected:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                msg_count += 1
                if msg_count <= 3 or msg_count % 500 == 0:
                    preview = raw[:150] if len(raw) > 150 else raw
                    logger.info(f"[WS #{msg_count}] {preview}")

                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    await self._handle_message(item)

            except asyncio.TimeoutError:
                logger.debug("WS recv timeout (no messages)")
            except self._conn_closed as e:
                logger.warning(f"WS closed: {e}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except Exception as e:
                logger.error(f"WS recv error: {e}")

    async def connect(self) -> bool:
        try:
            self._ws = await self._ws_connect(
                self.url,
                ping_interval=20,
                ping_timeout=10,
            )
            logger.info(f"WebSocket connected: {self.url}")
            if self._on_connect:
                self._on_connect()
            return True
        except Exception as e:
            logger.error(f"WS connect failed: {e}")
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._on_disconnect:
            self._on_disconnect()

    async def run(self, auto_reconnect: bool = True) -> None:
        self._running = True
        while self._running:
            if not await self.connect():
                if auto_reconnect:
                    await asyncio.sleep(self.reconnect_interval)
                    continue
                break

            # Re-subscribe after connect
            if self._subscribed:
                await self.subscribe(list(self._subscribed))

            await self._recv_loop()

            if self._on_disconnect:
                self._on_disconnect()

            if not self._running:
                break
            if auto_reconnect:
                logger.info(f"Reconnecting in {self.reconnect_interval}s…")
                await asyncio.sleep(self.reconnect_interval)
            else:
                break

    def stop(self):
        self._running = False