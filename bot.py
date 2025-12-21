import asyncio
import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

# =========================
# НАСТРОЙКИ
# =========================
BOT_TOKEN = "8577361820:AAH-6wct2IpYn1aSryaDaT1HnFK3rQ-va4c"

SUBSCRIBERS_FILE = Path("subscribers.json")
DEFAULT_POLL_SECONDS = 15  # как часто проверяем цену

# Для Binance можно использовать основной домен или data-api (если вдруг режет).
BINANCE_BASE = "https://api.binance.com"
# BINANCE_BASE = "https://data-api.binance.vision"  # альтернатива из доков Binance :contentReference[oaicite:0]{index=0}

BYBIT_BASE = "https://api.bybit.com"  # /v5/market/tickers :contentReference[oaicite:1]{index=1}

# =========================
# МОДЕЛИ
# =========================
@dataclass
class WatchItem:
    exchange: str          # "binance" | "bybit"
    symbol: str            # "SOLUSDT"
    threshold_pct: float   # например 1.0
    period_sec: int        # например 300 (5 минут)
    cooldown_sec: int      # чтобы не спамило, например 120
    last_alert_ts: float = 0.0

@dataclass
class Subscriber:
    chat_id: int
    watches: List[WatchItem]

# =========================
# ХРАНИЛИЩЕ
# =========================
def load_subscribers() -> Dict[str, Subscriber]:
    if not SUBSCRIBERS_FILE.exists():
        return {}
    data = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
    out: Dict[str, Subscriber] = {}
    for k, v in data.items():
        watches = [WatchItem(**w) for w in v.get("watches", [])]
        out[k] = Subscriber(chat_id=int(v["chat_id"]), watches=watches)
    return out

def save_subscribers(subs: Dict[str, Subscriber]) -> None:
    data = {}
    for k, sub in subs.items():
        data[k] = {
            "chat_id": sub.chat_id,
            "watches": [asdict(w) for w in sub.watches],
        }
    SUBSCRIBERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

SUBS: Dict[str, Subscriber] = load_subscribers()

# История цен для % за период:
# key = (exchange, symbol) -> list[(ts, price)]
PRICE_HISTORY: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}

# =========================
# API: цены
# =========================
async def fetch_binance_price(session: aiohttp.ClientSession, symbol: str) -> float:
    # GET /api/v3/ticker/price?symbol=SOLUSDT :contentReference[oaicite:2]{index=2}
    url = f"{BINANCE_BASE}/api/v3/ticker/price"
    async with session.get(url, params={"symbol": symbol.upper()}, timeout=10) as r:
        r.raise_for_status()
        j = await r.json()
        return float(j["price"])

async def fetch_bybit_price(session: aiohttp.ClientSession, symbol: str) -> float:
    # GET /v5/market/tickers?category=spot&symbol=SOLUSDT :contentReference[oaicite:3]{index=3}
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "spot", "symbol": symbol.upper()}
    async with session.get(url, params=params, timeout=10) as r:
        r.raise_for_status()
        j = await r.json()
        lst = j.get("result", {}).get("list", [])
        if not lst:
            raise RuntimeError("Bybit вернул пустой список по этому символу")
        return float(lst[0]["lastPrice"])

async def get_price(session: aiohttp.ClientSession, exchange: str, symbol: str) -> float:
    exchange = exchange.lower()
    if exchange == "binance":
        return await fetch_binance_price(session, symbol)
    if exchange == "bybit":
        return await fetch_bybit_price(session, symbol)
    raise ValueError("exchange должен быть binance или bybit")

# =========================
# Волатильность (простая)
# =========================
async def fetch_binance_klines(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int) -> List[float]:
    # GET /api/v3/klines :contentReference[oaicite:4]{index=4}
    url = f"{BINANCE_BASE}/api/v3/klines"
    async with session.get(url, params={"symbol": symbol.upper(), "interval": interval, "limit": limit}, timeout=10) as r:
        r.raise_for_status()
        j = await r.json()
        closes = [float(k[4]) for k in j]  # close price
        return closes

def stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(var)

