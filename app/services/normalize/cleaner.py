from __future__ import annotations

import re

from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    normalized = text.replace("\xa0", " ").replace("\u3000", " ").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    return clean_text(text)
