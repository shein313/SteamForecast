"""Streamlit-дашборд Steam Forecast: прогноз потенциала игры по baseline-модели.

Запуск (из корня проекта):
    streamlit run src/app.py

Зависимости: pip install streamlit plotly

Модель: models/baseline_rf.joblib (Pipeline: SimpleImputer -> RandomForestRegressor),
обучена на y = log1p(total_reviews).
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / 'models' / 'baseline_rf.joblib'
DATA_PATH = PROJECT_ROOT / 'data' / 'processed' / 'games_clean.csv'

# Порядок признаков строго совпадает с порядком при обучении модели
FEATURE_ORDER = [
    'price', 'release_year', 'languages_count',
    'is_multiplayer', 'is_singleplayer', 'is_coop',
    'genre_action', 'genre_rpg', 'genre_strategy',
    'genre_adventure', 'genre_indie', 'genre_simulation',
]

# Подписи признаков для интерфейса и графика
FEATURE_LABELS = {
    'price': 'Цена',
    'release_year': 'Год релиза',
    'languages_count': 'Кол-во языков',
    'is_multiplayer': 'Мультиплеер',
    'is_singleplayer': 'Одиночная игра',
    'is_coop': 'Кооператив',
    'genre_action': 'Жанр: Экшен',
    'genre_rpg': 'Жанр: RPG',
    'genre_strategy': 'Жанр: Стратегия',
    'genre_adventure': 'Жанр: Приключения',
    'genre_indie': 'Жанр: Инди',
    'genre_simulation': 'Жанр: Симулятор',
}

GENRES = {
    'Экшен': 'genre_action',
    'RPG': 'genre_rpg',
    'Стратегия': 'genre_strategy',
    'Приключения': 'genre_adventure',
    'Инди': 'genre_indie',
    'Симулятор': 'genre_simulation',
}

# Метод Boxleiter: число проданных копий ≈ число отзывов * коэффициент
BOXLEITER_MULTIPLIER = 30


@st.cache_resource(show_spinner='Загрузка модели...')
def load_model(path: Path):
    """Загружает обученный sklearn-пайплайн (кэшируется между перезапусками)."""
    return joblib.load(path)


@st.cache_data(show_spinner=False)
def load_dataset(path: Path) -> pd.DataFrame | None:
    """Загружает очищенный датасет для справочного контекста."""
    if not path.exists():
        return None
    return pd.read_csv(path)


def get_feature_names(model) -> list[str]:
    """Имена признаков из модели, если она их хранит; иначе порядок из обучения."""
    names = getattr(model, 'feature_names_in_', None)
    if names is None and hasattr(model, 'named_steps'):
        for step in model.named_steps.values():
            names = getattr(step, 'feature_names_in_', None)
            if names is not None:
                break
    return list(names) if names is not None else FEATURE_ORDER


def get_feature_importances(model) -> np.ndarray | None:
    """Достаёт feature_importances_ из пайплайна (named_steps) или самой модели."""
    estimator = model
    if hasattr(model, 'named_steps'):
        estimator = next(
            (s for s in reversed(list(model.named_steps.values()))
             if hasattr(s, 'feature_importances_')),
            None,
        )
    return getattr(estimator, 'feature_importances_', None)


def build_feature_vector(price, release_year, languages_count,
                         is_singleplayer, is_multiplayer, is_coop,
                         selected_genres: list[str], columns: list[str]) -> pd.DataFrame:
    """Собирает одну строку признаков в порядке обучения модели."""
    values = {
        'price': float(price),
        'release_year': float(release_year),
        'languages_count': float(languages_count),
        'is_multiplayer': float(is_multiplayer),
        'is_singleplayer': float(is_singleplayer),
        'is_coop': float(is_coop),
    }
    for genre_label, column in GENRES.items():
        values[column] = float(genre_label in selected_genres)
    return pd.DataFrame([[values[c] for c in columns]], columns=columns)


def format_int(value: int) -> str:
    return f'{value:,}'.replace(',', ' ')


def plot_importances(importances: np.ndarray, columns: list[str]):
    df = pd.DataFrame({
        'Признак': [FEATURE_LABELS.get(c, c) for c in columns],
        'Важность': importances,
    }).sort_values('Важность', ascending=True)
    fig = px.bar(
        df, x='Важность', y='Признак', orientation='h',
        color='Важность', color_continuous_scale='Blues',
        text=df['Важность'].map(lambda v: f'{v:.3f}'),
    )
    fig.update_traces(textposition='outside', cliponaxis=False)
    fig.update_layout(
        height=460,
        coloraxis_showscale=False,
        margin=dict(l=10, r=30, t=10, b=10),
        yaxis_title=None,
    )
    return fig


def render_sidebar():
    """Боковая панель с параметрами игры. Возвращает словарь значений и флаг кнопки."""
    st.sidebar.header('Параметры игры')
    price = st.sidebar.slider('Цена (USD)', 0.0, 70.0, 20.0, step=0.5)
    release_year = st.sidebar.slider('Год релиза', 2024, 2028, 2026)
    languages_count = st.sidebar.slider('Количество языков', 1, 30, 5)

    st.sidebar.subheader('Режимы игры')
    is_singleplayer = st.sidebar.checkbox('Одиночная игра', value=True)
    is_multiplayer = st.sidebar.checkbox('Мультиплеер', value=False)
    is_coop = st.sidebar.checkbox('Кооператив', value=False)

    st.sidebar.subheader('Жанры')
    selected_genres = st.sidebar.multiselect(
        'Жанры игры', options=list(GENRES), default=['Экшен'],
    )

    submitted = st.sidebar.button('Рассчитать потенциал', type='primary',
                                  width='stretch')
    params = dict(
        price=price, release_year=release_year, languages_count=languages_count,
        is_singleplayer=is_singleplayer, is_multiplayer=is_multiplayer,
        is_coop=is_coop, selected_genres=selected_genres,
    )
    return params, submitted


def render_results(model, params: dict, columns: list[str]) -> None:
    X = build_feature_vector(columns=columns, **params)
    log_reviews = float(model.predict(X)[0])
    pred_reviews = max(int(np.expm1(log_reviews)), 0)
    estimated_copies = pred_reviews * BOXLEITER_MULTIPLIER
    estimated_revenue = estimated_copies * params['price']

    st.subheader('Результат прогноза')
    c1, c2, c3 = st.columns(3)
    c1.metric('Прогноз отзывов', format_int(pred_reviews))
    c2.metric('Примерные копии (владельцы)', format_int(estimated_copies))
    c3.metric('Потенциальная выручка (USD)', f'${format_int(int(estimated_revenue))}')

    if params['price'] == 0:
        st.info('Цена равна 0 — игра бесплатная, прямая выручка от продаж копий '
                'в этой оценке нулевая.')
    if not params['selected_genres']:
        st.warning('Не выбрано ни одного жанра — прогноз может быть менее точным.')

    # Контекст относительно обучающей выборки
    df = load_dataset(DATA_PATH)
    if df is not None and 'total_reviews' in df.columns:
        median_reviews = int(df['total_reviews'].median())
        mean_reviews = int(df['total_reviews'].mean())
        st.caption(
            f'Для сравнения, в обучающем датасете ({len(df)} игр): медиана отзывов — '
            f'{format_int(median_reviews)}, среднее — {format_int(mean_reviews)}.'
        )


def render_importances(model, columns: list[str]) -> None:
    st.subheader('Важность признаков модели')
    importances = get_feature_importances(model)
    if importances is None or len(importances) != len(columns):
        st.warning('Не удалось получить feature_importances_ из модели.')
        return
    st.plotly_chart(plot_importances(importances, columns), width='stretch')


def render_methodology() -> None:
    with st.expander('О методике прогноза', expanded=False):
        st.markdown(
            f"""
