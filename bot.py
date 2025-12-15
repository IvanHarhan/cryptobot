import asyncio
import aiohttp
from aiogram import Bot, Dispatcher

BOT_TOKEN = "ВСТАВЬ_ТОКЕН_БОТА"
CHAT_ID = 123456789  # ← твой chat_id

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

URL = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=SOLUSDT"

last_price = None

async def price_watcher():
    global last_price

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(URL) as r:
                    data = await r.json()
                    price = float(data["result"]["list"][0]["lastPrice"])

            if last_price is None:
                last_price = price
                await bot.send_message(
                    CHAT_ID,
                    f"🚀 Бот запущен\nSOL = {price}$"
                )

            elif price != last_price:
                diff = price - last_price
                emoji = "📈" if diff > 0 else "📉"

                await bot.send_message(
                    CHAT_ID,
                    f"{emoji} SOL изменился\n"
                    f"Было: {last_price}$\n"
                    f"Стало: {price}$"
                )

                last_price = price

        except Exception as e:
            print("Ошибка:", e)

        await asyncio.sleep(60)  # проверка раз в минуту


async def main():
    asyncio.create_task(price_watcher())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
