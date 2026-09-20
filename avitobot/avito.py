"""Клиент к мобильному API Авито.

Единственное место, которое знает про сеть и формат ответа. Авито меняет
структуру без предупреждения, поэтому извлечение полей намеренно устойчивое:
ищем значение по нескольким возможным путям, а при неудаче умеем сохранить
сырой JSON (`--dump`), чтобы починить разбор за минуты.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

BASE = "https://m.avito.ru"
USER_AGENTS = [
    "Avito/107.0 (ru.avito.app; build:107; iOS 17.5.1) Alamofire/5.9.1",
    "Avito/106.2 (ru.avito.app; build:106; iOS 16.7.8) Alamofire/5.8.0",
]


class AvitoBlocked(Exception):
    """Авито ответил 403/429 — нас режет антибот."""


class RateLimiter:
    """Простой лимитер: не чаще N запросов в минуту, со случайной паузой."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / max(per_minute, 1)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await asyncio.sleep(random.uniform(0.2, 1.1))  # разброс, чтобы не быть метрономом
            self._last = time.monotonic()


def _dig(obj: Any, *paths: str) -> Any:
    """Достаёт первое непустое значение по списку путей вида 'a.b.c'."""
    for path in paths:
        cur = obj
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                cur = None
            if cur is None:
                break
        if cur not in (None, "", [], {}):
            return cur
    return None


def extract_item(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Превращает элемент выдачи в плоский словарь. None — если это не объявление."""
    value = raw.get("value") if isinstance(raw.get("value"), dict) else raw

    item_id = _dig(raw, "id") or _dig(value, "id")
    title = _dig(value, "title")
    if not item_id or not title:
        return None

    price = _dig(value, "priceDetailed.value", "price.value", "price")
    if isinstance(price, str):
        digits = "".join(ch for ch in price if ch.isdigit())
        price = int(digits) if digits else 0
    price = int(price or 0)

    url = _dig(value, "uri_mweb", "urlPath", "uri")
    if url and url.startswith("/"):
        url = f"https://www.avito.ru{url}"
    if not url:
        url = f"https://www.avito.ru/{item_id}"

    images = _dig(value, "images") or []
    image = None
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            # ключи — размеры вида "640x480"; берём самый крупный
            candidates = [v for k, v in first.items() if isinstance(v, str) and v.startswith("http")]
            image = candidates[-1] if candidates else None
        elif isinstance(first, str):
            image = first

    return {
        "id": str(item_id),
        "title": str(title),
        "price": price,
        "url": url,
        "image": image,
        "region": _dig(value, "location.name", "location", "geo.formattedAddress") or "",
        "description": _dig(value, "description", "snippet.text") or "",
        "seller_rating": _dig(value, "seller.rating.score", "rating.score", "seller.score"),
        "seller_reviews": int(_dig(value, "seller.rating.summary", "rating.reviewCount") or 0),
        "photos": len(images) if isinstance(images, list) else 0,
    }


class AvitoClient:
    def __init__(self, api_key: str, proxy: Optional[str], per_minute: int,
                 dump_dir: Optional[str] = None):
        self.api_key = api_key
        self.limiter = RateLimiter(per_minute)
        self.dump_dir = Path(dump_dir) if dump_dir else None
        kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(20.0),
            "follow_redirects": True,
            "headers": {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
        }
        if proxy:
            kwargs["proxy"] = proxy
        self.client = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        await self.limiter.wait()
        params = {"key": self.api_key, **params}
        response = await self.client.get(f"{BASE}{path}", params=params)

        # 3xx здесь — это редирект на страницу проверки: Location либо отсутствует,
        # либо ведёт на капчу. Считаем блокировкой, иначе бот пойдёт за следующей
        # страницей и добавит себе 429 вместо того, чтобы выждать паузу.
        if response.status_code in (403, 429) or 300 <= response.status_code < 400:
            raise AvitoBlocked(
                f"HTTP {response.status_code} — антибот Авито. "
                "Нужен российский IP или резидентный прокси."
            )
        response.raise_for_status()

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AvitoBlocked("Авито вернул не JSON — вероятно, страница проверки") from exc

        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            name = f"{path.strip('/').replace('/', '_')}_{int(time.time())}.json"
            (self.dump_dir / name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return data

    async def search(self, query: str, location_id: int, page: int = 1,
                     category_id: int = 84, limit: int = 50) -> List[Dict[str, Any]]:
        """Страница выдачи, отсортированная по дате — самые свежие первыми."""
        data = await self._get("/api/9/items", {
            "query": query,
            "locationId": location_id,
            "categoryId": category_id,
            "page": page,
            "limit": limit,
            "sort": "date",
            "display": "list",
        })
        raw_items = _dig(data, "result.items", "items") or []
        out = []
        for raw in raw_items:
            if isinstance(raw, dict) and raw.get("type") in (None, "item", "itemsList"):
                item = extract_item(raw)
                if item:
                    out.append(item)
        return out

    async def fetch_description(self, item_id: str) -> str:
        """Полный текст объявления. Дёргаем только для кандидатов — он дорогой."""
        try:
            data = await self._get(f"/api/14/items/{item_id}", {})
            return str(_dig(data, "result.description", "description") or "")
        except (AvitoBlocked, httpx.HTTPError) as exc:
            log.debug("Описание %s не получено: %s", item_id, exc)
            return ""

    async def resolve_location(self, name: str) -> Optional[int]:
        """Находит locationId по названию — надёжнее, чем хардкод."""
        try:
            data = await self._get("/api/1/slocations", {"q": name, "limit": 3})
            locations = _dig(data, "result.locations", "locations") or []
            if locations:
                return int(locations[0].get("id"))
        except (AvitoBlocked, httpx.HTTPError, ValueError, TypeError) as exc:
            log.warning("Не удалось найти locationId для %s: %s", name, exc)
        return None
