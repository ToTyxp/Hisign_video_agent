"""Unicode-aware deterministic phrase matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping


_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    cleaned = "".join(
        character
        if not unicodedata.category(character).startswith(("P", "S", "C"))
        else " "
        for character in without_marks
    )
    return _WHITESPACE.sub(" ", cleaned).strip()


def contains_term(text: str, term: str) -> bool:
    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    if not normalized_text or not normalized_term:
        return False
    if _contains_cjk(normalized_term):
        return normalized_term in normalized_text
    return re.search(
        rf"(?<!\w){re.escape(normalized_term)}(?!\w)",
        normalized_text,
        flags=re.UNICODE,
    ) is not None


def find_matches(
    fields: Mapping[str, str],
    terms: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched_fields: list[str] = []
    matched_terms: list[str] = []
    for field_name, text in fields.items():
        for term in terms:
            if contains_term(text, term):
                if field_name not in matched_fields:
                    matched_fields.append(field_name)
                if term not in matched_terms:
                    matched_terms.append(term)
    return tuple(matched_fields), tuple(matched_terms)


def field_has_conjunction(text: str, left_terms: Iterable[str], right_terms: Iterable[str]) -> bool:
    return any(contains_term(text, term) for term in left_terms) and any(
        contains_term(text, term) for term in right_terms
    )


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)
