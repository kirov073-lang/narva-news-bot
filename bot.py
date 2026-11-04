import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sqlite3
import asyncio
from aiogram import Bot
from aiogram.types import FSInputFile
from datetime import datetime
import os
from google import genai
from google.genai import types

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
    conn.commit()
    return conn

def is_new(conn, url):
    return conn.execute("SELECT 1 FROM seen WHERE url=?", (url,)).fetchone() is None

def mark_seen(conn, url):
    conn.execute("INSERT OR IGNORE INTO seen (url) VALUES (?)", (url,))
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
            links.append({"title": title, "url": full})
    return links[:5]

def parse_article(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    paragraphs = soup.find_all("p")
    valid_paragraphs = [p for p in paragraphs if len(p.get_text(strip=True)) > 30]
    
    if not valid_paragraphs:
        return None
        
    body = "\n\n".join([p.get_text(" ", strip=True) for p in valid_paragraphs])
    
    img_url = None
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and ("uploads" in src or "stena.ee" in src):
            img_url = urljoin("https://www.stena.ee/", src)
            break

    return {"img": img_url, "text": body}

def summarize_text(text):
    if not text or len(text) < 100:
        return text
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Ты — редактор новостей. Сократи этот текст до 3-5 предложений на русском языке. Оставь только самую суть. Пиши четко и понятно. Не добавляй воду.\n\nТекст:\n{text[:3000]}",
            config=types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.1,
            )
        )
        if response.text:
            return response.text.strip()
        return text[:400] + "…"
    except Exception as e:
        print(f"Gemini error: {e}")
        return text[:400] + "…"

async def main():
    if not BOT_TOKEN or not CHANNEL_ID or not GEMINI_KEY:
        print("ERROR: Настройки не заданы")
        return
    conn = init_db()
    links = parse_stena_list()
    print(f"[{datetime.now()}] Ссылок на главной: {len(links)}")

    bot = Bot(token=BOT_TOKEN)
    sent = 0
    try:
        for item in links:
            if not is_new(conn, item["url"]):
                continue
            article = parse_article(item["url"])
            if not article:
                print(f"Не удалось разобрать: {item['url']}")
                mark_seen(conn, item["url"])
                continue

            short_text = summarize_text(article["text"])
            
            # ВОТ ЗДЕСЬ ИЗМЕНЕНИЕ: Источник: stena.ee (кликабельно)
            caption = f"<b>{item['title']}</b>\n\n{short_text}\n\nИсточник: <a href=\"{item['url']}\">stena.ee</a>"

            ok = False
            if article["img"]:
                ok = await send_photo(bot, article["img"], caption)
            if not ok:
                try:
                    await bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML")
                except Exception as e:
                    print(f"send error: {e}")
            mark_seen(conn, item["url"])
            sent += 1
            await asyncio.sleep(6)
    finally:
        await bot.session.close()
        conn.close()
    print(f"Отправлено новых: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
