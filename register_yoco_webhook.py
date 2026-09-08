import requests

YOCO_SECRET_KEY = input(
    "Paste your Yoco TEST secret key: "
).strip()

url = "https://payments.yoco.com/api/webhooks"

payload = {
    "name": "kalxa-payments",
    "url": "https://lac-local-access.onrender.com/webhooks/yoco",
}

headers = {
    "Authorization": f"Bearer {YOCO_SECRET_KEY}",
    "Content-Type": "application/json",
}

response = requests.post(
    url,
    json=payload,
    headers=headers,
    timeout=20,
)

print("Status:", response.status_code)
print("Response:")
print(response.text)
