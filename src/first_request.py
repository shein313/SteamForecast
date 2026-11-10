import requests

response = requests.get("https://example.com")
print(response)
print(response.status_code)
print(len(response.text))
print(response.text)