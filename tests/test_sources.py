"""Главная гарантия схемы с двумя источниками.

Прямой опрос Авито заблокирован антиботом, письма приходят — бот обязан
продолжать находить выгодное. Если этот тест падает, вся затея с письмами
бессмысленна.
"""
import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from avitobot.avito import AvitoBlocked
from avitobot.config import Settings
from avitobot.mail import parse_email_html
from avitobot.poller import Poller
from avitobot.scoring import VERDICT_SEND
from avitobot.sources import MailSource
from avitobot.storage import Storage

MODEL = "iPhone 14 Pro"
MARKET_PRICES = [96000, 98000, 99000, 100000, 101000,
                 102000, 103000, 99500, 100500, 97000]


def build_letter() -> str:
    """Письмо Авито: десять обычных объявлений и одна настоящая находка."""
    blocks = []
    for i, price in enumerate(MARKET_PRICES):
        blocks.append(f"""
        <tr><td>
          <a href="https://www.avito.ru/moskva/telefony/iphone_14_pro_45000000{i:02d}">
            {MODEL} 256GB
          </a>
          <div>{price} &#8381;</div>
        </td></tr>""")
    blocks.append(f"""
        <tr><td>
          <a href="https://www.avito.ru/moskva/telefony/iphone_14_pro_deal_4599999999">
            {MODEL} 256GB полный комплект
          </a>
          <div>72000 &#8381;</div>
        </td></tr>""")
    # Мусор, который обязан отсеяться по заголовку
    blocks.append("""
        <tr><td>
          <a href="https://www.avito.ru/moskva/telefony/chehol_4588888888">
            Чехол для iPhone 14 Pro 256GB
          </a>
          <div>700 &#8381;</div>
        </td></tr>""")
    return "<html><body><table>" + "".join(blocks) + "</table></body></html>"


class BlockedAvito:
    """Прямой опрос: антибот режет наглухо."""
    name = "Авито напрямую"
    has_descriptions = True

    async def fetch_new(self, pages=None):
        raise AvitoBlocked("HTTP 403 — антибот Авито")

    async def fetch_description(self, item_id):
        return ""


class FakeMailbox:
    def __init__(self, html):
        self.html = html
        self.calls = 0

    async def fetch_items(self):
        self.calls += 1
        return parse_email_html(self.html) if self.calls == 1 else []


async def scenario():
    storage = Storage(":memory:")
    settings = Settings()
    settings.location_ids = [637640]
    settings.price_min, settings.price_max = 10_000, 150_000
    settings.min_discount, settings.scam_floor = 0.15, 0.45
    settings.min_sample, settings.min_battery = 8, 85
    settings.median_window_days = 21

    storage.upsert_user(1, "подписчик")
    sent = []

    async def notifier(user, item, verdict):
        sent.append((item["id"], verdict.verdict, verdict.discount))

    mailbox = FakeMailbox(build_letter())
    poller = Poller([BlockedAvito(), MailSource(mailbox)], storage, settings,
                    notifier=notifier)
    stats = await poller.sweep(pages=1)
    return storage, stats, sent


def run():
    storage, stats, sent = asyncio.run(scenario())
    by_id = {s[0]: s for s in sent}
    failures = []

    # Заблокированный источник не должен отменять проход
    if stats.blocked:
        failures.append("проход признан заблокированным, хотя письма принесли данные")
    if "Авито напрямую" not in stats.blocked_sources:
        failures.append("блокировка прямого опроса не зафиксирована")
    if stats.fetched != 12:
        failures.append(f"из письма получено {stats.fetched} объявлений, ждали 12")

    # Находка обязана дойти
    deal = by_id.get("4599999999")
    if not deal:
        failures.append("находка из письма не отправлена")
    elif deal[1] != VERDICT_SEND:
        failures.append(f"находка получила вердикт {deal[1]}, ждали {VERDICT_SEND}")
    elif not 0.20 <= deal[2] <= 0.35:
        failures.append(f"скидка посчитана неверно: {deal[2]:.0%}")

    if "4588888888" in by_id:
        failures.append("чехол из письма не отсеян")

    counts = storage.counts()
    if counts["clean"] != 11:
        failures.append(f"в индекс попало {counts['clean']} чистых объявлений, ждали 11")

    print(f"Проход: {stats.summary()}")
    print(f"В базе: {counts['total']} объявлений, чистых {counts['clean']}")
    print("Отправлено:")
    for item_id, verdict, discount in sent:
        print(f"  {item_id}: {verdict}, -{discount:.0%}")

    if failures:
        print(f"\nПРОВАЛЕНО {len(failures)}:")
        for f in failures:
            print("  ✗", f)
        return 1
    print("\nOK — письма работают при заблокированном прямом опросе")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
