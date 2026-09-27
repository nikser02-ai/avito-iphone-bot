"""Тест разбора письма Авито на синтетической фикстуре.

Настоящего письма у меня нет, поэтому фикстура собрана по типовой структуре:
блок на объявление, ссылка с заголовком, цена рядом. Проверяется устойчивость
к обоим вариантам вёрстки — цена в подписи ссылки и цена отдельной строкой.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from avitobot.mail import parse_email_html

# Вариант 1: цена отдельным блоком под заголовком
LETTER_A = """
<html><body>
<h1>Новые объявления по вашему поиску</h1>
<table>
  <tr><td>
    <a href="https://www.avito.ru/moskva/telefony/iphone_14_pro_256gb_4512398765?utm_source=email">
      iPhone 14 Pro 256GB
    </a>
    <div>73 000 &#8381;</div>
    <div>Москва, Химки</div>
  </td></tr>
  <tr><td>
    <a href="https://www.avito.ru/moskva/telefony/iphone_13_128gb_4512300111">
      iPhone 13, 128 ГБ
    </a>
    <div>45 500 &#8381;</div>
  </td></tr>
  <tr><td>
    <a href="https://www.avito.ru/moskva/chasy/apple_watch_series_9_45mm_4512377777">
      Apple Watch Series 9 45mm
    </a>
    <div>28 000 руб</div>
  </td></tr>
</table>
<a href="https://www.avito.ru/profile/settings">Настройки уведомлений</a>
<a href="https://www.avito.ru/unsubscribe">Отписаться</a>
</body></html>
"""

# Вариант 2: цена внутри текста ссылки, заголовок отдельной строкой выше
LETTER_B = """
<html><body>
<div>Свежее по запросу «iPhone»</div>
<div>iPhone 15 Pro Max 512GB отличное состояние</div>
<a href="https://m.avito.ru/moskva/telefony/iphone_15_pro_max_4599911111">125 000 ₽</a>
<div>Ещё объявление</div>
<div>iPhone 12 mini 64 ГБ</div>
<a href="https://www.avito.ru/moskva/telefony/iphone_12_mini_4599922222">21 000 ₽</a>
</body></html>
"""


def run():
    failures = []

    a = {item["id"]: item for item in parse_email_html(LETTER_A)}
    if len(a) != 3:
        failures.append(f"письмо A: ждали 3 объявления, получили {len(a)}: {list(a)}")
    expected_a = {
        "4512398765": ("iPhone 14 Pro 256GB", 73000),
        "4512300111": ("iPhone 13, 128 ГБ", 45500),
        "4512377777": ("Apple Watch Series 9 45mm", 28000),
    }
    for item_id, (title, price) in expected_a.items():
        got = a.get(item_id)
        if not got:
            failures.append(f"письмо A: объявление {item_id} не найдено")
            continue
        if got["title"] != title:
            failures.append(f"письмо A {item_id}: заголовок {got['title']!r}, ждали {title!r}")
        if got["price"] != price:
            failures.append(f"письмо A {item_id}: цена {got['price']}, ждали {price}")

    # Служебные ссылки не должны попасть в объявления
    if any(k in a for k in ("settings", "unsubscribe")):
        failures.append("письмо A: служебная ссылка принята за объявление")

    b = {item["id"]: item for item in parse_email_html(LETTER_B)}
    if len(b) != 2:
        failures.append(f"письмо B: ждали 2 объявления, получили {len(b)}: {list(b)}")
    expected_b = {
        "4599911111": ("iPhone 15 Pro Max 512GB отличное состояние", 125000),
        "4599922222": ("iPhone 12 mini 64 ГБ", 21000),
    }
    for item_id, (title, price) in expected_b.items():
        got = b.get(item_id)
        if not got:
            failures.append(f"письмо B: объявление {item_id} не найдено")
            continue
        if got["price"] != price:
            failures.append(f"письмо B {item_id}: цена {got['price']}, ждали {price}")
        # Заголовком не должна становиться цена: из него бот берёт модель
        if got["title"] != title:
            failures.append(f"письмо B {item_id}: заголовок {got['title']!r}, ждали {title!r}")

    # Ни у одного объявления заголовок не должен быть просто ценой
    for source, items in (("A", a), ("B", b)):
        for item in items.values():
            if item["title"].replace(" ", "").rstrip("₽руб.").isdigit():
                failures.append(f"письмо {source} {item['id']}: заголовком стала цена")

    # Письмо без объявлений не должно ничего выдумывать
    if parse_email_html("<html><body><p>Здравствуйте!</p></body></html>"):
        failures.append("пустое письмо дало объявления")

    print("Письмо A:")
    for item in a.values():
        print(f"  {item['id']}  {item['price']:>7} ₽  {item['title']}")
    print("Письмо B:")
    for item in b.values():
        print(f"  {item['id']}  {item['price']:>7} ₽  {item['title']}")

    if failures:
        print(f"\nПРОВАЛЕНО {len(failures)}:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("\nOK — письма разбираются")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
