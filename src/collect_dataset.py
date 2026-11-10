import argparse
import json
import logging
import math
import random
import re
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SEARCH_URL = 'https://store.steampowered.com/search/results/'
SEARCH_PAGE_SIZE = 50
# Источники выдачи поиска. Чтобы снизить ошибку выжившего, берём не только хиты:
# "topsellers" - бестселлеры, "newreleases" - новинки, "" - общая выдача по релевантности.
SEARCH_FILTERS = ('topsellers', 'newreleases', '')
# Во сколько раз собираем больше appid, чем limit (часть игр отсеется при сборе признаков)
CANDIDATES_OVERSAMPLE = 1.5
# Глубина пагинации: максимум новых кандидатов на один фильтр и страниц на фильтр
MAX_CANDIDATES_PER_FILTER = 500
MAX_PAGES_PER_FILTER = 30

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
APPID_RE = re.compile(r'data-ds-appid="(\d+)"')  # бандлы ("1,2,3") сознательно не матчатся

REQUEST_TIMEOUT = 10
MAX_RETRIES = 5
BACKOFF_BASE = 2.0
RETRY_STATUS_CODES = {403, 429, 500, 502, 503, 504}
PAUSE_RANGE = (1.0, 1.5)

# Значения по умолчанию, если SteamSpy недоступен (игра при этом не отбрасывается)
STEAMSPY_DEFAULTS = {'positive': 0, 'negative': 0, 'owners': 'unknown', 'average_forever': 0}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / 'data' / 'raw' / 'dataset_raw.json'
SAVE_EVERY = 50


