"""Второй источник данных: письма Авито о новых объявлениях по сохранённому поиску.

Антибот Авито режет хостинговые адреса независимо от страны, поэтому прямой
парсинг без доверенного IP невозможен. Письма обходят это принципиально:
Авито присылает их сам, блокировать тут нечего. Данных меньше, чем в выдаче
(описания нет), но заголовка, цены и ссылки хватает, чтобы посчитать медиану
и отсеять мусор.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
from email.header import decode_header, make_header
from html.parser import HTMLParser
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Ссылка на объявление: путь заканчивается идентификатором из 8-12 цифр
ITEM_URL = re.compile(r"https?://(?:www\.|m\.)?avito\.ru/[^\s\"'<>]*?_(\d{8,12})")
PRICE = re.compile(r"(\d[\d\s ]{2,})\s*(?:₽|руб)")
# Подпись ссылки, состоящая только из цены: заголовок надо искать рядом,
# иначе моделью станет «125 000 ₽» и объявление молча выпадет из анализа.
PRICE_ONLY = re.compile(r"^[\d\s\u00a0]+(?:₽|руб\.?)?$")


class _Collector(HTMLParser):
    """Собирает поток из ссылок и текста в порядке появления в письме."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: List[tuple] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.tokens.append(("link", self._href, " ".join(self._text).strip()))
            self._href, self._text = None, []

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._href is not None:
            self._text.append(text)
        else:
            self.tokens.append(("text", "", text))


def _clean_price(raw: str) -> int:
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else 0


def parse_email_html(html: str) -> List[Dict]:
    """Вытаскивает объявления из письма Авито.

    Цену ищем в самой ссылке и в тексте сразу после неё — в письмах она
    стоит либо в подписи ссылки, либо отдельной строкой под заголовком.
    """
    collector = _Collector()
    collector.feed(html)
    tokens = collector.tokens

    found: Dict[str, Dict] = {}
    for index, token in enumerate(tokens):
        if token[0] != "link":
            continue
        _, href, text = token
        match = ITEM_URL.search(href)
        if not match:
            continue
        item_id = match.group(1)

        # Заголовок: текст ссылки, если он осмысленный и это не сама цена
        title = text if len(text) > 8 and not PRICE_ONLY.match(text.strip()) else ""

        # Цена: сначала в тексте ссылки, потом в трёх следующих фрагментах
        window = [text] + [t[2] for t in tokens[index + 1: index + 4]]
        price = 0
        for chunk in window:
            price_match = PRICE.search(chunk)
            if price_match:
                price = _clean_price(price_match.group(1))
                break

        # Заголовок мог остаться пустым — берём предыдущий текстовый фрагмент
        if not title:
            for previous in reversed(tokens[max(0, index - 3): index]):
                if previous[0] == "text" and len(previous[2]) > 8:
                    title = previous[2]
                    break

        if not title:
            continue

        existing = found.get(item_id)
        if existing and existing["price"] and not price:
            continue
        found[item_id] = {
            "id": item_id,
            "title": title,
            "price": price,
            "url": match.group(0),
            "image": None,
            "region": "",
            "description": "",
            "seller_rating": None,
            "seller_reviews": 0,
            "photos": 0,
        }

    return [item for item in found.values() if item["price"] > 0]


def _decode(raw) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:  # noqa: BLE001
        return str(raw or "")


class Mailbox:
    """Читает непрочитанные письма Авито по IMAP."""

    def __init__(self, host: str, user: str, password: str,
                 folder: str = "INBOX", sender: str = "avito.ru", port: int = 993):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.folder, self.sender = folder, sender

    def _read_sync(self) -> List[Dict]:
        items: List[Dict] = []
        connection = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            connection.login(self.user, self.password)
            connection.select(self.folder)
            status, data = connection.search(None, "UNSEEN", "FROM", f'"{self.sender}"')
            if status != "OK":
                return items

            for num in data[0].split():
                status, payload = connection.fetch(num, "(RFC822)")
                if status != "OK" or not payload or not payload[0]:
                    continue
                message = email.message_from_bytes(payload[0][1])
                subject = _decode(message.get("Subject"))

                html = ""
                for part in message.walk():
                    if part.get_content_type() == "text/html":
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            html += part.get_payload(decode=True).decode(charset, "replace")
                        except Exception:  # noqa: BLE001
                            continue

                parsed = parse_email_html(html)
                log.info("Письмо «%s»: объявлений %s", subject[:60], len(parsed))
                items.extend(parsed)
                connection.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                connection.logout()
            except Exception:  # noqa: BLE001
                pass
        return items

    def _probe_sync(self) -> str:
        """Проверяет вход и считает непрочитанные письма Авито, ничего не помечая."""
        connection = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            connection.login(self.user, self.password)
            status, _ = connection.select(self.folder, readonly=True)
            if status != "OK":
                return f"папка {self.folder} не открывается"
            status, data = connection.search(None, "UNSEEN", "FROM", f'"{self.sender}"')
            if status != "OK":
                return "поиск по папке не удался"
            count = len(data[0].split()) if data and data[0] else 0
            return f"вход выполнен, непрочитанных писем от {self.sender}: {count}"
        finally:
            try:
                connection.logout()
            except Exception:  # noqa: BLE001
                pass

    async def probe(self) -> str:
        return await asyncio.to_thread(self._probe_sync)

    async def fetch_items(self) -> List[Dict]:
        """Непрочитанные письма от Авито, разобранные в объявления."""
        return await asyncio.to_thread(self._read_sync)