- **Модель:** `RandomForestRegressor` (с заполнением пропусков медианой), обученная на
  `log1p(total_reviews)` по данным Steam. Логарифм сглаживает сильный перекос
  в распределении числа отзывов.
- **Прогноз отзывов:** `expm1(предсказание модели)` — возврат из логарифмической шкалы.
- **Копии (метод Boxleiter):** число проданных копий ≈ отзывы × **{BOXLEITER_MULTIPLIER}**.
  Реальный коэффициент зависит от жанра, цены и года и обычно лежит в диапазоне 20–60.
- **Выручка:** `копии × цена` — валовая, без учёта скидок, региональных цен,
  комиссии Steam (30%) и налогов.
- **Ограничения:** baseline-модель использует только метаданные (цена, год, языки,
  режимы, жанры); не учитывает маркетинг, качество игры, бренд студии и вишлисты.
  Результат — ориентировочная оценка, а не гарантированный прогноз.
"""
        )


def main() -> None:
    st.set_page_config(page_title='Steam Forecast', page_icon='🎮', layout='wide')
    st.title('🎮 Steam Forecast')
    st.markdown('Оценка коммерческого потенциала игры в Steam на основе ML-модели.')

    if not MODEL_PATH.exists():
        st.error(f'Файл модели не найден: `{MODEL_PATH}`. '
                 'Сначала запустите `python src/train_baseline.py`.')
        st.stop()

    model = load_model(MODEL_PATH)
    columns = get_feature_names(model)

    params, submitted = render_sidebar()

    if submitted:
        render_results(model, params, columns)
    else:
        st.info('Задайте параметры игры в боковой панели и нажмите '
                '**«Рассчитать потенциал»**.')

    st.divider()
    render_importances(model, columns)
    render_methodology()


if __name__ == '__main__':
    main()
