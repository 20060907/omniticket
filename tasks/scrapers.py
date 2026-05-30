import asyncio
import os
import json
import re
import urllib.parse
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
import redis.asyncio as redis
from sqlalchemy.orm import Session
from db.models import Event
from playwright.async_api import async_playwright



async def scrape_kktix_events(db: Session):
    print("SCRAPER: [KKTIX] Starting scrape job using Playwright (Chromium Stealth)...")
    new_event_titles = []
    
    try:
        db.query(Event).filter(Event.external_url.like('%/dashboard/events/new%')).delete(synchronize_session=False)
        db.query(Event).filter(Event.title.like('%建立活動%')).delete(synchronize_session=False)
        db.commit()

        async with async_playwright() as p:
            # 🎯 終極解答：TicketPlus 的 Chromium 隱身參數 + 內部 Fetch Atom Feed！
            # 經交叉比對，Chromium 的隱身模式能成功通過 CF，而 events.atom 是唯一存活的 API
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--window-size=1920,1080",
                    "--disable-dev-shm-usage"
                ],
                ignore_default_args=["--enable-automation"]
            )
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-TW"
                )
                page = await context.new_page()
                
                # 隱藏自動化標籤
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = { runtime: {} };
                """)
                
                print("SCRAPER: [KKTIX] 正在以 Chromium (TicketPlus 隱身模式) 訪問首頁並等待 CF 通關...")
                await page.goto("https://kktix.com/", timeout=60000, wait_until="domcontentloaded")
                
                # 給 Cloudflare 一點時間運算通過驗證
                for _ in range(15):
                    title = await page.title()
                    if any(kw in title for kw in ["Just a moment", "Cloudflare", "Attention Required", "請稍候"]):
                        try:
                            await page.mouse.click(300, 300)
                        except: pass
                        await page.wait_for_timeout(3000)
                    else:
                        break
                        
                title = await page.title()
                print(f"SCRAPER: [KKTIX] CF 驗證結束，當前網頁標題為: '{title}'")
                
                events_data = []
                seen_urls = set()
                
                print("SCRAPER: [KKTIX] 嘗試在已通關的 Chromium 內部擷取 Atom Feed...")
                try:
                    # 利用已通關的瀏覽器 Cookie 進行內部 API 請求
                    api_data = await page.evaluate('''async () => {
                        const resp = await fetch("/events.atom");
                        if (!resp.ok) return null;
                        return await resp.text();
                    }''')
                    
                    if api_data and "<entry>" in api_data:
                        print("SCRAPER: [KKTIX] 內部 Fetch Atom 成功抓到資料！")
                        soup = BeautifulSoup(api_data, "html.parser")
                        for entry in soup.find_all("entry"):
                            title_elem = entry.find("title")
                            link_elem = entry.find("link")
                            summary_elem = entry.find("summary") or entry.find("content")
                            if not title_elem or not link_elem: continue
                            title_str = title_elem.text.strip()
                            url = link_elem.get("href", "")
                            if not url or url in seen_urls: continue
                            if "dashboard/events/new" in url or "建立活動" in title_str: continue
                            img_src = ""
                            if summary_elem:
                                soup_sum = BeautifulSoup(summary_elem.text, 'html.parser')
                                img = soup_sum.find('img')
                                if img: img_src = img.get('src') or ""
                            if title_str and len(title_str) > 2:
                                seen_urls.add(url)
                                events_data.append({"title": re.sub(r'\s+', ' ', title_str), "url": url, "cover_image": img_src})
                except Exception as e:
                    print(f"SCRAPER: [KKTIX] 內部 Fetch Atom 失敗: {e}")

                if not events_data:
                    print("SCRAPER: [KKTIX] 警告：無法獲取活動資料。")
                    return []
                        
                print(f"SCRAPER: [KKTIX] 本頁共找到 {len(events_data)} 個活動。")
                
                for item in events_data:
                    db_event = db.query(Event).filter(Event.external_url == item['url']).first()
                    if not db_event:
                        new_event = Event(
                            title=item['title'],
                            external_url=item['url'],
                            cover_image_url=item['cover_image'],
                            source_platform='kktix'
                        )
                        db.add(new_event)
                        new_event_titles.append(item['title'])
                    else:
                        if not db_event.cover_image_url and item['cover_image']:
                            db_event.cover_image_url = item['cover_image']
                            db_event.title = item['title']

                db.commit()
                print(f"SCRAPER: [KKTIX] Scrape successful. Upserted {len(events_data)} events.")
                
                if new_event_titles:
                    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                    redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                    await redis_client.publish("data-updates", json.dumps({"source": "kktix", "new_events": new_event_titles}))
                    await redis_client.close()

                # 深度爬蟲階段
                events_to_deep_scrape = db.query(Event).filter(Event.source_platform == 'kktix', Event.description == None).limit(3).all()
                for ev in events_to_deep_scrape:
                    try:
                        print(f"SCRAPER: [KKTIX] 深度抓取內頁 -> {ev.title}")
                        await page.goto(ev.external_url, timeout=30000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(2000)
                        
                        desc = await page.evaluate("""() => {
                            const info = document.querySelector('.event-info');
                            return info ? info.innerText.trim() : null;
                        }""")
                        
                        ev.description = (desc[:400] + '\n...') if desc else '請點擊「前往原網站搶票」查看詳細資訊。'
                        db.commit()
                    except Exception as e:
                        print(f"SCRAPER: [KKTIX] 深度抓取失敗 {ev.external_url}: {e}")
                        db.rollback()

            finally:
                await browser.close()

    except Exception as e:
        db.rollback()
        print(f"SCRAPER: [KKTIX] An error occurred: {e}")
        
    return new_event_titles

async def scrape_tixcraft_events(db: Session):
    print("SCRAPER: [TIXCRAFT] Starting scrape job using curl_cffi...")
    new_event_titles = []
    
    try:
        async with AsyncSession(impersonate="safari17_0") as session:
            print("SCRAPER: [TIXCRAFT] 透過 AllOrigins 代理繞過 AWS IP 封鎖...")
            proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote('https://tixcraft.com/activity')}"
            response = await session.get(proxy_url, timeout=30)
            
            if "Identity Verified" in response.text or "Cloudfront" in response.text:
                print("SCRAPER: [TIXCRAFT] 警告：代理仍被 WAF 攔截，嘗試偽裝成 Googlebot...")
                response = await session.get("https://tixcraft.com/activity", headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}, timeout=30)
                if "Identity Verified" in response.text or "Cloudfront" in response.text:
                    print("SCRAPER: [TIXCRAFT] 警告：所有突破方式皆被攔截。")
                    return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            events_data = []
            seen_urls = set()
            
            links = soup.find_all('a', href=True)
            for a in links:
                url = a['href']
                if not ('/activity/detail/' in url or '/activity/game/' in url):
                    continue
                if not url.startswith('http'):
                    url = f"https://tixcraft.com{url}"
                    
                if url in seen_urls:
                    continue
                    
                title = a.get_text(strip=True)
                img_src = ""
                
                container = a.find_parent(['li', 'div', 'article', 'a'])
                if container:
                    img = container.find('img')
                    if img:
                        img_src = img.get('src') or img.get('data-src') or img.get('ng-src') or ""
                    if not title:
                        heading = container.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div'], class_=re.compile(r'title|name|txt', re.I))
                        if heading:
                            title = heading.get_text(strip=True)
                            
                if not img_src:
                    img = a.find('img')
                    if img:
                        img_src = img.get('src') or img.get('data-src') or ""
                        
                if img_src and img_src.startswith('/'):
                    img_src = f"https://tixcraft.com{img_src}"
                    
                if title and len(title) > 2:
                    seen_urls.add(url)
                    events_data.append({
                        "title": re.sub(r'\s+', ' ', title),
                        "url": url,
                        "cover_image": img_src
                    })
                    
            print(f"SCRAPER: [TIXCRAFT] 本頁共找到 {len(events_data)} 個活動。")
            
            for item in events_data:
                db_event = db.query(Event).filter(Event.external_url == item['url']).first()
                if not db_event:
                    new_event = Event(title=item['title'], external_url=item['url'], cover_image_url=item['cover_image'], source_platform='tixcraft')
                    db.add(new_event)
                    new_event_titles.append(item['title'])
                else:
                    if not db_event.cover_image_url and item['cover_image']:
                        db_event.cover_image_url = item['cover_image']
                        db_event.title = item['title']

            db.commit()
            print(f"SCRAPER: [TIXCRAFT] Scrape successful. Upserted {len(events_data)} events.")
            
            if new_event_titles:
                REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                await redis_client.publish("data-updates", json.dumps({"source": "tixcraft", "new_events": new_event_titles}))
                await redis_client.close()

            # 深度爬蟲階段
            events_to_deep_scrape = db.query(Event).filter(Event.source_platform == 'tixcraft', Event.description == None).limit(3).all()
            for ev in events_to_deep_scrape:
                try:
                    print(f"SCRAPER: [TIXCRAFT] 深度抓取內頁 -> {ev.title}")
                    proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(ev.external_url)}"
                    res = await session.get(proxy_url, timeout=15)
                    inner_soup = BeautifulSoup(res.text, 'html.parser')
                    info = inner_soup.find(['div', 'table'], class_=re.compile(r'activity-info|game-info|table', re.I))
                    desc = info.get_text(strip=True)[:400] + '\n...' if info else '請點擊「前往原網站搶票」查看詳細資訊。'
                    ev.description = desc
                    db.commit()
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"SCRAPER: [TIXCRAFT] 深度抓取失敗 {ev.external_url}: {e}")
                    db.rollback()

    except Exception as e:
        db.rollback()
        print(f"SCRAPER: [TIXCRAFT] An error occurred: {e}")
        
    return new_event_titles
