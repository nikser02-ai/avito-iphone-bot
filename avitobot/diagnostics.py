"""Самодиагностика: пролезаем ли мы к Авито и переживает ли база перезапуск.

Один и тот же отчёт доступен из командной строки (`--doctor`) и командой
/doctor в самом боте. Второе нужно для хостингов без доступа к шеллу:
там это единственный способ узнать, что происходит.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import httpx

from .avito import AvitoBlocked, AvitoClient
from .config import Settings
from .storage import Storage

OK, WARN, BAD = "✅", "⚠️", "❌"


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

    # ------------------------------------------------ доступ к Авито
    lines.append("\n<b>Авито</b>")
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
