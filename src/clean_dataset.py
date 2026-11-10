"""Предобработка и очистка сырых данных Steam.

Вход:  data/raw/dataset_raw.json        (список словарей по играм)
Выход: data/processed/games_clean.csv   (витрина признаков)
"""
import json
import logging
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = PROJECT_ROOT / 'data' / 'raw' / 'dataset_raw.json'
OUTPUT_PATH = PROJECT_ROOT / 'data' / 'processed' / 'games_clean.csv'

# Цены в Steam API хранятся в минимальных единицах валюты (центах): 2999 -> 29.99.
PRICE_DIVISOR = 100
# Порог аномалии в «центах»: > 20000 (т.е. > 200.00) - явный сбой валюты
# (цены в IDR/KRW/VND и т.п. приходят как 34000000, 192300 и т.д.).
PRICE_ANOMALY_THRESHOLD = 20000
# Верхняя граница итоговой цены после масштабирования (страховка от выбросов).
PRICE_MAX = 200.0

YEAR_RE = re.compile(r'(19\d\d|20\d\d)')
HTML_TAG_RE = re.compile(r'<[^>]*>')
BR_RE = re.compile(r'<br\s*/?>', flags=re.IGNORECASE)

# Подстроки (в нижнем регистре) для поиска в списке categories
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    'is_multiplayer': [
        'multi-player', 'multiplayer', 'multijogador', 'wieloosobow',
        'multijoueur', 'mehrspieler', 'multijugador', 'multigiocatore',
        'мультиплеер', 'многопользовательск', 'для нескольких игроков',
    ],
    'is_singleplayer': [
        'single-player', 'singleplayer', 'jednoosobow', 'solo',
        'um jogador', 'un jogador', 'un joueur', 'einzelspieler',
        'un jugador', 'giocatore singolo', 'одиночн', 'для одного игрока',
    ],
    'is_coop': [
        'co-op', 'coop', 'kooperac', 'cooperativ', 'cooperat',
        'coopératif', 'kooperativ', 'кооператив', 'совместн',
    ],
}

# Подстроки (в нижнем регистре) для поиска в списке genres
GENRE_KEYWORDS: Dict[str, List[str]] = {
    'genre_action': ['action', 'akcja', 'ação', 'acao', 'acción', 'aktion', 'экшен', 'действи'],
    'genre_rpg': ['rpg', 'role-playing', 'role playing', 'ролев'],
    'genre_strategy': ['strateg', 'estratég', 'estrateg', 'стратег'],
    'genre_adventure': ['adventure', 'przygod', 'aventur', 'abenteuer', 'приключен'],
    'genre_indie': ['indie', 'indépendant', 'independ', 'niezależn', 'инди'],
    'genre_simulation': ['simul', 'symulac', 'симулятор', 'симуляц'],
}

# Текущий год для нормализации по возрасту игры
CURRENT_YEAR = 2026
MIN_GAME_AGE_YEARS = 0.5

OUTPUT_COLUMNS = [
    'appid', 'name',
    'total_reviews', 'positive_ratio', 'reviews_per_year',
    'price', 'release_year', 'languages_count',
    'is_multiplayer', 'is_singleplayer', 'is_coop',
    *GENRE_KEYWORDS.keys(),
]

logger = logging.getLogger('clean_dataset')


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def _to_number(value: Any) -> Optional[float]:
    """Безопасно приводит значение к float; None/NaN/мусор -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # отсев NaN


def _to_list(value: Any) -> List[str]:
    """Приводит поле к списку строк (поддерживает list, строку через запятую и None)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.split(',') if part.strip()]
    if isinstance(value, Iterable):
        return [str(item) for item in value if item is not None]
    return []


def _normalize_text(items: List[str]) -> str:
    """Склеивает элементы в одну строку в нижнем регистре (NFC) для поиска подстрок."""
    return unicodedata.normalize('NFC', ' | '.join(items)).casefold()


def _contains_any(text: str, keywords: List[str]) -> int:
    return int(any(keyword in text for keyword in keywords))


# --------------------------------------------------------------------------- #
# Очистка отдельных полей
# --------------------------------------------------------------------------- #
def parse_reviews(record: Dict[str, Any]) -> Dict[str, Any]:
    """total_reviews = positive + negative; positive_ratio = positive / total (0.0 при total == 0)."""
    positive = max(int(_to_number(record.get('positive')) or 0), 0)
    negative = max(int(_to_number(record.get('negative')) or 0), 0)
    total = positive + negative
    ratio = positive / total if total > 0 else 0.0
    return {'total_reviews': total, 'positive_ratio': round(ratio, 6)}


