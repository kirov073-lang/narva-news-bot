import os
from google import genai

GEMINI_KEY = os.environ.get("GEMINI_KEY")
print(f"Ключ начинается с: {GEMINI_KEY[:5]}... (длина: {len(GEMINI_KEY)})")

try:
    client = genai.Client(api_key=GEMINI_KEY)
    print("Клиент создан. Пробуем получить список моделей...")
    for m in client.models.list():
        print(f"Доступна модель: {m.name}")
except Exception as e:
    print(f"ОШИБКА: {e}")
    
