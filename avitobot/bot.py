"""Телеграм-бот: меню выбора техники, карточки находок, сохранённые объявления."""
from __future__ import annotations

import html
import logging
from typing import Dict, List, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    Message, ReplyKeyboardMarkup,
)

from .config import Settings
from .normalize import CATEGORY_IPHONE, CATEGORY_WATCH, CATEGORY_TITLES
from .scoring import VERDICT_RISKY, Verdict
from .storage import Storage, User

log = logging.getLogger(__name__)
router = Router()

_storage: Optional[Storage] = None
_settings: Optional[Settings] = None
_client = None


def setup(storage: Storage, settings: Settings, client=None) -> Router:
    global _storage, _settings, _client
    _storage, _settings, _client = storage, settings, client
    return router


# ---------------------------------------------------------------- каталог

# Только те модели, что реально встречаются в бюджете 10–150 тыс.
IPHONE_MODELS = [
    "iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone 17", "iPhone Air",
    "iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 16 Plus", "iPhone 16", "iPhone 16e",
    "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15 Plus", "iPhone 15",
    "iPhone 14 Pro Max", "iPhone 14 Pro", "iPhone 14 Plus", "iPhone 14",
    "iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13", "iPhone 13 mini",
    "iPhone 12 Pro Max", "iPhone 12 Pro", "iPhone 12", "iPhone 12 mini",
    "iPhone 11 Pro Max", "iPhone 11 Pro", "iPhone 11", "iPhone XR",
    "iPhone SE 2022", "iPhone SE 2020",
]
WATCH_MODELS = [
    "Apple Watch Ultra 3", "Apple Watch Ultra 2", "Apple Watch Ultra",
    "Apple Watch Series 11", "Apple Watch Series 10", "Apple Watch Series 9",
    "Apple Watch Series 8", "Apple Watch Series 7", "Apple Watch Series 6",
    "Apple Watch Series 5", "Apple Watch Series 4",
    "Apple Watch SE 3", "Apple Watch SE 2", "Apple Watch SE",
]
CATALOG = {CATEGORY_IPHONE: IPHONE_MODELS, CATEGORY_WATCH: WATCH_MODELS}
ALL_MODELS = IPHONE_MODELS + WATCH_MODELS   # индекс в этом списке едет в callback_data

PAGE_SIZE = 8
CATEGORY_ICON = {CATEGORY_IPHONE: "📱", CATEGORY_WATCH: "⌚"}

BTN_FIND = "🔎 Найти сейчас"
BTN_SEARCH = "🎯 Настроить поиск"
BTN_FILTERS = "⚙️ Фильтры"
BTN_SAVED = "💾 Сохранённые"
BTN_MARKET = "📊 Рынок"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶️ Включить"


def money(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", " ") + " ₽"


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FIND)],
            [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_FILTERS)],
            [KeyboardButton(text=BTN_SAVED), KeyboardButton(text=BTN_MARKET)],
            [KeyboardButton(text=BTN_PAUSE if user.active else BTN_RESUME)],
        ],
        resize_keyboard=True,
    )


def _user(message: Message) -> User:
    return _storage.upsert_user(message.from_user.id, message.from_user.username or "")


# ---------------------------------------------------------------- меню «что ищем»

