"""Сквозная проверка конвейера на подставной выдаче — без сети.

Проверяется то, ради чего бот существует: среди обычных объявлений есть
настоящая находка по каждой категории, приманка по бросовой цене и мусор,
который обязан отсеяться. Отдельно — что подписка на одну категорию
не приносит другую.
"""
import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from avitobot.config import Settings
from avitobot.poller import Poller
from avitobot.scoring import VERDICT_RISKY, VERDICT_SEND
from avitobot.storage import Storage

PHONE, WATCH = "iPhone 14 Pro", "Apple Watch Series 9"


def listing(item_id, title, price, **kw):
    base = {"id": item_id, "title": title, "price": price, "image": None,
            "url": f"https://www.avito.ru/{item_id}", "region": "Москва",
            "description": "", "seller_rating": 4.7, "seller_reviews": 12, "photos": 5}
    base.update(kw)
    return base


# Рынок: 12 телефонов (медиана ~100 000) и 10 часов (медиана ~30 000)
PHONE_MARKET = [listing(f"p{i}", f"{PHONE} 256GB", p) for i, p in enumerate(
    [96000, 98000, 99000, 100000, 101000, 102000, 103000, 99500, 100500, 97000, 104000, 98500])]
WATCH_MARKET = [listing(f"w{i}", f"{WATCH} 45mm", p) for i, p in enumerate(
    [29000, 30000, 31000, 30500, 29500, 32000, 28500, 31500, 30200, 29800])]

PHONE_SPECIALS = [
    listing("deal", f"{PHONE} 256GB", 73000, region="Химки",
            seller_rating=4.9, seller_reviews=44, photos=8),
    listing("bait", f"{PHONE} 256GB", 25000, seller_rating=None, seller_reviews=0, photos=1),
    listing("ghost", f"{PHONE} 256GB", 30000, seller_rating=None, seller_reviews=0, photos=2),
    listing("case", f"Чехол для {PHONE} 256GB", 700),
    listing("parts", f"{PHONE} 256GB на запчасти", 20000),
    listing("locked", f"{PHONE} 256GB отличное состояние", 70000),
]
WATCH_SPECIALS = [
    listing("wdeal", f"{WATCH} 45mm", 21000, seller_rating=4.8, seller_reviews=19, photos=6),
    listing("strap", f"Ремешок для {WATCH} 45mm", 900),
]

DESCRIPTIONS = {
    "deal": "Полный комплект, акб 93%, чек сохранился. Срочно нужны деньги.",
    "bait": "Пишите в вотсап, отправлю по предоплате",
    "ghost": "Продам телефон, всё работает, торг у капота",
    "locked": "Аппарат залочен на айклауд прошлого владельца, решайте сами",
    "wdeal": "Часы в идеале, полный комплект, два ремешка в подарок",
}


class FakeAvito:
    """Отдаёт заранее заданную выдачу вместо сети, раздельно по категориям."""

    def __init__(self):
        self.description_calls = []
        self.overrides = {}          # id → новая цена, имитация правки объявления

    async def search(self, query, location_id, page=1, category_id=84, limit=50):
        if page != 1:
            return []
        source = (PHONE_MARKET + PHONE_SPECIALS) if query == "iPhone" \
            else (WATCH_MARKET + WATCH_SPECIALS)
        out = []
        for x in source:
            row = dict(x)
            if row["id"] in self.overrides:
                row["price"] = self.overrides[row["id"]]
            out.append(row)
        return out

    async def fetch_description(self, item_id):
        self.description_calls.append(item_id)
        return DESCRIPTIONS.get(item_id, "")


