"""Поиск по запросу: переоценка уже собранного индекса под фильтры пользователя.

Обычный режим бота — реагировать на новую публикацию. Кнопка «Найти сейчас»
работает иначе: берёт всё, что уже известно, заново считает выгоду и отдаёт
лучшее. Логика живёт отдельно от Telegram, чтобы её можно было проверить.
"""
from __future__ import annotations

from typing import List, NamedTuple

from .config import Settings
from .normalize import ParsedListing
from .scoring import Verdict, evaluate
from .storage import Storage, User


class Found(NamedTuple):
    row: object
    parsed: ParsedListing
    verdict: Verdict


def effective_filters(user: User, settings: Settings) -> dict:
    """Личные настройки пользователя поверх общих значений по умолчанию."""
    return {
        "price_min": user.price_min if user.price_min is not None else settings.price_min,
        "price_max": user.price_max if user.price_max is not None else settings.price_max,
        "min_discount": (user.min_discount if user.min_discount is not None
                         else settings.min_discount),
        "min_battery": (user.min_battery if user.min_battery is not None
                        else settings.min_battery),
    }


def best_deals(storage: Storage, settings: Settings, user: User,
               limit: int = 5) -> List[Found]:
    """Лучшие предложения из индекса, отсортированные по оценке."""
    filters = effective_filters(user, settings)
    rows = storage.candidates(settings.median_window_days, user.category_list,
                              user.model_list, filters["price_min"], filters["price_max"])

    found: List[Found] = []
    for row in rows:
        parsed = ParsedListing(category=row["category"], model=row["model"],
                               variant=row["variant"], battery=row["battery"])
        verdict = evaluate(
            parsed, row["price"],
            storage.market_prices(row["model"], row["variant"], settings.median_window_days),
            price_min=filters["price_min"], price_max=filters["price_max"],
            min_discount=filters["min_discount"], scam_floor=settings.scam_floor,
            min_sample=settings.min_sample, min_battery=filters["min_battery"],
            seller_rating=row["seller_rating"], seller_reviews=row["seller_reviews"],
            photos=row["photos"],
        )
        if verdict.should_notify:
            found.append(Found(row, parsed, verdict))

    found.sort(key=lambda f: f.verdict.score, reverse=True)
    return found[:limit]


def scanned_count(storage: Storage, settings: Settings, user: User) -> int:
    """Сколько объявлений вообще попало под фильтры — для честного отчёта."""
    filters = effective_filters(user, settings)
    return len(storage.candidates(settings.median_window_days, user.category_list,
                                  user.model_list, filters["price_min"],
                                  filters["price_max"]))
