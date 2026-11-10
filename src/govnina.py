games = [
    {"name": "Game A", "rating": 92},
    {"name": "Game B", "rating": 75},
    {"name": "Game C"},
    {"name": "Game D", "rating": 80},
    {"name": "Game E", "rating": 60},
]

def get_high_rated_games(games):
    for game in games:
        if "rating" in game:
            all = [g["name"] for g in games if g["rating"] >= 80]
        else: continue
    return all

print(get_high_rated_games(games))