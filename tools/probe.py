"""Разведка доступа к Авито изнутри контейнера.

Запускать в консоли хостинга: python tools/probe.py

Перебирает комбинации «адрес + набор заголовков» и печатает, что ответила
каждая. Нужен, чтобы отличить блокировку по IP от придирки к оформлению
запроса: если полный браузерный набор проходит там, где минимальный нет,
дело в заголовках и это чинится кодом.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402

CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36")
APP_UA = "Avito/107.0 (ru.avito.app; build:107; iOS 17.5.1) Alamofire/5.9.1"
API_KEY = "af0deccbgcgidddjgnvljitntccdduijhdinfgjgfjir"

MINIMAL = {"User-Agent": CHROME_UA, "Accept-Language": "ru-RU,ru;q=0.9"}

FULL = {
    "User-Agent": CHROME_UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

MOBILE_WEB = dict(FULL, **{
    "User-Agent": ANDROID_UA,
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
})

APP = {"User-Agent": APP_UA, "Accept": "application/json",
       "Accept-Language": "ru-RU,ru;q=0.9"}

CATALOG = "https://www.avito.ru/moskva/telefony/iphone-ASgBAgICAUSkA8SQAQ"
MOBILE_CATALOG = "https://m.avito.ru/moskva/telefony"
API = (f"https://m.avito.ru/api/9/items?key={API_KEY}&query=iPhone"
       "&locationId=637640&categoryId=84&page=1&limit=10&sort=date")

CHECKS = [
    ("robots.txt", "https://www.avito.ru/robots.txt", MINIMAL),
    ("Каталог, минимум заголовков", CATALOG, MINIMAL),
    ("Каталог, полный браузер", CATALOG, FULL),
    ("Каталог, мобильный браузер", CATALOG, MOBILE_WEB),
    ("Мобильный сайт", MOBILE_CATALOG, MOBILE_WEB),
    ("Мобильный API", API, APP),
]


def main() -> None:
    proxy = os.getenv("PROXY") or None
    print(f"Прокси: {proxy or 'не задан'}")

    kwargs = {"proxy": proxy} if proxy else {}
    with httpx.Client(timeout=20, follow_redirects=False, **kwargs) as http:
        try:
            info = http.get("https://ipinfo.io/json").json()
            print(f"IP: {info.get('ip')} — {info.get('country')}, {info.get('org', '')}")
        except Exception as exc:  # noqa: BLE001
            print(f"IP не определён: {exc}")

        print()
        for name, url, headers in CHECKS:
            try:
                response = http.get(url, headers=headers)
                note = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    note += f", {len(response.content)} байт"
                    body = response.text[:2000].lower()
                    if "iphone" in body:
                        note += ", в ответе есть объявления"
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    note += f", Retry-After: {retry_after}"
                if 300 <= response.status_code < 400:
                    note += f" → {response.headers.get('location', 'без Location')}"
                mark = "OK  " if response.status_code == 200 else "БЛОК"
                print(f"{mark} {name:32} {note}")
            except Exception as exc:  # noqa: BLE001
                print(f"ОШИБ {name:32} {type(exc).__name__}: {exc}")

    print("\nЕсли хоть одна строка OK с объявлениями — этот путь можно использовать.")


if __name__ == "__main__":
    main()
