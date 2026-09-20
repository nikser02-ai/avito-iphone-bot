"""Тесты разбора на реальных формулировках Авито — iPhone и Apple Watch."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from avitobot.normalize import (
    CATEGORY_IPHONE, CATEGORY_WATCH, parse, parse_battery, parse_case_size,
    parse_iphone, parse_storage, parse_watch,
)

IPHONE_CASES = [
    ("iPhone 15 Pro Max 256GB", "iPhone 15 Pro Max"),
    ("Айфон 13 про макс 128 гб", "iPhone 13 Pro Max"),
    ("iPhone 13, 128 ГБ", "iPhone 13"),
    ("iphone13pro 256", "iPhone 13 Pro"),
    ("iPhone 16e 128GB", "iPhone 16e"),
    ("Apple iPhone 12 mini 64gb", "iPhone 12 mini"),
    ("iPhone SE 2022 64", "iPhone SE 2022"),
    ("iPhone XS Max 512", "iPhone XS Max"),
    ("iPhone X 64gb", "iPhone X"),
    ("iPhone XR", "iPhone XR"),
    ("iPhone 6s Plus 32", "iPhone 6s Plus"),
    ("iPhone 14 Plus", "iPhone 14 Plus"),
    ("iPhone Air 256", "iPhone Air"),
    ("Samsung Galaxy S23", None),
    ("iPhone 13 128гб, отдам за 15 000 руб", "iPhone 13"),   # цена не подменяет модель
    ("iPhone 128 гб срочно", None),                           # память не подменяет модель
]

WATCH_CASES = [
    ("Apple Watch Series 9 45mm", "Apple Watch Series 9"),
    ("Эпл вотч ультра 2 49мм", "Apple Watch Ultra 2"),
    ("Apple Watch Ultra 49 mm", "Apple Watch Ultra"),
    ("Apple Watch SE 2 44мм", "Apple Watch SE 2"),
    ("Apple Watch S8 41 mm", "Apple Watch Series 8"),
    ("Apple Watch Series 10 46мм", "Apple Watch Series 10"),
    ("Часы Apple Watch 7 серия 45мм", "Apple Watch Series 7"),
    ("Смарт-часы Xiaomi", None),
    ("iPhone 14 Pro 256", None),                              # не путаем категории
]

STORAGE_CASES = [
    ("iPhone 15 Pro 256GB", 256),
    ("айфон 13 128 гб", 128),
    ("iPhone 15 Pro Max 1TB", 1024),
    ("iPhone 12 без указания", None),
]

SIZE_CASES = [
    ("Apple Watch Series 9 45mm", 45),
    ("Apple Watch Ultra 49 мм", 49),
    ("Apple Watch SE 40мм", 40),
    ("Apple Watch без размера", None),
]

BATTERY_CASES = [
    ("АКБ 89%", 89), ("емкость аккумулятора 100 %", 100),
    ("состояние батареи — 78%", 78), ("без слов про батарею", None),
]

FLAG_CASES = [
    ("iPhone 13 на запчасти", "", "broken"),
    ("Чехол для iPhone 15 Pro", "", "not_a_phone"),
    ("Ремешок для Apple Watch 45mm", "", "not_a_phone"),
    ("iPhone 14 Pro реплика 1в1", "", "fake"),
    ("iPhone 12", "залочен на айклауд", "locked"),
    ("Ремонт iPhone любой сложности", "", "service"),
    ("iPhone 13 Pro", "только предоплата на карту", "prepay"),
    ("iPhone 15", "цена при обмене, рассрочка", "price_bait"),
    ("Дисплей для iPhone 11", "", "not_a_phone"),
]

# Аксессуар в подарок не делает товар аксессуаром
CLEAN_CASES = [
    ("iPhone 15 Pro 256GB, чехол и стекло в подарок", ""),
    ("Apple Watch Series 9 45mm", "в комплекте два ремешка, коробка"),
    ("iPhone 14 Pro 256GB", "Полный комплект, акб 94%, чек есть"),
]


def run():
    failures = []

    for text, expected in IPHONE_CASES:
        got = parse_iphone(text)
        if got != expected:
            failures.append(f"iPhone {text!r}: ждали {expected!r}, получили {got!r}")

    for text, expected in WATCH_CASES:
        got = parse_watch(text)
        if got != expected:
            failures.append(f"Watch {text!r}: ждали {expected!r}, получили {got!r}")

    for text, expected in STORAGE_CASES:
        if parse_storage(text) != expected:
            failures.append(f"ПАМЯТЬ {text!r}: получили {parse_storage(text)!r}")

    for text, expected in SIZE_CASES:
        if parse_case_size(text) != expected:
            failures.append(f"РАЗМЕР {text!r}: получили {parse_case_size(text)!r}")

    for text, expected in BATTERY_CASES:
        if parse_battery(text) != expected:
            failures.append(f"АКБ {text!r}: получили {parse_battery(text)!r}")

    for title, desc, expected_flag in FLAG_CASES:
        got = parse(title, desc)
        if expected_flag not in got.flags:
            failures.append(f"ФЛАГ {title!r}: ждали {expected_flag!r}, получили {got.flags!r}")

    for title, desc in CLEAN_CASES:
        got = parse(title, desc)
        if not got.is_clean:
            failures.append(f"ЧИСТОЕ {title!r} помечено: {got.flag_reasons}")

    # Категории и подписи вариантов
    iphone = parse("iPhone 14 Pro 256GB", "акб 94%")
    if iphone.category != CATEGORY_IPHONE or iphone.variant_label != "256 ГБ":
        failures.append(f"iPhone разобран неверно: {iphone}")
    if iphone.key != "iPhone 14 Pro|256":
        failures.append(f"ключ iPhone неверен: {iphone.key}")

    watch = parse("Apple Watch Series 9 45mm", "")
    if watch.category != CATEGORY_WATCH or watch.variant_label != "45 мм":
        failures.append(f"Watch разобран неверно: {watch}")
    if watch.key != "Apple Watch Series 9|45":
        failures.append(f"ключ Watch неверен: {watch.key}")

    terabyte = parse("iPhone 15 Pro Max 1TB", "")
    if terabyte.variant_label != "1 ТБ":
        failures.append(f"терабайт подписан как {terabyte.variant_label!r}")

    total = (len(IPHONE_CASES) + len(WATCH_CASES) + len(STORAGE_CASES) + len(SIZE_CASES)
             + len(BATTERY_CASES) + len(FLAG_CASES) + len(CLEAN_CASES) + 5)
    if failures:
        print(f"ПРОВАЛЕНО {len(failures)} из {total}:")
        for f in failures:
            print("  ✗", f)
        return 1
    print(f"OK — все {total} проверок прошли")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
