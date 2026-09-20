"""Оценка объявления: сравнение с медианой рынка и отсев приманок.

Смысл всего бота живёт здесь. Просто «самое дешёвое» — это всегда либо
запчасти, либо приманка, поэтому цена сравнивается с медианой по связке
модель+вариант (память для iPhone, размер корпуса для часов), а слишком
низкая цена трактуется как повод насторожиться, а не как находка.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import List, Optional, Sequence

from .normalize import ParsedListing

VERDICT_SEND = "send"       # годится в рассылку
VERDICT_RISKY = "risky"     # цена слишком хороша, показываем отдельно и с предупреждением
VERDICT_SKIP = "skip"       # не показываем


@dataclass
class Verdict:
    verdict: str
    discount: float = 0.0          # доля ниже медианы, 0.23 = на 23% дешевле
    median_price: Optional[int] = None
    sample_size: int = 0
    score: float = 0.0             # интегральная оценка для сортировки
    reasons: List[str] = field(default_factory=list)

    @property
    def should_notify(self) -> bool:
        return self.verdict in (VERDICT_SEND, VERDICT_RISKY)

    def saving(self, price: int) -> Optional[int]:
        """Сколько рублей экономии относительно медианы."""
        if self.median_price is None:
            return None
        return max(0, self.median_price - price)


def compute_median(prices: Sequence[int]) -> Optional[int]:
    """Медиана по чистым объявлениям. Хвосты обрезаем — они портят ориентир."""
    values = sorted(p for p in prices if p and p > 0)
    if not values:
        return None
    if len(values) >= 10:
        cut = max(1, len(values) // 10)
        values = values[cut:-cut] or values
    return int(median(values))


def quality_score(
    parsed: ParsedListing,
    discount: float,
    battery: Optional[int],
    seller_rating: Optional[float],
    seller_reviews: int,
    photos: int,
    description_length: int,
) -> float:
    """0..100. Скидка весит больше всего, остальное — признаки добросовестности."""
    score = 0.0
    score += min(discount, 0.45) / 0.45 * 55          # до 55 за цену
    if battery is not None:
        score += max(0.0, min((battery - 80) / 20, 1.0)) * 12   # до 12 за АКБ
    elif parsed.model:
        score += 3                                     # не указал — не штрафуем жёстко
    if seller_rating is not None:
        score += max(0.0, min((seller_rating - 4.0) / 1.0, 1.0)) * 12
    score += min(seller_reviews, 20) / 20 * 8
    score += min(photos, 6) / 6 * 8
    score += min(description_length, 400) / 400 * 5
    return round(min(score, 100.0), 1)


def evaluate(
    parsed: ParsedListing,
    price: int,
    market_prices: Sequence[int],
    *,
    price_min: int,
    price_max: int,
    min_discount: float,
    scam_floor: float,
    min_sample: int,
    min_battery: int,
    seller_rating: Optional[float] = None,
    seller_reviews: int = 0,
    photos: int = 0,
    description_length: int = 0,
) -> Verdict:
    """Решает, показывать объявление пользователю."""
    reasons: List[str] = []

    if not parsed.model:
        return Verdict(VERDICT_SKIP, reasons=["модель не распознана"])
    if parsed.flags:
        return Verdict(VERDICT_SKIP, reasons=list(dict.fromkeys(parsed.flag_reasons)))
    if not price or price <= 0:
        return Verdict(VERDICT_SKIP, reasons=["цена не указана"])
    if price < price_min or price > price_max:
        return Verdict(VERDICT_SKIP, reasons=[f"вне бюджета: {price:,} ₽".replace(",", " ")])
    if min_battery and parsed.battery is not None and parsed.battery < min_battery:
        return Verdict(VERDICT_SKIP, reasons=[f"АКБ {parsed.battery}% ниже порога {min_battery}%"])

    sample_size = len(market_prices)
    med = compute_median(market_prices)
    if med is None or sample_size < min_sample:
        return Verdict(
            VERDICT_SKIP, median_price=med, sample_size=sample_size,
            reasons=[f"мало данных для медианы: {sample_size} из {min_sample}"],
        )

    discount = 1.0 - price / med
    ratio = price / med

    score = quality_score(
        parsed, discount, parsed.battery, seller_rating,
        seller_reviews, photos, description_length,
    )

    if ratio < scam_floor:
        reasons.append(
            f"дешевле медианы на {discount:.0%} — так не продают, вероятна приманка"
        )
        return Verdict(VERDICT_RISKY, discount, med, sample_size, score, reasons)

    if discount < min_discount:
        return Verdict(
            VERDICT_SKIP, discount, med, sample_size, score,
            [f"скидка {discount:.0%} меньше порога {min_discount:.0%}"],
        )

    reasons.append(f"на {discount:.0%} ниже медианы {med:,} ₽".replace(",", " "))
    if parsed.battery is not None and parsed.battery >= 90:
        reasons.append(f"АКБ {parsed.battery}%")
    if seller_rating is not None and seller_rating >= 4.7 and seller_reviews >= 5:
        reasons.append(f"продавец {seller_rating:.1f} ({seller_reviews} отз.)")

    return Verdict(VERDICT_SEND, discount, med, sample_size, score, reasons)
