"""Цикл опроса: выдача → разбор → ценовой индекс → решение → рассылка.

Описание объявления стоит отдельного запроса, поэтому работаем в два захода:
по заголовку отсеиваем очевидный мусор и наполняем индекс, а полный текст
тянем только для тех, кто уже прошёл по цене. Так бюджет запросов уходит
на кандидатов, а не на чехлы.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

from .avito import AvitoBlocked, AvitoClient
from .config import Settings
from .normalize import parse
from .scoring import VERDICT_RISKY, VERDICT_SEND, Verdict, evaluate
from .storage import Storage, User

log = logging.getLogger(__name__)

# По одному запросу на категорию: выдача по дате даёт все свежие объявления
SEARCH_QUERIES = {"iphone": "iPhone", "watch": "Apple Watch"}

Notifier = Callable[[User, Dict, Verdict], Awaitable[None]]


@dataclass
class SweepStats:
    fetched: int = 0
    new: int = 0
    candidates: int = 0
    price_drops: int = 0
    notified: int = 0
    blocked: bool = False
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.blocked:
            return "заблокировано антиботом — нужен российский IP"
        return (f"получено {self.fetched}, новых {self.new}, "
                f"снижений цены {self.price_drops}, "
                f"кандидатов {self.candidates}, отправлено {self.notified}")


class Poller:
    def __init__(self, client: AvitoClient, storage: Storage,
                 settings: Settings, notifier: Optional[Notifier] = None):
        self.client = client
        self.storage = storage
        self.settings = settings
        self.notifier = notifier
        self._backoff = 0

    # ------------------------------------------------------------------ проход

    async def sweep(self, pages: Optional[int] = None) -> SweepStats:
        stats = SweepStats()
        pages = pages or self.settings.pages_per_sweep

        for query in SEARCH_QUERIES.values():
          for location_id in self.settings.location_ids:
            for page in range(1, pages + 1):
                try:
                    items = await self.client.search(
                        query, location_id, page,
                        category_id=self.settings.category_id,
                    )
                except AvitoBlocked as exc:
                    log.error("Блокировка: %s", exc)
                    stats.blocked = True
                    stats.errors.append(str(exc))
                    return stats
                except Exception as exc:  # noqa: BLE001 — один сбой не должен ронять цикл
                    log.warning("Ошибка запроса стр. %s: %s", page, exc)
                    stats.errors.append(str(exc))
                    continue

                if not items:
                    break
                stats.fetched += len(items)
                for item in items:
                    await self._handle(item, stats)

        return stats

    async def _handle(self, item: Dict, stats: SweepStats) -> None:
        """Первый заход: разбор по заголовку, запись в индекс."""
        parsed = parse(item["title"], item.get("description", ""))
        if not parsed.model:
            return

        is_new, previous_price = self.storage.upsert_listing(
            id=item["id"], title=item["title"], price=item["price"],
            category=parsed.category, model=parsed.model, variant=parsed.variant,
            battery=parsed.battery,
            url=item["url"], image=item.get("image"), region=item.get("region", ""),
            seller_rating=item.get("seller_rating"),
            seller_reviews=item.get("seller_reviews", 0),
            photos=item.get("photos", 0),
            is_clean=1 if parsed.is_clean else 0,
            flags=",".join(parsed.flags),
        )

        # Уже виденное объявление интересно только одним: продавец снизил цену.
        # Это такое же событие «появилось выгодное предложение», как новая публикация.
        price_drop = None
        if is_new:
            stats.new += 1
        elif previous_price and item["price"] < previous_price * (1 - self.settings.min_price_drop):
            price_drop = previous_price
            stats.price_drops += 1
            log.info("Снижение цены: %s  %s → %s ₽",
                     item["title"][:50], previous_price, item["price"])
        else:
            return

        if not parsed.is_clean:
            return

        # Дешёвая предварительная проверка: стоит ли вообще тратить запрос на описание
        prices = self.storage.market_prices(
            parsed.model, parsed.variant, self.settings.median_window_days
        )
        pre = evaluate(
            parsed, item["price"], prices,
            price_min=self.settings.price_min, price_max=self.settings.price_max,
            min_discount=self.settings.min_discount, scam_floor=self.settings.scam_floor,
            min_sample=self.settings.min_sample, min_battery=0,
        )
        if not pre.should_notify:
            return

        stats.candidates += 1

        # Второй заход: полный текст — только теперь, когда цена уже заинтересовала
        description = await self.client.fetch_description(item["id"])
        if description:
            parsed = parse(item["title"], description)
            self.storage.upsert_listing(
                id=item["id"], title=item["title"], price=item["price"],
                category=parsed.category, model=parsed.model, variant=parsed.variant,
                battery=parsed.battery,
                url=item["url"], image=item.get("image"), region=item.get("region", ""),
                seller_rating=item.get("seller_rating"),
                seller_reviews=item.get("seller_reviews", 0),
                photos=item.get("photos", 0),
                is_clean=1 if parsed.is_clean else 0,
                flags=",".join(parsed.flags),
            )
            if not parsed.is_clean:
                log.info("Отсеян после чтения описания: %s (%s)",
                         item["title"], ", ".join(parsed.flag_reasons))
                return

        item = {**item, "description": description, "price_drop": price_drop}
        await self._dispatch(item, parsed, stats)

    async def _dispatch(self, item: Dict, parsed, stats: SweepStats) -> None:
        """Прогоняет объявление через личные фильтры каждого подписчика."""
        if not self.notifier:
            return

        item = {**item, "category": parsed.category, "model": parsed.model,
                "variant": parsed.variant, "variant_label": parsed.variant_label,
                "battery": parsed.battery}

        # Для снижения цены ключ включает цену, иначе повторная отправка
        # была бы заблокирована прошлой доставкой этого же объявления.
        dedup_key = item["id"] if not item.get("price_drop") else f"{item['id']}:{item['price']}"

        for user in self.storage.active_users():
            if self.storage.was_sent(user.user_id, dedup_key):
                continue
            categories = user.category_list
            if categories and parsed.category not in categories:
                continue
            models = user.model_list
            if models and parsed.model not in models:
                continue

            prices = self.storage.market_prices(
                parsed.model, parsed.variant, self.settings.median_window_days
            )
            verdict = evaluate(
                parsed, item["price"], prices,
                price_min=user.price_min if user.price_min is not None else self.settings.price_min,
                price_max=user.price_max if user.price_max is not None else self.settings.price_max,
                min_discount=(user.min_discount if user.min_discount is not None
                              else self.settings.min_discount),
                scam_floor=self.settings.scam_floor,
                min_sample=self.settings.min_sample,
                min_battery=(user.min_battery if user.min_battery is not None
                             else self.settings.min_battery),
                seller_rating=item.get("seller_rating"),
                seller_reviews=item.get("seller_reviews", 0),
                photos=item.get("photos", 0),
                description_length=len(item.get("description", "")),
            )
            if not verdict.should_notify:
                continue

            try:
                await self.notifier(user, item, verdict)
                self.storage.mark_sent(user.user_id, dedup_key)
                stats.notified += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Не отправлено пользователю %s: %s", user.user_id, exc)

    # ------------------------------------------------------------------ цикл

    async def run_forever(self) -> None:
        if self.storage.get_meta("backfilled") != "1":
            log.info("Первичное наполнение индекса: %s страниц", self.settings.backfill_pages)
            stats = await self.sweep(pages=self.settings.backfill_pages)
            log.info("Наполнение: %s", stats.summary())
            if not stats.blocked:
                self.storage.set_meta("backfilled", "1")

        while True:
            stats = await self.sweep()
            log.info("Проход: %s", stats.summary())

            if stats.blocked:
                self._backoff = min(self._backoff * 2 + 60, 1800)
                log.warning("Пауза %s с из-за блокировки", self._backoff)
                await asyncio.sleep(self._backoff)
                continue

            self._backoff = 0
            await asyncio.sleep(self.settings.poll_interval)
