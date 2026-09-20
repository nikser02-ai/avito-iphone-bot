"""Разбор объявления Авито: категория, модель, вариант, состояние АКБ, красные флаги.

Поддерживаются две категории техники:
  * iPhone      — вариант это объём памяти в ГБ
  * Apple Watch — вариант это размер корпуса в мм

Весь модуль — чистые функции без сети, поэтому проверяется тестами офлайн.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CATEGORY_IPHONE = "iphone"
CATEGORY_WATCH = "watch"

CATEGORY_TITLES = {
    CATEGORY_IPHONE: "iPhone",
    CATEGORY_WATCH: "Apple Watch",
}

# ---------------------------------------------------------------- подготовка

_RU_TOKENS = [
    ("про максимум", "pro max"), ("про макс", "pro max"), ("промакс", "pro max"),
    ("про", "pro"), ("макс", "max"), ("плюс", "plus"), ("мини", "mini"),
    ("эйр", "air"), ("айр", "air"), ("айфон", "iphone"),
    ("эпл вотч", "apple watch"), ("эппл вотч", "apple watch"),
    ("эпл вотс", "apple watch"), ("вотч", "watch"), ("вотс", "watch"),
    ("ультра", "ultra"), ("серия", "series"), ("серии", "series"),
]


def prepare(text: str) -> str:
    """Единый вид: нижний регистр, ё→е, кириллица моделей→латиница."""
    t = (text or "").lower().replace("ё", "е")
    t = t.replace(" ", " ")
    for ru, en in _RU_TOKENS:
        t = t.replace(ru, en)
    t = re.sub(r"(\d)([a-zа-я])", r"\1 \2", t)   # 13pro → 13 pro, 45мм → 45 мм
    t = re.sub(r"[^\w%+]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- iPhone

_IPHONE_PATTERNS: List[Tuple[str, str]] = []


def _iphone(name: str, pattern: str) -> None:
    _IPHONE_PATTERNS.append((name, pattern))


for _num in (17, 16, 15, 14, 13, 12, 11):
    _iphone(f"iPhone {_num} Pro Max", rf"{_num}\b\s*pro\s*max")
    _iphone(f"iPhone {_num} Pro", rf"{_num}\b\s*pro")
    if _num in (14, 15, 16, 17):
        _iphone(f"iPhone {_num} Plus", rf"{_num}\b\s*plus")
    if _num in (12, 13):
        _iphone(f"iPhone {_num} mini", rf"{_num}\b\s*mini")
    if _num == 16:
        _iphone("iPhone 16e", r"16\s*e\b")
    _iphone(f"iPhone {_num}", rf"{_num}\b")

_iphone("iPhone Air", r"air\b")
_iphone("iPhone XS Max", r"xs\s*max\b")
_iphone("iPhone XS", r"xs\b")
_iphone("iPhone XR", r"xr\b")
_iphone("iPhone X", r"x\b")
_iphone("iPhone SE 2022", r"se\s*(?:3|2022)")
_iphone("iPhone SE 2020", r"se\s*(?:2|2020)")
_iphone("iPhone SE", r"se")
for _num in (8, 7, 6):
    if _num == 6:
        _iphone("iPhone 6s Plus", r"6\s*s\s*plus")
        _iphone("iPhone 6s", r"6\s*s\b")
    _iphone(f"iPhone {_num} Plus", rf"{_num}\b\s*plus")
    _iphone(f"iPhone {_num}", rf"{_num}\b")

_IPHONE_COMPILED = [(n, re.compile(r"^\s*" + p)) for n, p in _IPHONE_PATTERNS]
_IPHONE_ANCHOR = re.compile(r"iphone")


def parse_iphone(text: str) -> Optional[str]:
    """Модель iPhone. Якорится на слово iphone, иначе цена из описания
    («отдам за 15 000») подменит модель."""
    t = prepare(text)
    for anchor in _IPHONE_ANCHOR.finditer(t):
        tail = t[anchor.end(): anchor.end() + 24]
        for name, rx in _IPHONE_COMPILED:
            if rx.match(tail):
                return name
    return None


# ---------------------------------------------------------------- Apple Watch

_WATCH_ANCHOR = re.compile(r"watch")
_WATCH_SPECIAL = [
    ("Apple Watch Ultra 3", re.compile(r"ultra\s*3\b")),
    ("Apple Watch Ultra 2", re.compile(r"ultra\s*2\b")),
    ("Apple Watch Ultra", re.compile(r"ultra\b")),
    ("Apple Watch SE 3", re.compile(r"se\s*(?:3|2025)\b")),
    ("Apple Watch SE 2", re.compile(r"se\s*(?:2|2022)\b")),
    ("Apple Watch SE", re.compile(r"\bse\b")),
]
# "series 9", "серия 9", "s9" — все три формы встречаются в заголовках
_WATCH_SERIES = re.compile(r"(?:series|s)\s*(\d{1,2})\b")
_WATCH_SERIES_POST = re.compile(r"\b(\d{1,2})\s*series\b")


def parse_watch(text: str) -> Optional[str]:
    """Модель Apple Watch. Ultra и SE проверяются раньше номерных серий."""
    t = prepare(text)
    if not _WATCH_ANCHOR.search(t):
        return None

    for name, rx in _WATCH_SPECIAL:
        if rx.search(t):
            return name

    for rx in (_WATCH_SERIES, _WATCH_SERIES_POST):
        for m in rx.finditer(t):
            number = int(m.group(1))
            if 1 <= number <= 11:
                return f"Apple Watch Series {number}"

    # Голый номер сразу после слова watch: "apple watch 8 45mm"
    for anchor in _WATCH_ANCHOR.finditer(t):
        m = re.match(r"\s*(\d{1,2})\b", t[anchor.end(): anchor.end() + 12])
        if m and 1 <= int(m.group(1)) <= 11:
            return f"Apple Watch Series {m.group(1)}"
    return None


# ---------------------------------------------------------------- вариант

_STORAGE_VALUES = {16, 32, 64, 128, 256, 512, 1024, 2048}
_STORAGE_RX = re.compile(r"\b(\d{1,4})\s*(гб|gb|тб|tb|g\b|t\b)")

_SIZE_VALUES = {38, 40, 41, 42, 44, 45, 46, 49}
_SIZE_RX = re.compile(r"\b(\d{2})\s*(мм|mm)\b")


def parse_storage(text: str) -> Optional[int]:
    """Объём накопителя в ГБ, терабайты приводятся к ГБ."""
    t = prepare(text)
    for m in _STORAGE_RX.finditer(t):
        value = int(m.group(1))
        if m.group(2) in ("тб", "tb", "t"):
            value *= 1024
        if value in _STORAGE_VALUES:
            return value
    m = re.search(r"iphone[^\d]{0,12}\d{1,2}\s*(?:pro|max|plus|mini|e)?\s*(\d{2,4})\b", t)
    if m and int(m.group(1)) in _STORAGE_VALUES:
        return int(m.group(1))
    return None


def parse_case_size(text: str) -> Optional[int]:
    """Размер корпуса часов в мм."""
    t = prepare(text)
    m = _SIZE_RX.search(t)
    if m and int(m.group(1)) in _SIZE_VALUES:
        return int(m.group(1))
    for m in re.finditer(r"\b(\d{2})\b", t):
        if int(m.group(1)) in _SIZE_VALUES:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------- аккумулятор

_BATTERY_RX = [
    re.compile(r"(?:акб|аккум\w*|батар\w*|battery|health|емкость)\D{0,18}(\d{2,3})\s*%"),
    re.compile(r"(\d{2,3})\s*%\D{0,18}(?:акб|аккум\w*|батар\w*|battery|health)"),
]


def parse_battery(text: str) -> Optional[int]:
    t = prepare(text)
    for rx in _BATTERY_RX:
        m = rx.search(t)
        if m:
            value = int(m.group(1))
            if 1 <= value <= 100:
                return value
    return None


# ---------------------------------------------------------------- красные флаги

# scope="before" — слово должно стоять в заголовке ПЕРЕД названием техники.
# «Чехол для iPhone 15» — аксессуар, «iPhone 15, чехол в подарок» — телефон.
# scope="full" — ищем во всём тексте, включая описание.
_FLAG_RULES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("not_a_phone", "аксессуар или запчасть, а не товар", "before", (
        "чехол", "стекло", "бампер", "накладка", "кабель", "зарядк", "адаптер",
        "наушник", "airpods", "дисплей", "экран", "аккумулятор для", "акб для",
        "корпус", "плата", "шлейф", "камера для", "задняя крышка", "коробка от",
        "муляж", "макет", "ремешок", "ремешки", "браслет", "ремень для",
        "док станция", "подставка", "пленка", "пленки",
    )),
    ("service", "услуга, а не товар", "before", (
        "ремонт", "диагностик", "прошивк", "разблокировк", "перепрошив",
        "аренда", "прокат", "выкуп", "скупка", "куплю", "обмен на",
    )),
    ("broken", "неисправен или на запчасти", "full", (
        "на запчаст", "на детал", "не включается", "не работает", "разбит",
        "треснут", "трещин", "битое стекло", "утоплен", "нерабоч",
        "под восстановление", "под востановление", "донор",
    )),
    ("fake", "реплика или неоригинал", "full", (
        "реплика", "копия", "1в1", "аналог", "не оригинал", "неоригинал",
        "китайск", "люкс копия", "премиум копия",
    )),
    ("locked", "привязка iCloud или блокировка", "full", (
        "icloud", "айклауд", "залочен", "заблокирован", "блокировка активации",
        "на запросе активации", "activation lock", "неверлок", "залок",
    )),
    ("prepay", "схема с предоплатой", "full", (
        "предоплат", "отправлю после оплаты", "переведите",
    )),
    ("price_bait", "цена не за товар", "full", (
        "цена при обмене", "цена за корпус", "цена указана за", "рассрочк",
        "кредит", "trade in", "трейд ин",
    )),
]


@dataclass
class ParsedListing:
    """Результат разбора одного объявления."""
    category: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[int] = None          # ГБ для iPhone, мм для Watch
    battery: Optional[int] = None
    flags: List[str] = field(default_factory=list)
    flag_reasons: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return bool(self.model) and not self.flags

    @property
    def variant_label(self) -> str:
        if self.variant is None:
            return ""
        if self.category == CATEGORY_WATCH:
            return f"{self.variant} мм"
        if self.variant >= 1024:
            return f"{self.variant // 1024} ТБ"
        return f"{self.variant} ГБ"

    @property
    def key(self) -> Optional[str]:
        """Ключ ценового индекса: модель + вариант."""
        if not self.model:
            return None
        return f"{self.model}|{self.variant if self.variant is not None else '?'}"


def parse(title: str, description: str = "") -> ParsedListing:
    """Разбирает объявление. Модель и вариант — из заголовка, флаги — по своим областям."""
    head = title or ""
    full = f"{head} {description or ''}"
    prepared_title, prepared_full = prepare(head), prepare(full)

    # Позиция названия техники в заголовке — граница между «аксессуар для X» и «X с аксессуаром»
    anchor_at = len(prepared_title)
    for word in ("iphone", "watch"):
        pos = prepared_title.find(word)
        if pos != -1:
            anchor_at = min(anchor_at, pos)

    result = ParsedListing()

    watch_model = parse_watch(head) or parse_watch(full)
    if watch_model:
        result.category = CATEGORY_WATCH
        result.model = watch_model
        result.variant = parse_case_size(head) or parse_case_size(full)
    else:
        iphone_model = parse_iphone(head) or parse_iphone(full)
        if iphone_model:
            result.category = CATEGORY_IPHONE
            result.model = iphone_model
            result.variant = parse_storage(head) or parse_storage(full)

    result.battery = parse_battery(full)

    for code, reason, scope, needles in _FLAG_RULES:
        for needle in needles:
            prepared_needle = prepare(needle)
            if scope == "before":
                pos = prepared_title.find(prepared_needle)
                hit = pos != -1 and pos < anchor_at
            else:
                hit = prepared_needle in prepared_full
            if hit:
                result.flags.append(code)
                result.flag_reasons.append(reason)
                break

    return result