def search_keyboard(user: User) -> InlineKeyboardMarkup:
    chosen = user.category_list
    picked = set(user.model_list)
    rows = []
    for category in (CATEGORY_IPHONE, CATEGORY_WATCH):
        mark = "✅" if category in chosen else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {CATEGORY_ICON[category]} {CATEGORY_TITLES[category]}",
            callback_data=f"cat:{category}")])

    for category in (CATEGORY_IPHONE, CATEGORY_WATCH):
        count = len([m for m in picked if m in CATALOG[category]])
        label = f"Модели {CATEGORY_TITLES[category]}: " + (f"{count} выбрано" if count else "все")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"pg:{category}:0")])

    rows.append([InlineKeyboardButton(text="Готово", callback_data="close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_keyboard(user: User, category: str, page: int) -> InlineKeyboardMarkup:
    models = CATALOG[category]
    picked = set(user.model_list)
    start = page * PAGE_SIZE
    chunk = models[start:start + PAGE_SIZE]

    rows, pair = [], []
    for name in chunk:
        mark = "✅" if name in picked else "⬜"
        short = name.replace("Apple Watch ", "").replace("iPhone ", "")
        pair.append(InlineKeyboardButton(text=f"{mark} {short}",
                                         callback_data=f"mt:{ALL_MODELS.index(name)}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"pg:{category}:{page-1}"))
    if start + PAGE_SIZE < len(models):
        nav.append(InlineKeyboardButton(text="Дальше ›", callback_data=f"pg:{category}:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Сбросить — следить за всеми",
                                      callback_data=f"clr:{category}")])
    rows.append([InlineKeyboardButton(text="‹ К категориям", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SEARCH_HINT = ("<b>Что ищем</b>\n\nОтметьте категории. "
               "Если не выбрать ни одной модели — слежу за всеми в категории.")


@router.message(F.text == BTN_SEARCH)
@router.message(Command("search"))
async def open_search(message: Message) -> None:
    user = _user(message)
    await message.answer(SEARCH_HINT, reply_markup=search_keyboard(user))


@router.callback_query(F.data.startswith("cat:"))
async def toggle_category(call: CallbackQuery) -> None:
    category = call.data.split(":", 1)[1]
    user = _storage.upsert_user(call.from_user.id, call.from_user.username or "")
    chosen = user.category_list
    if category in chosen:
        chosen.remove(category)
    else:
        chosen.append(category)
    _storage.update_user(user.user_id, categories=",".join(chosen))
    user = _storage.get_user(user.user_id)
    await call.message.edit_reply_markup(reply_markup=search_keyboard(user))
    await call.answer("Слежу" if category in chosen else "Больше не слежу")


@router.callback_query(F.data.startswith("pg:"))
async def open_models(call: CallbackQuery) -> None:
    _, category, page = call.data.split(":")
    user = _storage.upsert_user(call.from_user.id, call.from_user.username or "")
    await call.message.edit_text(
        f"<b>Модели {CATEGORY_TITLES[category]}</b>\n\n"
        "Отмеченные — те, за которыми слежу. Ничего не отмечено — слежу за всеми.",
        reply_markup=models_keyboard(user, category, int(page)))
    await call.answer()


@router.callback_query(F.data.startswith("mt:"))
async def toggle_model(call: CallbackQuery) -> None:
    name = ALL_MODELS[int(call.data.split(":", 1)[1])]
    user = _storage.upsert_user(call.from_user.id, call.from_user.username or "")
    picked = user.model_list
    if name in picked:
        picked.remove(name)
    else:
        picked.append(name)
    _storage.update_user(user.user_id, models=",".join(picked))

    user = _storage.get_user(user.user_id)
    category = CATEGORY_WATCH if name in WATCH_MODELS else CATEGORY_IPHONE
    page = CATALOG[category].index(name) // PAGE_SIZE
    await call.message.edit_reply_markup(reply_markup=models_keyboard(user, category, page))
    await call.answer(f"{'Добавил' if name in picked else 'Убрал'}: {name}")


@router.callback_query(F.data.startswith("clr:"))
async def clear_models(call: CallbackQuery) -> None:
    category = call.data.split(":", 1)[1]
    user = _storage.upsert_user(call.from_user.id, call.from_user.username or "")
    kept = [m for m in user.model_list if m not in CATALOG[category]]
    _storage.update_user(user.user_id, models=",".join(kept))
    user = _storage.get_user(user.user_id)
    await call.message.edit_reply_markup(reply_markup=models_keyboard(user, category, 0))
    await call.answer(f"Слежу за всеми {CATEGORY_TITLES[category]}")


@router.callback_query(F.data == "back")
async def back_to_search(call: CallbackQuery) -> None:
    user = _storage.upsert_user(call.from_user.id, call.from_user.username or "")
    await call.message.edit_text(SEARCH_HINT, reply_markup=search_keyboard(user))
    await call.answer()


@router.callback_query(F.data == "close")
async def close_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("Настройки сохранены. Жду свежие объявления.")
    await call.answer()


# ---------------------------------------------------------------- карточка находки

def deal_keyboard(listing_id: str, url: str, saved: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать продавцу", url=url)],
        [
            InlineKeyboardButton(
                text="✅ Сохранено" if saved else "💾 Сохранить",
                callback_data=f"{'uns' if saved else 'sav'}:{listing_id}"),
            InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skp:{listing_id}"),
        ],
    ])


def format_deal(item: Dict, verdict: Verdict) -> str:
    risky = verdict.verdict == VERDICT_RISKY
    category = item.get("category") or CATEGORY_IPHONE
    icon = CATEGORY_ICON.get(category, "📦")

    name = item.get("model") or item["title"]
    variant = item.get("variant_label") or ""
    saving = verdict.saving(item["price"])

    drop = item.get("price_drop")
    if risky:
        head = "⚠️ <b>Подозрительно дёшево</b>"
    elif drop:
        head = "📉 <b>Продавец снизил цену</b>"
    else:
        head = "🔥 <b>Находка</b>"

    lines = [
        head,
        f"{icon} <b>{html.escape(name)}</b>" + (f" · {variant}" if variant else ""),
        "",
        f"💰 <b>{money(item['price'])}</b>   <s>{money(verdict.median_price)}</s> медиана",
    ]
    if drop:
        lines.append(f"↘️ Было {money(drop)} — упало на {money(drop - item['price'])}")
    if saving:
        lines.append(f"📉 Дешевле рынка на <b>{verdict.discount:.0%}</b> — "
                     f"экономия {money(saving)}")

    facts = []
    if item.get("battery"):
        facts.append(f"🔋 АКБ {item['battery']}%")
    if item.get("seller_rating"):
        facts.append(f"⭐ {item['seller_rating']:.1f} ({item.get('seller_reviews', 0)} отз.)")
    if item.get("region"):
        facts.append(f"📍 {html.escape(str(item['region']))}")
    if item.get("photos"):
        facts.append(f"🖼 {item['photos']} фото")
    if facts:
        lines.append(" · ".join(facts))

    lines.append(f"📊 Оценка {verdict.score:.0f}/100 · медиана по {verdict.sample_size} объявл.")

    if risky:
        lines.append("\n<i>Такая цена обычно означает приманку, битый аппарат "
                     "или предоплатную схему. Проверяйте лично, деньги вперёд не отправляйте.</i>")

    lines.append(f"\n<a href=\"{item['url']}\">{html.escape(item['title'])}</a>")
    return "\n".join(lines)


async def send_deal(bot: Bot, user: User, item: Dict, verdict: Verdict) -> None:
    text = format_deal(item, verdict)
    keyboard = deal_keyboard(item["id"], item["url"])
    if item.get("image"):
        try:
            await bot.send_photo(user.user_id, photo=item["image"], caption=text,
                                 reply_markup=keyboard)
            return
        except Exception as exc:  # noqa: BLE001 — картинка не должна съедать находку
            log.debug("Фото не ушло, шлём текстом: %s", exc)
    await bot.send_message(user.user_id, text, reply_markup=keyboard)


async def _refresh_deal_keyboard(call: CallbackQuery, listing_id: str, saved: bool) -> None:
    url = next((b.url for row in call.message.reply_markup.inline_keyboard
                for b in row if b.url), None)
    if url:
        await call.message.edit_reply_markup(
            reply_markup=deal_keyboard(listing_id, url, saved))


@router.callback_query(F.data.startswith("sav:"))
async def save_listing(call: CallbackQuery) -> None:
    listing_id = call.data.split(":", 1)[1]
    _storage.save_listing(call.from_user.id, listing_id)
    await _refresh_deal_keyboard(call, listing_id, saved=True)
    await call.answer("Сохранено — смотрите в «💾 Сохранённые»")


@router.callback_query(F.data.startswith("uns:"))
async def unsave_listing(call: CallbackQuery) -> None:
    listing_id = call.data.split(":", 1)[1]
    _storage.unsave_listing(call.from_user.id, listing_id)
    await _refresh_deal_keyboard(call, listing_id, saved=False)
    await call.answer("Убрано из сохранённых")


@router.callback_query(F.data.startswith("skp:"))
async def skip_listing(call: CallbackQuery) -> None:
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001 — старое сообщение удалить нельзя, просто гасим кнопки
        await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Пропущено")


# ---------------------------------------------------------------- поиск по запросу

@router.message(F.text == BTN_FIND)
@router.message(Command("find"))
async def find_now(message: Message) -> None:
    """Показывает лучшее из того, что уже известно боту, прямо сейчас."""
    from .search import best_deals, effective_filters, scanned_count

    user = _user(message)
    notice = await message.answer("Ищу…")

    scanned = scanned_count(_storage, _settings, user)
    if not scanned:
        counts = _storage.counts()
        if counts["total"] == 0:
            await notice.edit_text(
                "База пустая — с Авито не пришло ни одного объявления.\n\n"
                "Отправьте <code>/doctor</code>: скорее всего антибот Авито "
                "не пускает нас с текущего адреса.")
        else:
            await notice.edit_text(
                f"Под ваши фильтры ничего не подходит.\n"
                f"В базе {counts['total']} объявлений — попробуйте расширить бюджет "
                "или снять ограничение по моделям.")
        return

    found = best_deals(_storage, _settings, user, limit=5)
    if not found:
        min_discount = effective_filters(user, _settings)["min_discount"]
        await notice.edit_text(
            f"Просмотрел {scanned} объявлений — ничего дешевле медианы "
            f"на {min_discount:.0%} сейчас нет.\n\n"
            "Это нормально: выгодные варианты появляются не каждый час. "
            "Как только появится — пришлю сам, ждать у кнопки не нужно.")
        return

    await notice.edit_text(f"Просмотрел {scanned} объявлений, показываю {len(found)} лучших:")
    for item_row, parsed, verdict in found:
        item = {
            "id": item_row["id"], "title": item_row["title"], "price": item_row["price"],
            "url": item_row["url"], "image": item_row["image"], "region": item_row["region"],
            "category": parsed.category, "model": parsed.model,
            "variant_label": parsed.variant_label, "battery": item_row["battery"],
            "seller_rating": item_row["seller_rating"],
            "seller_reviews": item_row["seller_reviews"], "photos": item_row["photos"],
        }
        await send_deal(message.bot, user, item, verdict)


# ---------------------------------------------------------------- сохранённые

@router.message(F.text == BTN_SAVED)
@router.message(Command("saved"))
async def show_saved(message: Message) -> None:
    rows = _storage.saved_listings(message.from_user.id, 30)
    if not rows:
        await message.answer("Сохранённых объявлений пока нет.\n"
                             "Кнопка «💾 Сохранить» под находкой добавляет их сюда.")
        return

    lines = [f"<b>Сохранённые объявления</b> — {len(rows)}\n"]
    for i, r in enumerate(rows, 1):
        variant = ""
        if r["variant"]:
            variant = f" · {r['variant']} мм" if r["category"] == CATEGORY_WATCH \
                else f" · {r['variant']} ГБ"
        lines.append(f"{i}. <a href=\"{r['url']}\">{html.escape(r['model'] or r['title'])}"
                     f"{variant}</a> — <b>{money(r['price'])}</b>")

    await message.answer(
        "\n".join(lines), disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Очистить список", callback_data="clrsaved")]]))


@router.callback_query(F.data == "clrsaved")
async def clear_saved(call: CallbackQuery) -> None:
    for row in _storage.saved_listings(call.from_user.id, 1000):
        _storage.unsave_listing(call.from_user.id, row["id"])
    await call.message.edit_text("Список сохранённых очищен.")
    await call.answer()


# ---------------------------------------------------------------- фильтры и прочее

HELP = """<b>Что я делаю</b>
Слежу за свежими объявлениями об iPhone и Apple Watch в Москве и области,
сравниваю цену с медианой по такой же модели и присылаю только то, что
заметно дешевле рынка и не выглядит разводом.

<b>Кнопки</b>
🔎 Найти сейчас — показать выгодное из уже известного
🎯 Настроить поиск — категории и модели
⚙️ Фильтры — бюджет, скидка, АКБ
💾 Сохранённые — отложенные объявления
📊 Рынок — медианы, которые бот вывел сам

<b>Команды</b>
/budget 20000-90000 — диапазон цены
/battery 85 — минимальное состояние АКБ, 0 отключает
/discount 20 — на сколько процентов ниже медианы
/doctor — диагностика доступа к Авито"""


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = _user(message)
    _storage.update_user(user.user_id, active=1)
    user = _storage.get_user(user.user_id)
    await message.answer(
        "Подписка включена. Первые находки появятся, как только наберётся "
        f"статистика по ценам — нужно минимум {_settings.min_sample} объявлений "
        "на каждую модель.\n\n" + HELP,
        reply_markup=main_keyboard(user))
    await message.answer(SEARCH_HINT, reply_markup=search_keyboard(user))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=main_keyboard(_user(message)))


@router.message(F.text == BTN_FILTERS)
@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user = _user(message)
    categories = ", ".join(CATEGORY_TITLES[c] for c in user.category_list) or "ничего не выбрано"
    models = ", ".join(user.model_list) if user.model_list else "все в выбранных категориях"
    price_min = user.price_min if user.price_min is not None else _settings.price_min
    price_max = user.price_max if user.price_max is not None else _settings.price_max
    discount = user.min_discount if user.min_discount is not None else _settings.min_discount
    battery = user.min_battery if user.min_battery is not None else _settings.min_battery
    await message.answer(
        f"<b>Фильтры</b>\n"
        f"Статус: {'активна' if user.active else 'на паузе'}\n"
        f"Категории: {categories}\n"
        f"Модели: {models}\n"
        f"Бюджет: {money(price_min)} — {money(price_max)}\n"
        f"Минимальная скидка: {discount:.0%}\n"
        f"Минимум АКБ: {battery}%" + ("" if battery else " (не фильтруем)") +
        f"\nСохранено объявлений: {_storage.saved_count(user.user_id)}"
        f"\n\nВаш telegram id: <code>{message.from_user.id}</code>",
        reply_markup=main_keyboard(user))


@router.message(Command("budget"))
async def cmd_budget(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").replace(" ", "").replace("—", "-")
    parts = raw.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("Формат: <code>/budget 20000-90000</code>")
        return
    low, high = int(parts[0]), int(parts[1])
    if low >= high:
        await message.answer("Нижняя граница должна быть меньше верхней.")
        return
    _user(message)
    _storage.update_user(message.from_user.id, price_min=low, price_max=high)
    await message.answer(f"Бюджет: {money(low)} — {money(high)}")


@router.message(Command("battery"))
async def cmd_battery(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip().rstrip("%")
    if not raw.isdigit() or int(raw) > 100:
        await message.answer("Формат: <code>/battery 85</code>, 0 отключает фильтр.")
        return
    _user(message)
    _storage.update_user(message.from_user.id, min_battery=int(raw))
    await message.answer(f"Минимум АКБ: {raw}%" if int(raw) else "Фильтр по АКБ выключен.")


@router.message(Command("discount"))
async def cmd_discount(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip().rstrip("%")
    if not raw.isdigit() or not 1 <= int(raw) <= 90:
        await message.answer("Формат: <code>/discount 20</code> — от 1 до 90 процентов.")
        return
    _user(message)
    _storage.update_user(message.from_user.id, min_discount=int(raw) / 100)
    await message.answer(f"Буду присылать объявления дешевле медианы на {raw}% и больше.")


@router.message(F.text == BTN_PAUSE)
@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    _user(message)
    _storage.update_user(message.from_user.id, active=0)
    user = _storage.get_user(message.from_user.id)
    await message.answer("Пауза. Ничего не присылаю.", reply_markup=main_keyboard(user))


@router.message(F.text == BTN_RESUME)
@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    _user(message)
    _storage.update_user(message.from_user.id, active=1)
    user = _storage.get_user(message.from_user.id)
    await message.answer("Подписка снова активна.", reply_markup=main_keyboard(user))


@router.message(F.text == BTN_MARKET)
@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    from .scoring import compute_median

    counts = _storage.counts()
    rows = _storage.index_summary(_settings.median_window_days, _settings.min_sample)
    lines = ["<b>Что бот знает о рынке</b>",
             f"Объявлений в базе: {counts['total']} (чистых {counts['clean']}), "
             f"моделей {counts['models']}"]
    if rows:
        lines.append("\n<b>Медианы</b>")
        for r in rows[:30]:
            med = compute_median(_storage.market_prices(
                r["model"], r["variant"], _settings.median_window_days))
            unit = "мм" if r["category"] == CATEGORY_WATCH else "ГБ"
            variant = f", {r['variant']} {unit}" if r["variant"] else ""
            lines.append(f"• {html.escape(r['model'])}{variant}: "
                         f"{money(med)} ({r['n']} объявл.)")
    else:
        lines.append(f"\nМедиан пока нет: нужно минимум {_settings.min_sample} "
                     "чистых объявлений на модель.")
    await message.answer("\n".join(lines), reply_markup=main_keyboard(_user(message)))


@router.message(Command("doctor"))
async def cmd_doctor(message: Message) -> None:
    """Проверка прямо с хостинга: пролезаем ли к Авито и жива ли база."""
    if _settings.admin_id and message.from_user.id != _settings.admin_id:
        return
    if _client is None:
        await message.answer("Диагностика недоступна: клиент Авито не инициализирован.")
        return

    from .diagnostics import report

    notice = await message.answer("Проверяю…")
    try:
        text, _code = await report(_client, _storage, _settings)
    except Exception as exc:  # noqa: BLE001
        text = f"Диагностика упала: {exc}"
    await notice.edit_text(text, disable_web_page_preview=True)
