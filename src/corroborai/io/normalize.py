"""Normalisation des valeurs avant comparaison.

Principe : une différence de *format* n'est pas un écart. Ces fonctions sont
pures et sans effet de bord ; elles ne modifient jamais les données d'origine.

Chaque fonction :
* retourne ``None`` pour une valeur vide (voir :func:`is_empty`) ;
* lève :class:`NormalizationError` si la valeur ne peut pas être interprétée
  (le moteur transformera cela en verdict ``INDETERMINE`` traçable, plutôt que
  de masquer le problème).
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable

EMPTY_TOKENS = frozenset({"", "nan", "nat", "none", "null", "<na>", "n/a", "-"})
TRUE_TOKENS = frozenset({"oui", "o", "true", "vrai", "1", "y", "yes", "t"})
FALSE_TOKENS = frozenset({"non", "n", "false", "faux", "0", "no", "f"})
MOJIBAKE_MARKERS = ("Ã", "Â", "â€")
EXCEL_EPOCH = date(1899, 12, 30)

_WS = re.compile(r"\s+")
_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d",
    "%d/%m/%Y",
)


class NormalizationError(ValueError):
    """La valeur ne peut pas être interprétée dans le type demandé."""


class FieldKind(str, Enum):
    TEXT = "text"              # texte exact (après nettoyage d'espaces et d'encodage)
    TEXT_LOOSE = "text_loose"  # texte sans accents, insensible à la casse
    CODE = "code"              # identifiant ; zéros de tête et « .0 » ignorés si numérique
    DATE = "date"
    BOOL = "bool"
    NUMBER = "number"


# --------------------------------------------------------------------------- vide

def is_empty(value: Any) -> bool:
    if value is None:
        return True
    try:
        import pandas as pd  # import local : le module reste utilisable sans pandas

        if value is pd.NA or value is pd.NaT:
            return True
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in EMPTY_TOKENS:
        return True
    return False


# --------------------------------------------------------------------------- texte

def fix_mojibake(text: str) -> str:
    """Répare un texte UTF-8 décodé à tort en Latin-1/CP1252 (ex. « complÃ¨te »).

    La réparation n'est retenue que si elle réduit le nombre de marqueurs
    suspects ; sinon le texte est retourné tel quel.
    """
    if not any(m in text for m in MOJIBAKE_MARKERS):
        return text
    best, best_score = text, _mojibake_score(text)
    for enc in ("cp1252", "latin-1"):
        try:
            candidate = text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        score = _mojibake_score(candidate)
        if score < best_score:
            best, best_score = candidate, score
    return best


def _mojibake_score(text: str) -> int:
    return sum(text.count(m) for m in MOJIBAKE_MARKERS)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def norm_text(value: Any) -> str | None:
    if is_empty(value):
        return None
    text = fix_mojibake(str(value))
    text = unicodedata.normalize("NFC", text)
    return _WS.sub(" ", text).strip()


def norm_text_loose(value: Any) -> str | None:
    text = norm_text(value)
    return None if text is None else strip_accents(text).casefold()


# --------------------------------------------------------------------------- codes et nombres

def norm_number(value: Any) -> Decimal | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        raise NormalizationError(f"Booléen inattendu pour un nombre : {value!r}")
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise NormalizationError(f"Nombre invalide : {value!r}") from exc
    if not d.is_finite():
        raise NormalizationError(f"Nombre non fini : {value!r}")
    # 40 == 40.0 == 40.00 ; 7.2 reste 7.2
    return d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()


def norm_code(value: Any) -> str | None:
    """Normalise un identifiant.

    Numérique entier : zéros de tête et partie décimale nulle retirés
    (``"00397"`` → ``"397"``, ``"703.0"`` → ``"703"``). Sinon : texte nettoyé,
    casse conservée (une différence de casse dans un code reste visible).
    """
    text = norm_text(value)
    if text is None:
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return text
    if d.is_finite() and d == d.to_integral_value():
        return str(int(d))
    return text


# --------------------------------------------------------------------------- booléens

def norm_bool(value: Any) -> bool | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    raise NormalizationError(f"Booléen non reconnu : {value!r}")


# --------------------------------------------------------------------------- dates

def excel_serial_to_date(value: Any) -> date | None:
    """Convertit un numéro de série Excel (système 1900) en date."""
    if is_empty(value):
        return None
    try:
        serial = float(str(value).strip())
    except ValueError as exc:
        raise NormalizationError(f"Numéro de série Excel invalide : {value!r}") from exc
    if not (1 <= serial < 2958466):  # 9999-12-31
        raise NormalizationError(f"Numéro de série Excel hors plage : {value!r}")
    return EXCEL_EPOCH + timedelta(days=int(serial))


def norm_date(value: Any) -> date | None:
    """Normalise une date (heure ignorée).

    Accepte ``date``, ``datetime``/``Timestamp`` et les chaînes ISO, y compris
    le format ``2003-12-12T00:00:00.000Z`` du système cible. Les numéros de
    série Excel ne sont **pas** devinés ici (ambiguïté avec un code) : utiliser
    :func:`excel_serial_to_date` explicitement.
    """
    if is_empty(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    text = re.sub(r"([+-]\d{2}:?\d{2})$", "", text) if "T" in text else text
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise NormalizationError(f"Date non reconnue : {value!r}")


# --------------------------------------------------------------------------- répartiteur

_NORMALIZERS: dict[FieldKind, Callable[[Any], Any]] = {
    FieldKind.TEXT: norm_text,
    FieldKind.TEXT_LOOSE: norm_text_loose,
    FieldKind.CODE: norm_code,
    FieldKind.DATE: norm_date,
    FieldKind.BOOL: norm_bool,
    FieldKind.NUMBER: norm_number,
}


def normalize(value: Any, kind: FieldKind | str) -> Any:
    return _NORMALIZERS[FieldKind(kind)](value)
