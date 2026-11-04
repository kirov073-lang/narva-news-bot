import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
import asyncio
from aiogram import Bot
from aiogram.types import FSInputFile
from datetime import datetime
import os
import time
import random
from google import genai

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
DB_FILE = "seen_news.db"

client = genai.Client(api_key=GEMINI_KEY)

async def send_photo(bot, photo_url, caption):
    try:
        r = requests.get(photo_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            ext = ".png" if "png" in r.headers["Content-Type"] else ".jpg"
            path = f"tmp{ext}"
            with open(path, "wb") as f:
                f.write(r.content)
            await bot.send_photo(chat_id=CHANNEL_ID, photo=FSInputFile(path),
                                 caption=caption, parse_mode="HTML")
            os.remove(path)
            return True
    except Exception as e:
        print(f"photo error: {e}")
    return False

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_text (text_hash TEXT PRIMARY KEY)")
    conn.commit()
    return conn

def is_new(conn, url):
    return conn.execute("SELECT 1 FROM seen WHERE url=?", (url,)).fetchone() is None

def is_new_text(conn, text):
    # Проверка на дубликат по смыслу (первые 50 символов)
    text_hash = text[:50].strip()
    return conn.execute("SELECT 1 FROM seen_text WHERE text_hash=?", (text_hash,)).fetchone() is None

def mark_seen(conn, url, text):
    conn.execute("INSERT OR IGNORE INTO seen (url) VALUES (?)", (url,))
    text_hash = text[:50].strip()
    conn.execute("INSERT OR IGNORE INTO seen_text (text_hash) VALUES (?)", (text_hash,))
    conn.commit()

def parse_stena_list():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get("https://www.stena.ee/", headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links, seen_urls = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 30:
            continue
        if href.startswith("/") or href.startswith("https://www.stena.ee/"):
            full = href if href.startswith("http") else urljoin("https://www.stena.ee/", href)
            if len(full) < 45 or full in seen_urls:
                continue
            if "/afisha/" in full:
                continue
            seen_urls.add(full)
            links.append({"title": title, "url": full, "source": "stena.ee"})
    return links[:3] # Берем 3 с stena и 3 с narva

def parse_narva_list():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    # Берем главную страницу narva.ee (русская версия)
    r = requests.get("https://www.narva.ee/ru", headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    links, seen_urls = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 30:
            continue
        # Ищем ссылки на новости
        if href.startswith("/") or href.startswith("https://www.narva.ee/"):
            full = href if href.startswith("http") else urljoin("https://www.narva.ee/", href)
            if len(full) < 45 or full in seen_urls:
                continue
            # Оставляем только ссылки, где есть слово 'novosti' или 'news'
            if "/novosti/" in full or "/news/" in full:
                seen_urls.add(full)
                links.append({"title": title, "url": full, "source": "narva.ee"})
    return links[:3]

def parse_article(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Пытаемся найти основной текст (универсально для обоих сайтов)
    paragraphs = soup.find_all("p")
    valid_paragraphs = [p for p in paragraphs if len(p.get_text(strip=True)) > 30]
    
    if not valid_paragraphs:
        return None
        
    body = "\n\n".join([p.get_text(" ", strip=True) for p in valid_paragraphs])
    
    img_url = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and ("uploads" in src or "stena.ee" in src or "narva.ee" in src):
            img_url = urljoin("https://www.stena.ee/", src)
            break

    return {"img": img_url, "text": body}

def summarize_text(text):
    if not text or len(text) < 100:
        return text
    
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=f"Ты — редактор новостей. Сократи этот текст до 4-6 предложений на русском языке. Оставь все важные факты и цифры, но убери 'воду', лишние подробности и повторы. Пиши понятно и по делу.\n\nТекст:\n{text[:3000]}"
            )
            if response.text:
                print(f"--- GEMINI OTVET POLUCHEN (попытка {attempt+1}) ---")
                return response.text.strip()
        except Exception as e:
            err_text = str(e)
            if "503" in err_text or "UNAVAILABLE" in err_text or "overloaded" in err_text:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"--- GEMINI ZANYAT (503), zhdu {wait_time:.1f} sek... ---")
                time.sleep(wait_time)
            else:
                print(f"--- KRITICHESKAYA OSHIBKA GEMINI: {e} ---")
                break
    
    return f"⚠️ Не удалось сократить текст.\n\n{text[:300]}..."

async def main():
    if not BOT_TOKEN or not CHANNEL_ID or not GEMINI_KEY:
        print("ERROR: Настройки не заданы")
        return
    conn = init_db()
    
    # Собираем новости с двух сайтов
    stena_links = parse_stena_list()
    narva_links = parse_narva_list()
    all_links = stena_links + narva_links
    
    print(f"[{datetime.now()}] Ссылок: {len(all_links)}")

    bot = Bot(token=BOT_TOKEN)
    sent = 0
    try:
        for item in all_links:
            if not is_new(conn, item["url"]):
                continue
                
            article = parse_article(item["url"])
            if not article:
                print(f"Не удалось разобрать: {item['url']}")
                continue
            
            # ПРОВЕРКА НА ДУБЛИКАТЫ ПО ТЕКСТУ
            if not is_new_text(conn, article["text"]):
                print(f"Пропущен дубликат текста: {item['title'][:30]}...")
                mark_seen(conn, item["url"], article["text"]) # Помечаем, чтобы больше не проверять ссылку
                continue

            short_text = summarize_text(article["text"])
            
            caption = f"<b>{item['title']}</b>\n\n{short_text}\n\nИсточник: <a href=\"{item['url']}\">{item['source']}</a>"

            ok = False
            if article["img"]:
                ok = await send_photo(bot, article["img"], caption)
            if not ok:
                try:
                    await bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML")
                except Exception as e:
                    print(f"send error: {e}")
            
            mark_seen(conn, item["url"], article["text"])
            sent += 1
            
            print("Ждем 5 минут перед следующей новостью...")
            await asyncio.sleep(300) # Ждем 5 минут
    finally:
        await bot.session.close()
        conn.close()
    print(f"Отправлено новых: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
