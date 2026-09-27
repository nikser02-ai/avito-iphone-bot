"""Самодиагностика: пролезаем ли мы к Авито и переживает ли база перезапуск.

Один и тот же отчёт доступен из командной строки (`--doctor`) и командой
/doctor в самом боте. Второе нужно для хостингов без доступа к шеллу:
там это единственный способ узнать, что происходит.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import httpx

from .avito import AvitoBlocked, AvitoClient
from .config import Settings
from .storage import Storage

OK, WARN, BAD = "✅", "⚠️", "❌"

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
APP_UA = "Avito/107.0 (ru.avito.app; build:107; iOS 17.5.1) Alamofire/5.9.1"

# Разные двери в Авито. Нужно понять, закрыты ли все или только часть:
# robots.txt отвечает на вопрос «забанен ли адрес вообще», остальные —
# какой способ добычи данных ещё жив.
PROBES = [
    ("robots.txt", "https://www.avito.ru/robots.txt", BROWSER_UA),
    ("Главная", "https://www.avito.ru/", BROWSER_UA),
    ("Каталог телефонов", "https://www.avito.ru/moskva/telefony/iphone-ASgBAgICAUSkA8SQAQ", BROWSER_UA),
    ("Мобильный API", "https://m.avito.ru/api/9/items?key={key}&query=iPhone&locationId=637640"
                      "&categoryId=84&page=1&limit=10&sort=date", APP_UA),
]


async def probe_doors(api_key: str, proxy: Optional[str]) -> List[Tuple[str, str]]:
    """Стучится во все двери подряд и возвращает, что ответила каждая."""
    results = []
    kwargs = {"proxy": proxy} if proxy else {}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, **kwargs) as http:
        for name, url, agent in PROBES:
            try:
                response = await http.get(
                    url.format(key=api_key),
                    headers={"User-Agent": agent, "Accept-Language": "ru-RU,ru;q=0.9"},
                )
                mark = OK if response.status_code == 200 else BAD
                note = f"HTTP {response.status_code}"
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    note += f", Retry-After: {retry_after}"
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location", "")[:60]
                    note += f" → {location}"
                results.append((name, f"{mark} {note}"))
            except Exception as exc:  # noqa: BLE001
                results.append((name, f"{BAD} {type(exc).__name__}"))
    return results


def _age(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"


async def external_ip(proxy: Optional[str]) -> dict:
    kwargs = {"proxy": proxy} if proxy else {}
    try:
        async with httpx.AsyncClient(timeout=15, **kwargs) as http:
            return (await http.get("https://ipinfo.io/json")).json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def report(client: AvitoClient, storage: Optional[Storage],
                 settings: Settings) -> Tuple[str, int]:
    """Возвращает готовый текст отчёта и код выхода (0 — всё хорошо)."""
    lines = ["<b>Диагностика</b>"]
    code = 0

    # ------------------------------------------------ сеть
    lines.append(f"\n<b>Сеть</b>\nПрокси: {settings.proxy or 'не задан'}")
    info = await external_ip(settings.proxy)
    if "error" in info:
        lines.append(f"{WARN} IP не определён: {info['error']}")
    else:
        country = info.get("country", "?")
        mark = OK if country == "RU" else WARN
        lines.append(f"{mark} IP {info.get('ip')} — {country}, {info.get('org', '')}")
        if country != "RU":
            lines.append("   Авито почти наверняка ответит 403.")

    # ------------------------------------------------ какие двери открыты
    lines.append("\n<b>Двери Авито</b>")
    for name, verdict in await probe_doors(settings.api_key, settings.proxy):
        lines.append(f"{name}: {verdict}")

    # ------------------------------------------------ доступ к Авито
    lines.append("\n<b>Разбор выдачи</b>")
    try:
        items = await client.search("iPhone", settings.location_ids[0], page=1,
                                    category_id=settings.category_id)
        if items:
            first = items[0]
            lines.append(f"{OK} выдача получена: {len(items)} объявлений")
            lines.append(f"   пример: {first['title'][:60]} — {first['price']} ₽")
        else:
            lines.append(f"{WARN} ответ пустой — проверьте categoryId и locationId")
            code = 1
    except AvitoBlocked as exc:
        lines.append(f"{BAD} {exc}")
        code = 2
    except Exception as exc:  # noqa: BLE001
        lines.append(f"{BAD} ошибка запроса: {exc}")
        code = 3

    # ------------------------------------------------ живучесть базы
    if storage is not None:
        lines.append("\n<b>База</b>")
        created = storage.get_meta("created_at")
        counts = storage.counts()
        if created:
            age = int(time.time()) - int(created)
            # Если после перезапуска база снова «только что созданная» —
            # файловая система хостинга не сохраняется между рестартами.
            mark = OK if age > 600 else WARN
            lines.append(f"{mark} создана {_age(age)} назад")
            if age <= 600:
                lines.append("   Если бот работает давно, а база свежая — "
                             "хостинг стирает файлы при перезапуске, "
                             "и ценовой индекс не накопится.")
        lines.append(f"   объявлений {counts['total']}, чистых {counts['clean']}, "
                     f"моделей {counts['models']}")

        ready = storage.index_summary(settings.median_window_days, settings.min_sample)
        if ready:
            lines.append(f"{OK} медиан готово: {len(ready)}")
        else:
            lines.append(f"{WARN} медиан пока нет — нужно {settings.min_sample} "
                         "чистых объявлений на связку модель+память")

    lines.append("\n<b>Итог</b>")
    lines.append({
        0: f"{OK} Авито доступен, бот может работать здесь.",
        1: f"{WARN} Связь есть, но выдача пустая — нужна настройка категории.",
        2: f"{BAD} Антибот режет этот адрес. Нужен другой IP или прокси.",
        3: f"{BAD} Сеть недоступна.",
    }[code])
    return "\n".join(lines), code
