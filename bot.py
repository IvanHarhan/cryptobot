import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ==========================
# НАСТРОЙКИ
# ==========================
BOT_TOKEN = "8577361820:AAH-6wct2IpYn1aSryaDaT1HnFK3rQ-va4c"   # <-- вставь токен сюда
SYMBOL = "SOLUSDT"
CHECK_INTERVAL = 30  # секунд
PRICE_DIFF_PERCENT = 0.2  # % изменения для уведомления

# ==========================
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

last_price = None
chat_id_global = None


# ==========================
# BYBIT PRICE
# ==========================
async def get_price():
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "spot", "symbol": SYMBOL}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as r:
            data = await r.json()
            return float(data["result"]["list"][0]["lastPrice"])


# ==========================
# PRICE WATCHER
# ==========================
async def price_watcher():
    global last_price

    await asyncio.sleep(5)

    while True:
        try:
            price = await get_price()

            if last_price is not None and chat_id_global:
                diff = ((price - last_price) / last_price) * 100

                if abs(diff) >= PRICE_DIFF_PERCENT:
                    emoji = "📈" if diff > 0 else "📉"
                    await bot.send_message(
                        chat_id_global,
                        f"{emoji} <b>{SYMBOL}</b>\n"
                        f"Цена: <b>{price}</b>\n"
                        f"Изменение: <b>{diff:.2f}%</b>"
                    )

            last_price = price

        except Exception as e:
            print("Ошибка:", e)

        await asyncio.sleep(CHECK_INTERVAL)


# ==========================
# COMMANDS
# ==========================
@dp.message(Command("start"))
async def start(msg: Message):
    global chat_id_global
    chat_id_global = msg.chat.id

    await msg.answer(
        "🤖 <b>Bybit SOL бот запущен</b>\n\n"
        "Я буду следить за ценой <b>SOLUSDT</b>\n"
        "и писать, если цена двигается 📈📉"
    )


@dp.message(Command("price"))
async def price_cmd(msg: Message):
    price = await get_price()
    await msg.answer(f"💰 Цена <b>{SYMBOL}</b>: <b>{price}</b>")


# ==========================
# MAIN
# ==========================
async def main():
    asyncio.create_task(price_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