def parse_price(record: Dict[str, Any]) -> float:
    """Цена в основных единицах валюты (центы -> units).

    - is_free == True или цена отсутствует -> 0.0
    - берётся базовая цена (initial_price), т.к. скидки временны; при её отсутствии - final_price
    - аномально большие значения (> 20000 «центов») масштабируются делением на 10
      до разумного диапазона, итог дополнительно ограничивается PRICE_MAX
    """
    if record.get('is_free') is True:
        return 0.0

    raw = _to_number(record.get('initial_price'))
    if raw is None:
        raw = _to_number(record.get('final_price'))
    if raw is None or raw <= 0:
        return 0.0

    while raw > PRICE_ANOMALY_THRESHOLD:
        raw /= 10
    return round(min(raw / PRICE_DIVISOR, PRICE_MAX), 2)


def parse_release_year(value: Any) -> Optional[int]:
    """Год релиза из строки даты на любом языке; None, если год не найден."""
    if not isinstance(value, str):
        return None
    match = YEAR_RE.search(value)
    return int(match.group(1)) if match else None


def count_languages(value: Any) -> int:
    """Количество уникальных поддерживаемых языков без HTML-тегов, звёздочек и подсказок."""
    languages = set()
    for item in _to_list(value):
        # Подсказка вида "Indonesian<br><strong>*</strong>languages with full audio support"
        # отделяется тегом <br> - берём только часть до него.
        name = BR_RE.split(item, maxsplit=1)[0]
        name = HTML_TAG_RE.sub('', name).replace('*', '').strip()
        if not name or 'audio support' in name.casefold():
            continue
        languages.add(name.casefold())
    return len(languages)


def parse_categories(value: Any) -> Dict[str, int]:
    text = _normalize_text(_to_list(value))
    return {col: _contains_any(text, kws) for col, kws in CATEGORY_KEYWORDS.items()}


def parse_genres(value: Any) -> Dict[str, int]:
    text = _normalize_text(_to_list(value))
    return {col: _contains_any(text, kws) for col, kws in GENRE_KEYWORDS.items()}


# --------------------------------------------------------------------------- #
# Основной пайплайн
# --------------------------------------------------------------------------- #
def load_raw(path: Path) -> List[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f'Ожидался список словарей в {path}, получен {type(data).__name__}')
    return [item for item in data if isinstance(item, dict)]


def build_row(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Преобразует одну сырую запись в строку витрины; None, если запись непригодна."""
    appid = _to_number(record.get('appid'))
    name = record.get('name')
    if appid is None or not isinstance(name, str) or not name.strip():
        return None

    return {
        'appid': int(appid),
        'name': name.strip(),
        **parse_reviews(record),
        'price': parse_price(record),
        'release_year': parse_release_year(record.get('release_date')),
        'languages_count': count_languages(record.get('supported_languages')),
        **parse_categories(record.get('categories')),
        **parse_genres(record.get('genres')),
    }


def add_reviews_per_year(df: pd.DataFrame) -> pd.DataFrame:
    """reviews_per_year = total_reviews / max(CURRENT_YEAR - release_year, 0.5)."""
    game_age_years = (CURRENT_YEAR - df['release_year']).clip(lower=MIN_GAME_AGE_YEARS)
    df['reviews_per_year'] = (df['total_reviews'] / game_age_years).round(4)
    return df


def clean_dataset(records: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = [row for row in map(build_row, records) if row is not None]
    dropped = len(records) - len(rows)
    if dropped:
        logger.info('Отброшено записей без appid/названия: %d', dropped)
    if not rows:
        raise ValueError('После очистки не осталось ни одной записи')

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    before = len(df)
    df = df.drop_duplicates(subset='appid', keep='first')
    if len(df) != before:
        logger.info('Удалено дубликатов appid: %d', before - len(df))

    # Пропущенный год релиза -> медиана по датасету
    years = pd.to_numeric(df['release_year'], errors='coerce')
    median_year = years.median()
    if pd.isna(median_year):
        median_year = date.today().year
    df['release_year'] = years.fillna(round(median_year)).astype(int)

    df = add_reviews_per_year(df)

    return df.reset_index(drop=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    # Названия игр содержат произвольный Unicode - защищаем вывод в консоли Windows
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')

    try:
        records = load_raw(RAW_PATH)
    except FileNotFoundError:
        logger.error('Файл с сырыми данными не найден: %s', RAW_PATH)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error('Не удалось прочитать %s: %s', RAW_PATH, e)
        sys.exit(1)

    df = clean_dataset(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, encoding='utf-8', index=False)
    logger.info('Сохранено: %s', OUTPUT_PATH)
    logger.info('Среднее reviews_per_year по датасету: %.2f', df['reviews_per_year'].mean())

    print(f'Размер датасета: {df.shape[0]} строк, {df.shape[1]} столбцов')
    with pd.option_context('display.max_columns', None, 'display.width', 250):
        print(df.head())


if __name__ == '__main__':
    main()