class SteamDatasetCollector:
    def __init__(self, limit: int = 100):
        self.limit = limit  # сколько НОВЫХ игр добавить за запуск
        self.dataset: List[Dict[str, Any]] = []
        self.existing_ids: set = set()
        self.logger = self._setup_logger()

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger = logging.getLogger('steam_dataset_collector')
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
            logger.addHandler(handler)
        return logger

    @staticmethod
    def _pause() -> None:
        time.sleep(random.uniform(*PAUSE_RANGE))

    def _request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = MAX_RETRIES,
    ) -> Optional[requests.Response]:
        """GET с таймаутом и exponential backoff на 403/429/5xx, таймаутах и обрывах соединения.

        Возвращает None, если запрос так и не удался.
        """
        for attempt in range(max_retries):
            delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
            try:
                response = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                self.logger.warning(
                    f'Сетевая ошибка ({type(e).__name__}), повтор через {delay:.1f} c '
                    f'[{attempt + 1}/{max_retries}]'
                )
                time.sleep(delay)
                continue
            except requests.exceptions.RequestException as e:
                self.logger.error(f'Ошибка запроса {url}: {e}')
                return None

            if response.status_code in RETRY_STATUS_CODES:
                self.logger.warning(
                    f'HTTP {response.status_code} для {url}, повтор через {delay:.1f} c '
                    f'[{attempt + 1}/{max_retries}]'
                )
                time.sleep(delay)
                continue
            if response.status_code != 200:
                self.logger.error(f'HTTP {response.status_code} для {url}')
                return None
            return response

        self.logger.error(f'Исчерпаны попытки ({max_retries}) для {url}')
        return None

    def _fetch_search_page(self, search_filter: str, offset: int) -> Optional[List[int]]:
        """Одна страница поисковой выдачи; None при ошибке, пустой список - выдача закончилась."""
        params: Dict[str, Any] = {
            'query': '',
            'start': offset,
            'count': SEARCH_PAGE_SIZE,
            'dynamic_data': '',
            'force_infinite': 1,
            'category1': 998,  # только игры (без DLC, ПО, саундтреков)
            'json': 1,
        }
        if search_filter:
            params['filter'] = search_filter

        response = self._request(SEARCH_URL, params=params)
        if response is None:
            return None
        try:
            return self._extract_appids(response.json())
        except (ValueError, KeyError, TypeError) as e:
            self.logger.error(
                f'Неожиданный ответ поиска (filter={search_filter!r}, start={offset}): {e}. '
                f'Ответ: {response.text[:100]}'
            )
            return None

    @staticmethod
    def _extract_appids(payload: dict) -> List[int]:
        import re
        appids = []
        
        # 1. Извлекаем из списка items по ссылке на логотип (наиболее стабильный вариант)
        items = payload.get("items", [])
        for item in items:
            logo = item.get("logo", "")
            match = re.search(r"/apps/(\d+)/", logo)
            if match:
                appids.append(int(match.group(1)))
            elif "id" in item:
                try:
                    appids.append(int(item["id"]))
                except (ValueError, TypeError):
                    pass
        
        # 2. Запасной вариант: если items пуст, ищем в results_html
        if not appids:
            results_html = payload.get("results_html", "")
            found = re.findall(r'data-ds-appid="(\d+)"', results_html)
            appids = [int(aid) for aid in found]
            
        # Убираем дубликаты с сохранением порядка
        return list(dict.fromkeys(appids))

    def get_game_list(self) -> List[int]:
        """Возвращает список НОВЫХ appid-кандидатов (которых ещё нет в датасете).

        Фильтры (бестселлеры, новинки, общая выдача) обходятся по кругом страница за
        страницей. Пагинация идёт глубже одной сотни: на каждый фильтр - до
        MAX_CANDIDATES_PER_FILTER новых appid (и не более MAX_PAGES_PER_FILTER страниц),
        общий сбор останавливается, когда набрано limit * CANDIDATES_OVERSAMPLE
        уникальных кандидатов. Источники чередуются в итоговом списке, чтобы в выборку
        попадали и хиты, и средние/нишевые проекты.
        """
        target_total = math.ceil(self.limit * CANDIDATES_OVERSAMPLE)
        per_filter: Dict[str, List[int]] = {f: [] for f in SEARCH_FILTERS}
        offsets: Dict[str, int] = {f: 0 for f in SEARCH_FILTERS}
        pages: Dict[str, int] = {f: 0 for f in SEARCH_FILTERS}
        active = list(SEARCH_FILTERS)
        unique: set = set()

        while active and len(unique) < target_total:
            for search_filter in list(active):
                if len(unique) >= target_total:
                    break

                page_ids = self._fetch_search_page(search_filter, offsets[search_filter])
                offsets[search_filter] += SEARCH_PAGE_SIZE
                pages[search_filter] += 1

                if not page_ids:  # ошибка или конец выдачи
                    active.remove(search_filter)
                    continue

                bucket = per_filter[search_filter]
                in_bucket = set(bucket)
                for appid in page_ids:
                    if appid in self.existing_ids or appid in in_bucket:
                        continue
                    bucket.append(appid)
                    in_bucket.add(appid)
                    unique.add(appid)

                self.logger.info(
                    f'Поиск [{search_filter or "default"}]: новых appid {len(bucket)}'
                    f'/{MAX_CANDIDATES_PER_FILTER}, всего уникальных {len(unique)}/{target_total}'
                )
                if (len(bucket) >= MAX_CANDIDATES_PER_FILTER
                        or pages[search_filter] >= MAX_PAGES_PER_FILTER):
                    active.remove(search_filter)
                self._pause()

        candidates: List[int] = []
        seen = set()
        for group in zip_longest(*(per_filter[f][:MAX_CANDIDATES_PER_FILTER] for f in SEARCH_FILTERS)):
            for appid in group:
                if appid is not None and appid not in seen:
                    seen.add(appid)
                    candidates.append(appid)

        self.logger.info(f'Всего новых уникальных кандидатов: {len(candidates)}')
        return candidates

    def collect_game_data(self, appid: int) -> Optional[Dict[str, Any]]:
        """Собирает данные по одной игре из Steam Store; SteamSpy необязателен."""
        steam_store_data = self._get_steam_store_data(appid)
        if not steam_store_data:
            return None
        self._pause()
        try:
            steamspy_data = self._get_steamspy_data(appid)
        except Exception as e:  # SteamSpy не должен приводить к потере игры
            self.logger.warning(f'SteamSpy недоступен для appid {appid}: {e}')
            steamspy_data = None
        if not steamspy_data:
            self.logger.warning(f'SteamSpy без данных для appid {appid}, использую значения по умолчанию')
            steamspy_data = {}
        # Недостающие/пустые поля SteamSpy заполняем значениями по умолчанию
        steamspy_data = {
            key: steamspy_data.get(key) if steamspy_data.get(key) is not None else default
            for key, default in STEAMSPY_DEFAULTS.items()
        }
        return {'appid': appid, **steam_store_data, **steamspy_data}

    def _get_steam_store_data(self, appid: int) -> Optional[Dict[str, Any]]:
        """Получает данные из Steam Store API."""
        response = self._request(
            'https://store.steampowered.com/api/appdetails', params={'appids': appid}
        )
        if response is None:
            return None
        try:
            app = response.json()[str(appid)]
            # Берём только игры: DLC, ПО, видео и саундтреки пропускаем
            if app.get('success') and app['data'].get('type') == 'game':
                return self._parse_steam_store_data(app['data'])
        except (ValueError, KeyError, TypeError) as e:
            self.logger.error(
                f'Неожиданный ответ Steam Store API для appid {appid}: {e}. '
                f'Ответ: {response.text[:100]}'
            )
        return None

    @staticmethod
    def _parse_steam_store_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит данные из Steam Store API."""
        price = data.get('price_overview') or {}
        languages = data.get('supported_languages') or ''
        return {
            'name': data.get('name'),
            'release_date': (data.get('release_date') or {}).get('date'),
            'genres': [g['description'] for g in data.get('genres', [])],
            'categories': [c['description'] for c in data.get('categories', [])],
            'is_free': data.get('is_free', False),
            'initial_price': price.get('initial'),
            'final_price': price.get('final'),
            'supported_languages': [lang.strip() for lang in languages.split(',') if lang.strip()],
            'developers': data.get('developers', []),
            'publishers': data.get('publishers', []),
        }

    def _get_steamspy_data(self, appid: int) -> Optional[Dict[str, Any]]:
        """Получает данные из SteamSpy API."""
        response = self._request(
            'https://steamspy.com/api.php',
            params={'request': 'appdetails', 'appid': appid},
            max_retries=3,
        )
        if response is None:
            return None
        try:
            return self._parse_steamspy_data(response.json())
        except (ValueError, AttributeError) as e:
            self.logger.error(
                f'Некорректный ответ SteamSpy для appid {appid}: {e}. Ответ: {response.text[:100]}'
            )
        return None

    @staticmethod
    def _parse_steamspy_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит данные из SteamSpy API."""
        return {
            'positive': data.get('positive'),
            'negative': data.get('negative'),
            'owners': data.get('owners'),
            'average_forever': data.get('average_forever'),
        }

    def load_existing(self) -> None:
        """Подгружает уже собранные игры из dataset_raw.json, чтобы дописывать, а не перезаписывать.

        Если файл есть, но повреждён - бросает ValueError (чтобы не затереть данные).
        """
        if not OUTPUT_PATH.exists():
            return
        try:
            with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f'Не удалось прочитать {OUTPUT_PATH}: {e}') from e
        if not isinstance(data, list):
            raise ValueError(f'Ожидался список в {OUTPUT_PATH}, получен {type(data).__name__}')

        self.dataset = [item for item in data if isinstance(item, dict)]
        for item in self.dataset:
            try:
                self.existing_ids.add(int(item['appid']))
            except (KeyError, TypeError, ValueError):
                continue
        self.logger.info(f'Загружено уже собранных игр: {len(self.dataset)}')

    def save_progress(self) -> None:
        """Сохраняет прогресс (существующие + новые игры) в файл атомарно."""
        tmp_path = OUTPUT_PATH.with_suffix('.json.tmp')
        try:
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.dataset, f, ensure_ascii=False, indent=2)
            tmp_path.replace(OUTPUT_PATH)
            self.logger.info(f'Прогресс сохранен в {OUTPUT_PATH} ({len(self.dataset)} игр)')
        except OSError as e:
            self.logger.error(f'Ошибка при сохранении прогресса: {e}')

    def run(self) -> None:
        """Основной цикл сбора данных: дописывает до self.limit НОВЫХ игр к существующим."""
        try:
            self.load_existing()
        except ValueError as e:
            self.logger.error(f'{e}. Сбор прерван, чтобы не потерять данные')
            return

        candidates = self.get_game_list()
        if not candidates:
            self.logger.error('Не удалось получить список новых игр')
            return

        # Часть кандидатов отсеивается, поэтому идём по списку,
        # пока не наберём self.limit реально собранных НОВЫХ игр
        new_count = 0
        for appid in candidates:
            if new_count >= self.limit:
                break
            if appid in self.existing_ids:
                continue

            game_data = self.collect_game_data(appid)
            if game_data:
                self.dataset.append(game_data)
                self.existing_ids.add(appid)
                new_count += 1
                self.logger.info(
                    f'[{new_count}/{self.limit}] Собрана игра: {game_data.get("name")} '
                    f'(всего в датасете: {len(self.dataset)})'
                )
                if new_count % SAVE_EVERY == 0:
                    self.save_progress()

            self._pause()

        if new_count < self.limit:
            self.logger.warning(
                f'Кандидаты закончились: добавлено {new_count} из {self.limit} новых игр'
            )
        self.save_progress()


def main() -> None:
    parser = argparse.ArgumentParser(description='Сбор данных по играм из Steam')
    parser.add_argument('--limit', type=int, default=100,
                        help='Сколько НОВЫХ игр добавить к существующему датасету (по умолчанию 100)')
    args = parser.parse_args()

    collector = SteamDatasetCollector(limit=args.limit)
    try:
        collector.run()
    except KeyboardInterrupt:
        collector.logger.warning('Прервано пользователем, сохраняю прогресс')
        collector.save_progress()


if __name__ == '__main__':
    main()
