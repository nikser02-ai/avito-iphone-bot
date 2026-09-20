"""Точка входа: бот и опрос Авито в одном процессе.

  python -m avitobot.main            запустить бота
  python -m avitobot.main --doctor   проверить, пролезаем ли мы к Авито
  python -m avitobot.main --sweep    один проход без бота, с дампом ответов
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import bot as bot_module
from .avito import AvitoBlocked, AvitoClient
from .config import settings
from .poller import Poller
from .storage import Storage, User

log = logging.getLogger("avitobot")


def build_client(dump: bool = False) -> AvitoClient:
    return AvitoClient(
        api_key=settings.api_key,
        proxy=settings.proxy,
        per_minute=settings.requests_per_minute,
        dump_dir="dumps" if dump else None,
    )


# ---------------------------------------------------------------- диагностика

async def doctor() -> int:
    """Тот же отчёт, что и команда /doctor в боте, только в консоль."""
    from .diagnostics import report

    storage = Storage(settings.db_path) if os.path.exists(settings.db_path) else None
    client = build_client()
    try:
        text, code = await report(client, storage, settings)
        print(re.sub(r"</?[a-z][^>]*>", "", text))
        return code
    finally:
        await client.close()


async def one_sweep() -> int:
    """Один проход с сохранением сырых ответов — для отладки разбора."""
    storage = Storage(settings.db_path)
    client = build_client(dump=True)
    try:
        poller = Poller(client, storage, settings, notifier=None)
        stats = await poller.sweep(pages=2)
        print(stats.summary())
        if stats.errors:
            print("Ошибки:", "; ".join(stats.errors[:3]))
        print("Сырые ответы сохранены в dumps/")
        return 0 if not stats.blocked else 2
    finally:
        await client.close()


# ---------------------------------------------------------------- запуск

async def run() -> None:
    if not settings.bot_token:
        sys.exit("BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.")

    storage = Storage(settings.db_path)
    client = build_client()
    bot = Bot(settings.bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(bot_module.setup(storage, settings, client))

    async def notifier(user: User, item: dict, verdict) -> None:
        await bot_module.send_deal(bot, user, item, verdict)

    poller = Poller(client, storage, settings, notifier=notifier)

    poll_task = asyncio.create_task(poller.run_forever())
    log.info("Бот запущен. Регион: %s, бюджет %s—%s ₽",
             settings.location_ids, settings.price_min, settings.price_max)
    try:
        await dispatcher.start_polling(bot)
    finally:
        poll_task.cancel()
        await client.close()
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Бот мониторинга iPhone на Авито")
    parser.add_argument("--doctor", action="store_true", help="проверить доступ к Авито")
    parser.add_argument("--sweep", action="store_true", help="один проход с дампом ответов")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.doctor:
        sys.exit(asyncio.run(doctor()))
    if args.sweep:
        sys.exit(asyncio.run(one_sweep()))
    asyncio.run(run())


if __name__ == "__main__":
    main()
