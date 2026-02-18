# utils/request_parsing.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Mapping

# Участник: @telegram или +телефон (6–15 цифр)
_PART_RE = re.compile(r"^(?:@[A-Za-z0-9_]{2,}|\+\d{6,15})$")


@dataclass(frozen=True, slots=True)
class ParsedRequest:
    cmd: str
    kind: str                 # "dep" | "wd" | "fx"
    city: str

    # dep/wd
    amount_expr: str = ""
    code: str = ""

    # fx
    in_code: str = ""
    out_code: str = ""
    amt_in_expr: str = ""
    amt_out_expr: str = ""

    # common
    contact1: str = ""        # "Принимает(наш контакт)" для dep/fx, "Выдает" для wd (семантика задаётся отдельно)
    contact2: str = ""
    comment: str = ""


def _split_comment(tail: str) -> tuple[str, str]:
    before = (tail or "").strip()
    if "!" not in before:
        return before, ""
    a, b = before.split("!", 1)
    return a.strip(), b.strip()


def _pick_city(tokens: list[str], *, city_keys: set[str], default_city: str) -> tuple[str, list[str]]:
    city = (default_city or "екб").strip().lower()
    if tokens and tokens[0].strip().lower() in city_keys:
        city = tokens[0].strip().lower()
        tokens = tokens[1:]
    return city, tokens


def _pick_contacts(tokens: list[str]) -> tuple[str, str, list[str]]:
    """
    Снимаем контакты с конца (1 или 2).
    Возвращает (contact1, contact2, rest_tokens_without_contacts)
    """
    if not tokens:
        return "", "", tokens

    if not _PART_RE.match(tokens[-1]):
        return "", "", tokens

    if len(tokens) >= 2 and _PART_RE.match(tokens[-2]):
        return tokens[-2].strip(), tokens[-1].strip(), tokens[:-2]
    return tokens[-1].strip(), "", tokens[:-1]


def parse_dep_wd(
    raw_text: str,
    *,
    cmd_map: Mapping[str, tuple[str, str]],   # CMD_MAP
    city_keys: set[str],
    default_city: str,
) -> Optional[ParsedRequest]:
    """
    /депр [город] <amount_expr> <contact1> [contact2] [! comment]
    /выдр [город] <amount_expr> <contact1> [contact2] [! comment]
    """
    text = (raw_text or "").strip()
    if not text.startswith("/"):
        return None

    first, *rest = text.split(maxsplit=1)
    cmd = first[1:].split("@", 1)[0].lower()
    if cmd not in cmd_map:
        return None

    tail = rest[0] if rest else ""
    if not tail.strip():
        return None

    before_comment, comment = _split_comment(tail)
    tokens = before_comment.split()
    if len(tokens) < 2:
        return None

    city, tokens = _pick_city(tokens, city_keys=city_keys, default_city=default_city)

    contact1, contact2, core = _pick_contacts(tokens)
    if not contact1:
        return None
    amount_expr = " ".join(core).strip()
    if not amount_expr:
        return None

    kind, code = cmd_map[cmd]
    return ParsedRequest(
        cmd=cmd,
        kind=kind,
        city=city,
        amount_expr=amount_expr,
        code=str(code).upper(),
        contact1=contact1,
        contact2=contact2,
        comment=comment,
    )


def parse_fx(
    raw_text: str,
    *,
    fx_cmd_map: Mapping[str, tuple[str, str, str]],  # {"првд": ("fx","RUB","USD"), ...}
    city_keys: set[str],
    default_city: str,
) -> Optional[ParsedRequest]:
    """
    /првд [город] <amt_in_expr> <amt_out_expr> <contact1> [contact2] [! comment]

    Город опционален. Если не указан — берём default_city.

    ВАЖНО: amt_in_expr и amt_out_expr — по одному токену каждый (без пробелов внутри).
    Пример: /првд (700+300) 1000 +7999... ! коммент
    """
    text = (raw_text or "").strip()
    if not text.startswith("/"):
        return None

    first, *rest = text.split(maxsplit=1)
    cmd = first[1:].split("@", 1)[0].lower()
    if cmd not in fx_cmd_map:
        return None

    tail = rest[0] if rest else ""
    if not tail.strip():
        return None

    before_comment, comment = _split_comment(tail)
    tokens = before_comment.split()
    if not tokens:
        return None

    # город опционально
    city, tokens = _pick_city(tokens, city_keys=city_keys, default_city=default_city)

    # после выкусывания города минимум: amt_in amt_out contact1
    if len(tokens) < 3:
        return None

    # контакты снимаем с конца (1 или 2)
    contact1, contact2, core = _pick_contacts(tokens)
    if not contact1:
        return None

    # core должен быть ровно 2 токена: amt_in amt_out
    if len(core) != 2:
        return None

    amt_in_expr = core[0].strip()
    amt_out_expr = core[1].strip()
    if not amt_in_expr or not amt_out_expr:
        return None

    kind, in_code, out_code = fx_cmd_map[cmd]
    return ParsedRequest(
        cmd=cmd,
        kind=kind,
        city=city,
        in_code=str(in_code).upper(),
        out_code=str(out_code).upper(),
        amt_in_expr=amt_in_expr,
        amt_out_expr=amt_out_expr,
        contact1=contact1,
        contact2=contact2,
        comment=comment,
    )
