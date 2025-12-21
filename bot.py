import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = "8577361820:AAH-6wct2IpYn1aSryaDaT1HnFK3rQ-va4c"
PAIR = "SOLUSDT"
INTERVAL = 5  # секунд

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

subscribers = set()
last_price = None


async def get_price():
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={PAIR}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["price"])


@dp.message(Command("start"))
async def start(msg: Message):
    subscribers.add(msg.chat.id)
    await msg.answer("✅ Подписал на SOLUSDT\nБуду писать когда цена растёт или падает.")


async def price_watcher():
    global last_price
    await asyncio.sleep(2)

    while True:
        try:
            price = await get_price()

            if last_price is not None:
                if price > last_price:
                    text = f"📈 SOL растёт\n{last_price:.2f} → {price:.2f}"
                elif price < last_price:
                    text = f"📉 SOL падает\n{last_price:.2f} → {price:.2f}"
                else:
                    text = None

                if text:
                    for chat_id in subscribers:
                        await bot.send_message(chat_id, text)

            last_price = price

        except Exception as e:
            print("Ошибка:", e)

        await asyncio.sleep(INTERVAL)


async def main():
    asyncio.create_task(price_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