async def scenario():
    storage = Storage(":memory:")
    settings = Settings()
    settings.location_ids = [637640]
    settings.price_min, settings.price_max = 10_000, 150_000
    settings.min_discount, settings.scam_floor = 0.15, 0.45
    settings.min_sample, settings.min_battery = 8, 85
    settings.median_window_days = 21

    storage.upsert_user(1, "обе категории")
    storage.upsert_user(2, "только айфоны")
    storage.update_user(2, categories="iphone")

    sent = []

    async def notifier(user, item, verdict):
        sent.append((user.user_id, item["id"], verdict.verdict, verdict.discount))

    client = FakeAvito()
    poller = Poller(client, storage, settings, notifier=notifier)
    stats = await poller.sweep(pages=1)

    # Второй проход: продавец обычного объявления уронил цену со 100 000 до 70 000.
    # Объявление уже известно боту, но это тоже «появилось выгодное предложение».
    sent_after_first = len(sent)
    client.overrides["p3"] = 70000
    DESCRIPTIONS["p3"] = "Срочно нужны деньги, торг уместен"
    await poller.sweep(pages=1)

    return storage, client, stats, sent, sent_after_first


def run():
    storage, client, stats, sent, sent_after_first = asyncio.run(scenario())
    drops = [s for s in sent[sent_after_first:]]
    both = {s[1]: s for s in sent if s[0] == 1}
    phones_only = {s[1]: s for s in sent if s[0] == 2}
    failures = []

    if "deal" not in both:
        failures.append("находка по iPhone не отправлена")
    elif both["deal"][2] != VERDICT_SEND:
        failures.append(f"находка iPhone: вердикт {both['deal'][2]}, ждали {VERDICT_SEND}")
    elif not 0.20 <= both["deal"][3] <= 0.35:
        failures.append(f"скидка iPhone посчитана неверно: {both['deal'][3]:.0%}")

    if "wdeal" not in both:
        failures.append("находка по Apple Watch не отправлена")
    elif both["wdeal"][2] != VERDICT_SEND:
        failures.append(f"находка Watch: вердикт {both['wdeal'][2]}, ждали {VERDICT_SEND}")

    if "bait" in both:
        failures.append("приманка с предоплатой не отсеяна")
    if "ghost" not in both:
        failures.append("бросовая цена без флагов должна приходить с предупреждением")
    elif both["ghost"][2] != VERDICT_RISKY:
        failures.append(f"бросовая цена: вердикт {both['ghost'][2]}, ждали {VERDICT_RISKY}")

    for junk, why in (("case", "чехол"), ("parts", "на запчасти"), ("strap", "ремешок")):
        if junk in both:
            failures.append(f"{why} не отсеян")
    if "locked" in both:
        failures.append("объявление с привязкой к iCloud не отсеяно по описанию")

    # Подписка только на iPhone не должна приносить часы
    if "wdeal" in phones_only:
        failures.append("подписчику только на iPhone прислали Apple Watch")
    if "deal" not in phones_only:
        failures.append("подписчику только на iPhone не прислали iPhone")

    wasteful = [i for i in client.description_calls
                if i.startswith(("p", "w")) and i not in ("parts",)]
    wasteful = [i for i in wasteful if i not in DESCRIPTIONS]
    if wasteful:
        failures.append(f"лишние запросы описания: {wasteful}")

    # Снижение цены должно долететь до обоих подписчиков
    drop_ids = {d[1] for d in drops}
    if "p3" not in drop_ids:
        failures.append("снижение цены не отправлено — продавец уронил ценник, бот промолчал")
    else:
        for user_id in (1, 2):
            if not any(d[0] == user_id and d[1] == "p3" for d in drops):
                failures.append(f"снижение цены не дошло до подписчика {user_id}")

    counts = storage.counts()
    print(f"Проход: {stats.summary()}")
    print(f"В базе: {counts['total']} объявлений, чистых {counts['clean']}, "
          f"моделей {counts['models']}")
    print(f"Запросов описания: {len(client.description_calls)} {client.description_calls}")
    print("Отправлено в первый проход:")
    for user_id, item_id, verdict, discount in sent[:sent_after_first]:
        print(f"  пользователю {user_id}: {item_id} — {verdict}, -{discount:.0%}")
    print("Отправлено после снижения цены:")
    for user_id, item_id, verdict, discount in drops:
        print(f"  пользователю {user_id}: {item_id} — {verdict}, -{discount:.0%}")

    if failures:
        print(f"\nПРОВАЛЕНО {len(failures)}:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("\nOK — конвейер отработал как задумано")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
