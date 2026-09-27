"""Источники объявлений. Конвейер разбора и оценки у них общий.

Два источника дополняют друг друга. Прямой опрос Авито даёт полные данные
(описание, рейтинг продавца, фото), но требует доверенного IP. Письма от
сохранённых поисков приходят всегда, зато данных в них меньше. Работают
независимо: блокировка одного не мешает другому.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .avito import AvitoBlocked, AvitoClient
from .config import Settings
from .mail import Mailbox

log = logging.getLogger(__name__)

SEARCH_QUERIES = {"iphone": "iPhone", "watch": "Apple Watch"}


class AvitoSource:
    """Прямой опрос выдачи Авито."""

    name = "Авито напрямую"
    has_descriptions = True

    def __init__(self, client: AvitoClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def fetch_new(self, pages: Optional[int] = None) -> List[Dict]:
        pages = pages or self.settings.pages_per_sweep
        items: List[Dict] = []
        for query in SEARCH_QUERIES.values():
            for location_id in self.settings.location_ids:
                for page in range(1, pages + 1):
                    batch = await self.client.search(
                        query, location_id, page,
                        category_id=self.settings.category_id,
                    )
                    if not batch:
                        break
                    items.extend(batch)
        return items

    async def fetch_description(self, item_id: str) -> str:
        return await self.client.fetch_description(item_id)


class MailSource:
    """Письма Авито о новых объявлениях по сохранённому поиску."""

    name = "Письма Авито"
    has_descriptions = False

    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    async def fetch_new(self, pages: Optional[int] = None) -> List[Dict]:
        return await self.mailbox.fetch_items()

    async def fetch_description(self, item_id: str) -> str:
        # В письме описания нет, а лезть за ним на сайт — тот же антибот.
        # Флаги считаются по заголовку, этого достаточно для отсева мусора.
        return ""


def build_sources(client: AvitoClient, settings: Settings) -> List:
    """Собирает список источников по настройкам. Письма — если заданы IMAP."""
    sources: List = [AvitoSource(client, settings)]
    if settings.imap_host and settings.imap_user and settings.imap_password:
        sources.append(MailSource(Mailbox(
            host=settings.imap_host, user=settings.imap_user,
            password=settings.imap_password, folder=settings.imap_folder,
            sender=settings.imap_sender,
        )))
        log.info("Источник писем включён: %s@%s", settings.imap_user, settings.imap_host)
    else:
        log.info("Источник писем выключен: IMAP не настроен")
    return sources
