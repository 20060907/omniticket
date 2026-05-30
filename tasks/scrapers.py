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

async def fetch_with_proxies(session, target_url, is_json=False):
    """強大的多重免費代理輪詢機制，加上社群爬蟲白名單偽裝"""
    
    # 🎯 社群平台爬蟲特權：各大防護系統為確保網址分享能正常產生縮圖，通常會對這些 UA 絕對放行！
    headers_list = [
        {"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}, # FB 爬蟲
        {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}, # Google 爬蟲
        {"User-Agent": "Twitterbot/1.0"}, # Twitter 爬蟲
        None # 預設 (curl_cffi 偽裝的 Chrome/Safari)
    ]
    
    # 1. 優先測試直連搭配社群 UA 偽裝
    for headers in headers_list:
        try:
            await asyncio.sleep(0.5)
            resp = await session.get(target_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                text = resp.text
                if not any(waf in text for waf in ["Identity Verified", "Cloudfront", "Just a moment", "Cloudflare", "Attention Required"]):
                    if is_json:
                        try:
                            data = resp.json()
                            if data: return data, text
                        except Exception: pass
                    else:
                        if len(text) > 300: return None, text
        except Exception: pass

    # 2. 如果直連全死，再測試第三方代理伺服器
    proxies = [
        (f"https://api.allorigins.win/raw?url={urllib.parse.quote(target_url)}", None),
        (f"https://api.allorigins.win/get?url={urllib.parse.quote(target_url)}", None),
        (f"https://api.codetabs.com/v1/proxy/?quest={urllib.parse.quote(target_url)}", None),
        (f"https://corsproxy.io/?{urllib.parse.quote(target_url)}", None),
        (f"https://proxy.cors.sh/{target_url}", {"x-cors-api-key": "temp_123456789"}),
        (f"https://cors-anywhere.herokuapp.com/{target_url}", {"X-Requested-With": "XMLHttpRequest"})
    ]
    
    for p_url, headers in proxies:
        try:
            await asyncio.sleep(0.5)
            resp = await session.get(p_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                text = resp.text
                if not any(waf in text for waf in ["Identity Verified", "Cloudfront", "Just a moment", "Cloudflare", "Attention Required"]):
                    if "api.allorigins.win/get" in p_url:
                        try:
                            data = resp.json()
                            text = data.get("contents", "")
                            if not text: continue
                        except: continue
                        
                    if is_json:
                        try:
                            data = json.loads(text)
                            if data: return data, text
                        except Exception: pass
                    else:
                        if len(text) > 300: return None, text
        except Exception: pass
        
    return None, None

async def scrape_kktix_events(db: Session):
    print("SCRAPER: [KKTIX] Starting scrape job using curl_cffi...")
    new_event_titles = []
    
    try:
        db.query(Event).filter(Event.external_url.like('%/dashboard/events/new%')).delete(synchronize_session=False)
        db.query(Event).filter(Event.title.like('%建立活動%')).delete(synchronize_session=False)
        db.commit()

        # 升級 impersonate 為更新的瀏覽器特徵
        async with AsyncSession(impersonate="safari17_0") as session:
            print("SCRAPER: [KKTIX] 嘗試透過多重代理與 Atom Feed 獲取活動列表...")
            
            events_data = []
            seen_urls = set()

            # 🎯 終極 RSS 代理：使用 rss2json 官方 API (各大網站防護通常會放行正規的 RSS 閱讀器)
            print("SCRAPER: [KKTIX] 嘗試使用 RSS 專屬解析代理 (rss2json)...")
            try:
                rss_proxy_url = f"https://api.rss2json.com/v1/api.json?rss_url={urllib.parse.quote('https://kktix.com/events.atom')}"
                rss_resp = await session.get(rss_proxy_url, timeout=20)
                if rss_resp.status_code == 200:
                    data = rss_resp.json()
                    if data.get("status") == "ok":
                        for item in data.get("items", []):
                            title = item.get("title", "")
                            url = item.get("link", "")
                            summary = item.get("description", "") or item.get("content", "")
                            
                            if not url or url in seen_urls: continue
                            if "dashboard/events/new" in url or "建立活動" in title: continue
                            
                            img_src = ""
                            if summary:
                                soup_sum = BeautifulSoup(summary, 'html.parser')
                                img = soup_sum.find('img')
                                if img: img_src = img.get('src') or ""
                                
                            if title and len(title) > 2:
                                seen_urls.add(url)
                                events_data.append({"title": re.sub(r'\s+', ' ', title), "url": url, "cover_image": img_src})
                    else:
                        print(f"SCRAPER: [KKTIX] rss2json 回傳錯誤: {data.get('message')}")
                else:
                    print(f"SCRAPER: [KKTIX] rss2json 伺服器回傳狀態碼: {rss_resp.status_code}")
            except Exception as e:
                print(f"SCRAPER: [KKTIX] rss2json 代理解析錯誤: {e}")

            if not events_data:
                print("SCRAPER: [KKTIX] 嘗試使用備用 RSS 解析代理 (feed2json)...")
                try:
                    fm_url = f"https://feed2json.org/convert?url={urllib.parse.quote('https://kktix.com/events.atom')}"
                    fm_resp = await session.get(fm_url, timeout=20)
                    if fm_resp.status_code == 200:
                        fm_data = fm_resp.json()
                        items = fm_data.get("items", [])
                        
                        for item in items:
                            title = item.get("title", "")
                            url = item.get("url", "")
                            summary = item.get("content_html", "") or item.get("summary", "")
                            
                            if not url or url in seen_urls: continue
                            if "dashboard/events/new" in url or "建立活動" in title: continue
                            
                            img_src = ""
                            if summary:
                                soup_sum = BeautifulSoup(summary, 'html.parser')
                                img = soup_sum.find('img')
                                if img: img_src = img.get('src') or ""
                                
                            if title and len(title) > 2:
                                seen_urls.add(url)
                                events_data.append({"title": re.sub(r'\s+', ' ', title), "url": url, "cover_image": img_src})
                except Exception as e:
                    print(f"SCRAPER: [KKTIX] feed2json 代理解析失敗: {e}")

            if not events_data:
                print("SCRAPER: [KKTIX] 專屬 API 皆失敗，退回多重代理機制...")
                # KKTIX 隱藏技巧：Atom Feed 幾乎不會被 Cloudflare 擋！
                json_data, atom_text = await fetch_with_proxies(session, "https://kktix.com/events.atom", is_json=False)
                
                if atom_text and "<entry>" in atom_text:
                    soup = BeautifulSoup(atom_text, "html.parser")
                    for entry in soup.find_all("entry"):
                        title_elem = entry.find("title")
                        link_elem = entry.find("link")
                        summary_elem = entry.find("summary")
                        
                        if not title_elem or not link_elem: continue
                        title, url = title_elem.text.strip(), link_elem.get("href", "")
                        
                        if not url or url in seen_urls: continue
                        if "dashboard/events/new" in url or "建立活動" in title: continue
                        
                        img_src = ""
                        if summary_elem:
                            soup_sum = BeautifulSoup(summary_elem.text, 'html.parser')
                            img = soup_sum.find('img')
                            if img: img_src = img.get('src') or ""
                            
                        if title and len(title) > 2:
                            seen_urls.add(url)
                            events_data.append({"title": re.sub(r'\s+', ' ', title), "url": url, "cover_image": img_src})
                else:
                    # 備用方案：JSON API + 多重代理
                    json_data, _ = await fetch_with_proxies(session, "https://kktix.com/events.json", is_json=True)
                    if json_data:
                        for entry in json_data.get("entry", []):
                            url, title, summary = entry.get("url", ""), entry.get("title", ""), entry.get("summary", "")
                            
                            if not url or url in seen_urls: continue
                            if "dashboard/events/new" in url or "建立活動" in title: continue
                            
                            img_src = ""
                            if summary:
                                soup_sum = BeautifulSoup(summary, 'html.parser')
                                img = soup_sum.find('img')
                                if img: img_src = img.get('src') or ""
                                
                            if title and len(title) > 2:
                                seen_urls.add(url)
                                events_data.append({"title": re.sub(r'\s+', ' ', title), "url": url, "cover_image": img_src})
                            
            if not events_data:
                print("SCRAPER: [KKTIX] 警告：所有突破方式皆失敗。")
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
                    _, inner_text = await fetch_with_proxies(session, ev.external_url, is_json=False)
                    
                    inner_soup = BeautifulSoup(inner_text or "", 'html.parser')
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
