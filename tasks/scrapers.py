import asyncio
import os
import json
import re
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
import redis.asyncio as redis
from sqlalchemy.orm import Session
from db.models import Event

async def scrape_kktix_events(db: Session):
    print("SCRAPER: [KKTIX] Starting scrape job using curl_cffi...")
    new_event_titles = []
    
    try:
        db.query(Event).filter(Event.external_url.like('%/dashboard/events/new%')).delete(synchronize_session=False)
        db.query(Event).filter(Event.title.like('%建立活動%')).delete(synchronize_session=False)
        db.commit()

        # 🎯 終極武器：使用 curl_cffi 完美偽裝成 Chrome 120 的底層網路封包！
        async with AsyncSession(impersonate="chrome120") as session:
            target_url = "https://kktix.com/events?end_at=&event_tag_ids_in=1%2C6&max_price=&min_price=&search=&start_at="
            print("SCRAPER: [KKTIX] 正在獲取活動列表...")
            response = await session.get(target_url, timeout=30)
            
            if "Just a moment" in response.text or "Cloudflare" in response.text:
                print("SCRAPER: [KKTIX] 警告：仍被 CF 攔截。")
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            events_data = []
            seen_urls = set()
            
            links = soup.find_all('a', href=True)
            for a in links:
                url = a['href']
                if not ('/events/' in url) or url.endswith('/events') or '/dashboard/' in url or url in seen_urls:
                    continue
                    
                title = a.get_text(strip=True)
                img_src = ""
                
                container = a.find_parent(['li', 'div', 'article', 'a'])
                if container:
                    img = container.find('img')
                    if img:
                        img_src = img.get('src') or img.get('data-src') or img.get('ng-src') or ""
                    if not title:
                        heading = container.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'span', 'p'], class_=re.compile(r'title|name', re.I))
                        if heading:
                            title = heading.get_text(strip=True)
                
                if not title and img_src:
                    img = a.find('img')
                    title = a.get('title') or (img.get('alt') if img else '')
                
                if title and len(title) > 2 and "建立活動" not in title:
                    seen_urls.add(url)
                    events_data.append({
                        "title": re.sub(r'\s+', ' ', title),
                        "url": url,
                        "cover_image": img_src
                    })
                    
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
                    res = await session.get(ev.external_url, timeout=15)
                    inner_soup = BeautifulSoup(res.text, 'html.parser')
                    info = inner_soup.find(class_='event-info')
                    ev.description = info.get_text(strip=True)[:400] + '\n...' if info else '請點擊「前往原網站搶票」查看詳細資訊。'
                    db.commit()
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"SCRAPER: [KKTIX] 深度抓取失敗 {ev.external_url}: {e}")
                    db.rollback()

    except Exception as e:
        db.rollback()
        print(f"SCRAPER: [KKTIX] An error occurred: {e}")
        
    return new_event_titles

async def scrape_tixcraft_events(db: Session):
    print("SCRAPER: [TIXCRAFT] Starting scrape job using curl_cffi...")
    new_event_titles = []
    
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            print("SCRAPER: [TIXCRAFT] 正在進入活動列表...")
            response = await session.get("https://tixcraft.com/activity", timeout=30)
            
            if "Identity Verified" in response.text or "Cloudfront" in response.text:
                print("SCRAPER: [TIXCRAFT] 警告：仍被 AWS WAF 攔截。")
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
                    res = await session.get(ev.external_url, timeout=15)
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
