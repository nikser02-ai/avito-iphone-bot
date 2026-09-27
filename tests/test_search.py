"""Тест кнопки «Найти сейчас»: отбор и сортировка лучших предложений."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from avitobot.config import Settings
from avitobot.search import best_deals, scanned_count
from avitobot.storage import Storage


def fill(storage):
    rows = []
    # Рынок iPhone 14 Pro 256: медиана около 100 000
    for i, price in enumerate([96000, 98000, 99000, 100000, 101000,
                               102000, 103000, 99500, 100500, 97000]):
        rows.append((f"p{i}", "iphone", "iPhone 14 Pro", 256, price, None, 4.5, 5, 3))
    # Рынок Apple Watch Series 9 45: медиана около 30 000
    for i, price in enumerate([29000, 30000, 31000, 30500, 29500,
                               32000, 28500, 31500, 30200, 29800]):
        rows.append((f"w{i}", "watch", "Apple Watch Series 9", 45, price, None, 4.5, 5, 3))

    # Три выгодных: средняя скидка, лучшая скидка с отличным продавцом, и часы
    rows.append(("good", "iphone", "iPhone 14 Pro", 256, 80000, 88, 4.4, 6, 4))
    rows.append(("best", "iphone", "iPhone 14 Pro", 256, 74000, 97, 5.0, 60, 9))
    rows.append(("watchdeal", "watch", "Apple Watch Series 9", 45, 21000, None, 4.8, 20, 6))
    # Дорогое — не должно попасть
    rows.append(("pricey", "iphone", "iPhone 14 Pro", 256, 99000, 95, 5.0, 50, 8))

    for rid, cat, model, variant, price, battery, rating, reviews, photos in rows:
        storage.upsert_listing(
            id=rid, title=f"{model} {variant}", price=price, category=cat, model=model,
            variant=variant, battery=battery, url=f"https://www.avito.ru/{rid}",
            image=None, region="Москва", seller_rating=rating, seller_reviews=reviews,
            photos=photos, is_clean=1, flags="")


def run():
    storage = Storage(":memory:")
    settings = Settings()
    settings.min_sample, settings.min_discount, settings.scam_floor = 8, 0.15, 0.45
    settings.min_battery, settings.median_window_days = 85, 21
    settings.price_min, settings.price_max = 10_000, 150_000
    fill(storage)

    failures = []
    user = storage.upsert_user(1, "обе категории")

    found = best_deals(storage, settings, user, limit=5)
    ids = [f.row["id"] for f in found]

    if "best" not in ids or "good" not in ids or "watchdeal" not in ids:
        failures.append(f"не все выгодные найдены: {ids}")
    if "pricey" in ids:
        failures.append("объявление по рыночной цене попало в выгодные")
    if ids and ids[0] != "best":
        failures.append(f"лучшее предложение не первое: порядок {ids}")

    # Оценки должны убывать
    scores = [f.verdict.score for f in found]
    if scores != sorted(scores, reverse=True):
        failures.append(f"сортировка по оценке нарушена: {scores}")

    # Фильтр категории
    storage.update_user(1, categories="iphone")
    only_phones = [f.row["id"] for f in best_deals(storage, settings, storage.get_user(1))]
    if "watchdeal" in only_phones:
        failures.append("при подписке только на iPhone предложены часы")

    # Фильтр бюджета
    storage.update_user(1, categories="iphone,watch", price_max=30000)
    cheap = [f.row["id"] for f in best_deals(storage, settings, storage.get_user(1))]
    if "best" in cheap:
        failures.append("объявление дороже бюджета попало в выдачу")
    if "watchdeal" not in cheap:
        failures.append("часы в рамках бюджета не найдены")

    # Пустая база — ничего не находится и ничего не падает
    empty = Storage(":memory:")
    empty_user = empty.upsert_user(2, "новичок")
    if best_deals(empty, settings, empty_user) or scanned_count(empty, settings, empty_user):
        failures.append("на пустой базе что-то нашлось")

    print(f"Найдено: {ids}")
    print(f"Оценки: {[round(s, 1) for s in scores]}")
    print(f"Только iPhone: {only_phones}")
    print(f"Бюджет до 30 000: {cheap}")

    if failures:
        print(f"\nПРОВАЛЕНО {len(failures)}:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("\nOK — отбор и сортировка работают")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
