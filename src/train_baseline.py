"""Обучение baseline-моделей регрессии для прогнозирования популярности игры.

Вход:  data/processed/games_clean.csv
Цель:  y = log1p(total_reviews)
Выход: models/baseline_rf.joblib (лучшая модель по R^2 на тесте)
"""
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / 'data' / 'processed' / 'games_clean.csv'
MODEL_DIR = PROJECT_ROOT / 'models'
MODEL_PATH = MODEL_DIR / 'baseline_rf.joblib'

# reviews_per_year производна от целевой total_reviews - исключаем, чтобы не было утечки таргета
DROP_COLUMNS = ['appid', 'name', 'positive_ratio', 'reviews_per_year']
TARGET_SOURCE = 'total_reviews'
TOP_N_FEATURES = 5


def load_features(path: Path):
    """Возвращает (X, y): признаки и log1p(total_reviews)."""
    df = pd.read_csv(path)
    y = np.log1p(df[TARGET_SOURCE].astype(float))
    X = df.drop(columns=[*DROP_COLUMNS, TARGET_SOURCE], errors='ignore')
    X = X.select_dtypes(include=[np.number, bool]).astype(float)
    return X, y


def build_models() -> dict:
    """Пропуски (например, release_year) заполняются медианой, обучающей выборки."""
    return {
        'Ridge': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('scaler', StandardScaler()),
            ('model', Ridge(alpha=1.0)),
        ]),
        'RandomForest': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', RandomForestRegressor(n_estimators=100, random_state=42)),
        ]),
    }


def evaluate(model, X_test, y_test) -> dict:
    pred = model.predict(X_test)
    return {
        'R2': r2_score(y_test, pred),
        'MAE': mean_absolute_error(y_test, pred),
        'RMSE': float(np.sqrt(mean_squared_error(y_test, pred))),
    }


def print_metrics_table(results: dict) -> None:
    header = f"{'Модель':<16}{'R2':>10}{'MAE':>10}{'RMSE':>10}"
    line = '-' * len(header)
    print('\nМетрики на тестовой выборке (целевая: log1p(total_reviews))')
    print(line)
    print(header)
    print(line)
    for name, m in results.items():
        print(f"{name:<16}{m['R2']:>10.4f}{m['MAE']:>10.4f}{m['RMSE']:>10.4f}")
    print(line)


def print_top_features(rf_pipeline: Pipeline, feature_names, top_n: int) -> None:
    importances = rf_pipeline.named_steps['model'].feature_importances_
    order = np.argsort(importances)[::-1][:top_n]
    print(f'\nТоп-{top_n} признаков по важности (RandomForest)')
    print('-' * 40)
    for rank, idx in enumerate(order, start=1):
        print(f'{rank}. {feature_names[idx]:<20}{importances[idx]:>10.4f}')
    print('-' * 40)


def main() -> int:
    if not DATA_PATH.exists():
        print(f'Файл не найден: {DATA_PATH}', file=sys.stderr)
        return 1

    X, y = load_features(DATA_PATH)
    print(f'Загружено строк: {len(X)}, признаков: {X.shape[1]}')
    print('Признаки:', ', '.join(X.columns))

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    print(f'Train: {len(X_train)}, Test: {len(X_test)}')

    models = build_models()
    results = {}
    for name, model in models.items():
        model.fit(X_train, y_train)
        results[name] = evaluate(model, X_test, y_test)

    print_metrics_table(results)
    print_top_features(models['RandomForest'], list(X.columns), TOP_N_FEATURES)

    best_name = max(results, key=lambda n: results[n]['R2'])
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(models[best_name], MODEL_PATH)
    print(f'\nЛучшая модель по R2: {best_name}. Сохранена в {MODEL_PATH}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
