import json
import time
from pathlib import Path
import requests

SAMPLE_APP_IDS = [620, 105600, 367520]

def get_game_reviews(appid: int) -> dict:
    """Получает статистику отзывов через открытый API SteamSpy."""
    url = f"https://steamspy.com/api.php?request=appdetails&appid={appid}"
    headers = {
        "User-Agent": "SteamForecastResearchBot/1.0"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            pos = data.get("positive", 0)
            neg = data.get("negative", 0)
            total = pos + neg
            
            return {
                "total_reviews": total,
                "total_positive": pos,
                "total_negative": neg,
                "review_score_desc": f"{round((pos / total) * 100)}% positive" if total > 0 else "No reviews"
            }
        else:
            print(f"[{appid}] Ошибка SteamSpy: статус {res.status_code}")
    except Exception as e:
        print(f"[{appid}] Ошибка получения отзывов: {e}")
    
    return {
        "total_reviews": 0,
        "total_positive": 0,
        "total_negative": 0,
        "review_score_desc": "Unknown"
    }

def get_game_data(appid: int) -> dict | None:
    """Запрашивает метаданные и объединяет их со статистикой отзывов."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"[{appid}] Ошибка запроса данных: {response.status_code}")
            return None
            
        data = response.json()
        app_entry = data.get(str(appid), {})
        
        if not app_entry.get("success"):
            print(f"[{appid}] Игра не найдена или недоступна")
            return None
            
        game_info = app_entry.get("data", {})
        
        genres_raw = game_info.get("genres", [])
        genres = [g.get("description") for g in genres_raw]
        
        price_info = game_info.get("price_overview")
        price = price_info.get("final_formatted") if price_info else "Free / No price"
        
        # Получаем метрики отзывов
        reviews_data = get_game_reviews(appid)
        
        return {
            "appid": appid,
            "name": game_info.get("name"),
            "release_date": game_info.get("release_date", {}).get("date"),
            "is_free": game_info.get("is_free", False),
            "genres": genres,
            "price": price,
            # Целевые метрики для обучения:
            **reviews_data
        }
        
    except Exception as e:
        print(f"[{appid}] Ошибка обработки: {e}")
        return None

def main():
    collected_games = []
    
    output_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "games_sample.json"

    print("Начинаем сбор полных данных с отзывами...")
    for appid in SAMPLE_APP_IDS:
        print(f"Запрашиваем appid {appid}...")
        game = get_game_data(appid)
        if game:
            collected_games.append(game)
        
        # Пауза 1.5 сек между вызовами
        time.sleep(1.5)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(collected_games, f, ensure_ascii=False, indent=2)

    print(f"\nГотово! Собрано записей: {len(collected_games)}")
    print(f"Обновлен файл: {output_file}")

if __name__ == "__main__":
    main()