async def calc_volatility_pct(session: aiohttp.ClientSession, symbol: str) -> float:
    """
    Грубая волатильность за ~30 минут:
    std(log returns) * 100
    """
    closes = await fetch_binance_klines(session, symbol, interval="1m", limit=30)
    rets = []
    for i in range(1, len(closes)):
        if closes[i-1] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i-1]))
    return stdev(rets) * 100.0

# =========================
# ЛОГИКА % ЗА ПЕРИОД
# =========================
def update_history(exchange: str, symbol: str, price: float, now: float) -> None:
    key = (exchange, symbol)
    hist = PRICE_HISTORY.setdefault(key, [])
    hist.append((now, price))
    # чистим хвост: оставляем максимум 1 час истории
    cutoff = now - 3600
    while hist and hist[0][0] < cutoff:
        hist.pop(0)

def pct_change_over_period(exchange: str, symbol: str, period_sec: int, now: float) -> Optional[float]:
    key = (exchange, symbol)
    hist = PRICE_HISTORY.get(key, [])
    if len(hist) < 2:
        return None
    target_ts = now - period_sec
    # ищем ближайшую точку <= target_ts
    past = None
    for ts, price in hist:
        if ts <= target_ts:
            past = (ts, price)
        else:
            break
    if past is None:
        return None
    past_price = past[1]
    if past_price == 0:
        return None
    cur_price = hist[-1][1]
    return (cur_price - past_price) / past_price * 100.0

# =========================
# TELEGRAM BOT
# =========================
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

def get_sub(chat_id: int) -> Subscriber:
    key = str(chat_id)
    if key not in SUBS:
        SUBS[key] = Subscriber(chat_id=chat_id, watches=[])
        save_subscribers(SUBS)
    return SUBS[key]

@dp.message(F.text == "/start")
async def cmd_start(msg: Message):
    get_sub(msg.chat.id)
    await msg.answer(
        "✅ Подписал этот чат на алерты.\n\n"
        "<b>Команды:</b>\n"
        "• /watch binance SOLUSDT 1 300 120  — алерт если >1% за 300с, кулдаун 120с\n"
        "• /watch bybit SOLUSDT 1 300 120\n"
        "• /list — что отслеживаешь\n"
        "• /unwatch binance SOLUSDT — удалить\n"
        "• /vol SOLUSDT — волатильность (примерно за ~30 минут, Binance)\n"
        "• /help"
    )

@dp.message(F.text == "/help")
async def cmd_help(msg: Message):
    await cmd_start(msg)

@dp.message(F.text.startswith("/watch"))
async def cmd_watch(msg: Message):
    parts = msg.text.split()
    if len(parts) != 6:
        await msg.answer("Формат: /watch <binance|bybit> <SYMBOL> <threshold_pct> <period_sec> <cooldown_sec>")
        return

    _, exchange, symbol, thr, period, cooldown = parts
    exchange = exchange.lower()
    symbol = symbol.upper()

    if exchange not in ("binance", "bybit"):
        await msg.answer("Биржа: только binance или bybit")
        return

    try:
        thr_f = float(thr)
        period_i = int(period)
        cooldown_i = int(cooldown)
        assert thr_f > 0
        assert period_i >= 10
        assert cooldown_i >= 0
    except Exception:
        await msg.answer("Проверь числа. Пример: /watch binance SOLUSDT 1 300 120")
        return

    sub = get_sub(msg.chat.id)

    # если уже есть — обновим
    for w in sub.watches:
        if w.exchange == exchange and w.symbol == symbol:
            w.threshold_pct = thr_f
            w.period_sec = period_i
            w.cooldown_sec = cooldown_i
            save_subscribers(SUBS)
            await msg.answer(f"♻️ Обновил: {exchange} {symbol} | {thr_f}% за {period_i}s | cooldown {cooldown_i}s")
            return

    sub.watches.append(WatchItem(exchange=exchange, symbol=symbol, threshold_pct=thr_f, period_sec=period_i, cooldown_sec=cooldown_i))
    save_subscribers(SUBS)
    await msg.answer(f"✅ Добавил: {exchange} {symbol} | {thr_f}% за {period_i}s | cooldown {cooldown_i}s")

