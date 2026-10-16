game1 = {
    "name": "rust",
    "genres": ["survive","multiplayer"],
    "appid": 404,
    "players": 1001
}

game2 = {
    "name": "minecraft",
    "genres": ["sandbox", "survive"],
    "appid": 1000,
    "players": 50
}

games = [game1, game2]

print(games)
print(game1["name"])
print(game2["genres"])
print(len(games))
print(games[1]["name"])

i=0
while int(input()) != game1["appid"]:
    i += 1
    print("Guess the number of the game" + " " + game1["name"])
    print("your attemps = ", i)
else:
    print("Congrats, you re right")
