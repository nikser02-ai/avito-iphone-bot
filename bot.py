"""Точка входа для хостингов, которые запускают `python bot.py` — BotHost и подобные.

Локально удобнее `python -m avitobot.main`, но Procfile ожидает именно этот файл.
Весь проект намеренно остаётся чистым Python: посторонние .js в корне сбивают
определение типа сборки на BotHost.
"""
import asyncio
import logging

from avitobot.config import settings
from avitobot.main import run

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    asyncio.run(run())