@dp.message(F.text == "/list")
async def cmd_list(msg: Message):
    sub = get_sub(msg.chat.id)
    if not sub.watches:
        await msg.answer("Пока ничего не отслеживаешь. Добавь: /watch binance SOLUSDT 1 300 120")
        return
    lines = ["<b>Твои отслеживания:</b>"]
    for w in sub.watches:
        lines.append(f"• {w.exchange} {w.symbol}: {w.threshold_pct}% за {w.period_sec}s (cooldown {w.cooldown_sec}s)")
    await msg.answer("\n".join(lines))

@dp.message(F.text.startswith("/unwatch"))
async def cmd_unwatch(msg: Message):
    parts = msg.text.split()
    if len(parts) != 3:
        await msg.answer("Формат: /unwatch <binance|bybit> <SYMBOL>")
        return
    _, exchange, symbol = parts
    exchange = exchange.lower()
    symbol = symbol.upper()

    sub = get_sub(msg.chat.id)
    before = len(sub.watches)
    sub.watches = [w for w in sub.watches if not (w.exchange == exchange and w.symbol == symbol)]
    after = len(sub.watches)
    save_subscribers(SUBS)

    if after < before:
        await msg.answer(f"🗑️ Удалил {exchange} {symbol}")
    else:
        await msg.answer("Не найдено.")

@dp.message(F.text.startswith("/vol"))
async def cmd_vol(msg: Message):
    parts = msg.text.split()
    if len(parts) != 2:
        await msg.answer("Формат: /vol SOLUSDT")
        return
    symbol = parts[1].upper()
    await msg.answer("Считаю волатильность…")
    try:
        async with aiohttp.ClientSession() as session:
            v = await calc_volatility_pct(session, symbol)
        await msg.answer(f"📈 Волатильность (оценка): <b>{v:.3f}%</b> (1m returns, ~30 минут, Binance)")
    except Exception as e:
        await msg.answer(f"⚠️ Ошибка: {e}")

# =========================
# ФОНОВЫЙ ВОТЧЕР
# =========================
async def watcher_loop():
    await asyncio.sleep(2)  # небольшая пауза после старта
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # собираем уникальные пары
                uniq = set()
                for sub in SUBS.values():
                    for w in sub.watches:
                        uniq.add((w.exchange, w.symbol))
                now = time.time()

                # обновляем цены
                prices: Dict[Tuple[str, str], float] = {}
                for exchange, symbol in uniq:
                    try:
                        price = await get_price(session, exchange, symbol)
                        prices[(exchange, symbol)] = price
                        update_history(exchange, symbol, price, now)
                    except Exception:
                        # если конкретная пара упала — просто пропускаем
                        continue

                # проверяем алерты
                for sub in list(SUBS.values()):
                    for w in sub.watches:
                        key = (w.exchange, w.symbol)
                        if key not in prices:
                            continue
                        chg = pct_change_over_period(w.exchange, w.symbol, w.period_sec, now)
                        if chg is None:
                            continue

                        if abs(chg) >= w.threshold_pct:
                            if (now - w.last_alert_ts) < w.cooldown_sec:
                                continue
                            w.last_alert_ts = now
                            save_subscribers(SUBS)

                            direction = "⬆️" if chg > 0 else "⬇️"
                            txt = (
                                f"{direction} <b>{w.symbol}</b> ({w.exchange})\n"
                                f"Цена: <b>{prices[key]:.4f}</b>\n"
                                f"Изменение за {w.period_sec}s: <b>{chg:+.2f}%</b>\n"
                                f"Порог: {w.threshold_pct}%"
                            )
                            try:
                                await bot.send_message(sub.chat_id, txt)
                            except Exception:
                                # если чат умер/не найден — отписываем
                                SUBS.pop(str(sub.chat_id), None)
                                save_subscribers(SUBS)

            except Exception:
                # чтобы луп никогда не падал
                pass

            await asyncio.sleep(DEFAULT_POLL_SECONDS)

# =========================
# START
# =========================
async def main():
    asyncio.create_task(watcher_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
