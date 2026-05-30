# /celery_platform/tasks/scrapers.py

import asyncio
import os
import json
import time
import random
from playwright.async_api import async_playwright
import redis.asyncio as redis
from sqlalchemy.orm import Session
from db.models import Event # 引入我們的資料庫模型
from tasks.email_service import notify_subscribers

async def scrape_kktix_events(db: Session):
    """
    使用 Playwright 抓取 KKTIX 活動，將結果存入 PostgreSQL，並更新 Redis 快取。
    """
    print("SCRAPER: [KKTIX] Starting scrape job...")
    new_event_titles = []
    async with async_playwright() as p:
        # 🎯 再次進化：改回 Chromium 引擎，並加上全套的隱身參數 (Stealth Args) 來突破 Cloudflare
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--window-size=1920,1080",
                "--disable-dev-shm-usage"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-TW"
        )
        page = await context.new_page()
        
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW', 'zh', 'en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            """
        )

        try:
            # 🎯 終極除靈：先在資料庫中把之前不小心存進去的「建立活動」幽靈資料刪除
            db.query(Event).filter(Event.external_url.like('%/dashboard/events/new%')).delete(synchronize_session=False)
            db.query(Event).filter(Event.title.like('%建立活動%')).delete(synchronize_session=False)
            db.commit()

            all_events_data = []
            seen_urls = set()
            page_num = 1
            reloaded_pages = set()
            
            # 🎯 預熱機制：先到首頁取得合法 Cookie，降低被 Cloudflare 阻擋的機率
            print("SCRAPER: [KKTIX] 正在載入首頁進行 Cookie 預熱...")
            await page.goto("https://kktix.com/", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            # 🎯 回歸最自然的人類瀏覽模式：只載入首頁一次，後續全部依賴「點擊下一頁」，不再瘋狂觸發 Cloudflare！
            target_url = "https://kktix.com/events?end_at=&event_tag_ids_in=1%2C6&max_price=&min_price=&search=&start_at="
            print(f"SCRAPER: [KKTIX] 正在進入活動列表...")
            await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")

            while True:
                print(f"SCRAPER: [KKTIX] 正在解析第 {page_num} 頁...")
                
                # 🎯 Python 層級重試迴圈：等待畫面出現活動卡片，或遇到 CF 挑戰
                cards_found = False
                cf_retries = 0
                for _ in range(20): # 最多等待 40 秒
                    try:
                        title = await page.title()
                    except Exception:
                        title = ""
                        
                    if "請稍候" in title or "Just a moment" in title or "Attention Required" in title or "Cloudflare" in title:
                        cf_retries += 1
                        print(f"SCRAPER: [KKTIX] 遇到 CF 防護 ({title})，嘗試突破... ({cf_retries}/20)")
                        
                        try:
                            # 隨機滑動與盲點，幫助觸發 CF
                            await page.mouse.move(random.randint(300, 800), random.randint(200, 600), steps=5)
                            await page.evaluate("window.scrollBy(0, 150);")
                            
                            # 嘗試點擊 Turnstile 驗證框
                            iframes = await page.locator('iframe').all()
                            for iframe in iframes:
                                src = await iframe.get_attribute('src')
                                if src and ('challenge' in src or 'turnstile' in src):
                                    box = await iframe.bounding_box()
                                    if box and box['width'] > 0:
                                        tx, ty = box['x'] + 20, box['y'] + box['height'] / 2
                                        await page.mouse.move(tx, ty, steps=5)
                                        await page.mouse.down()
                                        await page.wait_for_timeout(100)
                                        await page.mouse.up()
                                        print("SCRAPER: [KKTIX] 物理點擊驗證框！")
                        except Exception:
                            pass
                            
                        # CF 需要比較長的運算時間，只在 12 次 (24秒) 時重整一次，不要頻繁打斷它
                        if cf_retries == 12:
                            print("SCRAPER: [KKTIX] 重新整理頁面以重置驗證...")
                            try:
                                await page.reload(wait_until="domcontentloaded", timeout=15000)
                            except Exception:
                                pass
                            
                        await page.wait_for_timeout(2000)
                        continue

                    try:
                        has_cards = await page.evaluate(
                            """() => {
                                const links = Array.from(document.querySelectorAll('a[href*="/events/"]'));
                                return links.some(a => !a.href.endsWith('/events') && !a.href.endsWith('/events/'));
                            }"""
                        )
                        if has_cards:
                            cards_found = True
                            break
                    except Exception:
                        pass
                        
                    await page.wait_for_timeout(2000)
                    
                if not cards_found:
                    print(f"SCRAPER: [KKTIX] 第 {page_num} 頁找不到活動。可能已達最後一頁或被 CF 阻擋。")
                    # 🎯 神級救援：如果是點擊下一頁後卡住 (背景 API 被擋)，我們就強制載入目標網址，觸發整頁 CF 驗證！
                    if page_num > 1 and page_num not in reloaded_pages:
                        print(f"SCRAPER: [KKTIX] 嘗試強制載入第 {page_num} 頁並觸發整頁 CF 驗證...")
                        try:
                            target_url = f"https://kktix.com/events?end_at=&event_tag_ids_in=1%2C6&max_price=&min_price=&page={page_num}&search=&start_at="
                            await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
                            reloaded_pages.add(page_num)
                            continue # 直接跳回 while 迴圈開頭，執行 CF 驗證
                        except Exception:
                            break
                    else:
                        break

                # 🎯 破圖突破口 1：模擬真人向下捲動，強制觸發海報圖片的 Lazy Load
                print("SCRAPER: [KKTIX] 模擬真人向下捲動，觸發海報圖片 Lazy Load...")
                await page.evaluate("""async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        let timer = setInterval(() => {
                            window.scrollBy(0, 400);
                            totalHeight += 400;
                            if(totalHeight >= document.body.scrollHeight || totalHeight > 4000){
                                clearInterval(timer);
                                resolve();
                            }
                        }, 200);
                    });
                }""")
                await page.wait_for_timeout(1000)

                # 🎯 無敵抓取法進化版：掃描所有 <a> 標籤，不依賴任何特定的 CSS 類別！
                events_data = await page.evaluate(
                    """() => {
                        const links = Array.from(document.querySelectorAll('a'));
                        const results = [];
                        const seen = new Set();
                        
                        links.forEach(a => {
                            const url = a.href;
                            if (!url.includes('/events/') || url.endsWith('/events') || url.endsWith('/events/')) return;
                            if (url.includes('/dashboard/') || url.includes('/users/') || seen.has(url)) return;
                            
                            let title = a.innerText.trim();
                            let imgSrc = '';
                            
                            const container = a.closest('li, .item, .card, article, div[class*="event"]') || a.parentElement;
                            if (container) {
                                const img = container.querySelector('img');
                                if (img) imgSrc = img.src || img.getAttribute('data-src') || img.getAttribute('ng-src') || '';
                                
                                if (!title) {
                                    const heading = container.querySelector('h1, h2, h3, h4, h5, h6, .title, .name');
                                    if (heading) title = heading.innerText.trim();
                                }
                            }
                            
                            if (!title && imgSrc) {
                                title = a.title || a.querySelector('img')?.alt || '';
                            }
                            
                            if (title && title.length > 2 && !title.includes('建立活動')) {
                                seen.add(url);
                                results.push({ title: title.replace(/\\s+/g, ' '), url: url, cover_image: imgSrc });
                            }
                        });
                        return results;
                    }"""
                )

                valid_events_in_page = 0
                for item in events_data:
                    # 🎯 利用網址特徵終極過濾「建立活動」
                    if "dashboard/events/new" in item['url'] or "建立活動" in item['title'] or "Create Event" in item['title']:
                        continue
                    if item['url'] not in seen_urls:
                        seen_urls.add(item['url'])
                        all_events_data.append(item)
                        valid_events_in_page += 1
                
                print(f"SCRAPER: [KKTIX] 本頁共找到 {len(events_data)} 個活動元素，過濾後剩 {valid_events_in_page} 個新活動")

                # 判斷是否還有下一頁：如果這一頁沒抓到任何新活動，代表已經到達最後一頁！
                if valid_events_in_page == 0:
                    # 輸出日誌協助 Debug
                    if not cards_found:
                        print("SCRAPER: [KKTIX] 警告！抓到 0 個活動。可能是 CF 阻擋。")
                        html_preview = await page.content()
                        print(html_preview[:500])
                        
                    print("SCRAPER: [KKTIX] 本頁無新活動，已經到達最後一頁，結束抓取。")
                    break
                    
                # 🎯 使用「點擊下一頁」按鈕，完全不改網址，避免激怒 Cloudflare
                next_button = page.locator('.pagination li:last-child:not(.disabled) a').first
                if await next_button.count() > 0:
                    pause_time = random.randint(4000, 7000)
                    print(f"SCRAPER: [KKTIX] 暫停 {pause_time/1000} 秒，模擬真人閱讀後點擊下一頁...")
                    await page.wait_for_timeout(pause_time)
                    
                    try:
                        await next_button.scroll_into_view_if_needed()
                        await next_button.click()
                        page_num += 1
                        await page.wait_for_timeout(2000) # 給 SPA 緩衝時間清空畫面
                    except Exception as e:
                        print(f"SCRAPER: [KKTIX] 點擊下一頁失敗: {e}")
                        break
                else:
                    print("SCRAPER: [KKTIX] 沒有下一頁按鈕了，結束抓取。")
                    break

            for item in all_events_data:
                if not item['url']: continue
                # 檢查資料庫是否已存在此活動
                db_event = db.query(Event).filter(Event.external_url == item['url']).first()
                if not db_event:
                    # 如果不存在，則新增
                    new_event = Event(
                        title=item['title'],
                        external_url=item['url'],
                        cover_image_url=item['cover_image'],
                        source_platform='kktix'
                    )
                    db.add(new_event)
                    new_event_titles.append(item['title']) # 記錄新活動
                else:
                    # 如果已存在，但原本沒有圖片，這次抓到了就更新它！
                    if not db_event.cover_image_url and item['cover_image']:
                        db_event.cover_image_url = item['cover_image']
                        db_event.title = item['title'] # 順便更新可能被截斷的標題

            db.commit() # 提交本次抓取的所有變更
            print(f"SCRAPER: [KKTIX] Scrape successful. Upserted {len(all_events_data)} events into PostgreSQL.")

            # 向 Redis 發布通知，告知 FastAPI 有新資料
            if new_event_titles:
                REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                await redis_client.publish(
                    "data-updates",
                    json.dumps({"source": "kktix", "new_events": new_event_titles})
                )
                await redis_client.close()

            # ----------------------------------------------------
            # 深度爬蟲 (Deep Scraping) 階段：進入內頁抓取詳細資訊
            # ----------------------------------------------------
            # 每次只抓 3 筆還沒有 description 的活動，避免頻繁請求被防護系統封鎖
            events_to_deep_scrape = db.query(Event).filter(Event.source_platform == 'kktix', Event.description == None).limit(3).all()
            for ev in events_to_deep_scrape:
                try:
                    print(f"SCRAPER: [KKTIX] 深度抓取內頁 -> {ev.title}")
                    await page.goto(ev.external_url, timeout=30000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    desc = await page.evaluate("""() => {
                        const info = document.querySelector('.event-info');
                        return info ? info.innerText.trim() : '請點擊「前往原網站搶票」查看詳細資訊。';
                    }""")
                    ev.description = desc
                    db.commit()
                    
                    # 抓完一筆後稍微停頓，模擬真人
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"SCRAPER: [KKTIX] 深度抓取失敗 {ev.external_url}: {e}")
                    db.rollback()

        except Exception as e:
            db.rollback() # 如果出錯，則回滾
            print(f"SCRAPER: [KKTIX] An error occurred: {e}")
        finally:
            await browser.close()
            
    return new_event_titles

async def scrape_tixcraft_events(db: Session):
    """
    使用 Playwright 抓取 tixcraft 網站上的活動，並將結果存入 PostgreSQL。
    """
    print("SCRAPER: [TIXCRAFT] Starting scrape job...")
    new_event_titles = []
    async with async_playwright() as p:
        # 加上隱藏自動化特徵的啟動參數
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW', 'zh', 'en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            """
        )

        try:
            # 🎯 預熱機制：先到首頁取得 AWS WAF 信任的 Cookie
            print("SCRAPER: [TIXCRAFT] 正在載入首頁進行 Cookie 預熱...")
            await page.goto("https://tixcraft.com/", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            print("SCRAPER: [TIXCRAFT] 正在進入活動列表...")
            await page.goto("https://tixcraft.com/activity", timeout=60000, wait_until="domcontentloaded")
            
            try:
                # 🎯 拓元常有追蹤代碼卡住 networkidle，改用固定等待讓 DOM 渲染完成
                await page.wait_for_timeout(4000)
            except Exception as e:
                title = await page.title()
                print(f"SCRAPER: [TIXCRAFT] 抓取失敗！目前機器人看到的網頁標題是: '{title}'")
                raise e

            print("SCRAPER: [TIXCRAFT] 模擬真人向下捲動，載入更多隱藏活動...")
            await page.evaluate("""async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    let timer = setInterval(() => {
                        window.scrollBy(0, 600);
                        totalHeight += 600;
                        if(totalHeight >= Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) - window.innerHeight || totalHeight > 25000){
                            clearInterval(timer);
                            resolve();
                        }
                    }, 400);
                });
            }""")
            await page.wait_for_timeout(5000) # 加長等待時間，確保所有懶加載圖片與 DOM 出現

            # 🎯 拓元無敵抓取法進化版：不受限於特定的父層類別，直接從超連結回推！
            events_data = await page.evaluate(
                """() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    const results = [];
                    const seen = new Set();
                    
                    links.forEach(a => {
                        const url = a.href;
                        if (!url.includes('/activity/detail/') && !url.includes('/activity/game/')) return;
                        if (seen.has(url)) return;
                        
                        let title = a.innerText.trim();
                        let imgSrc = '';
                        
                        const container = a.closest('li, .item, .card, article, div.activity-wrapper, div[class*="event"]') || a.parentElement;
                        if (container) {
                            const img = container.querySelector('img');
                            if (img) imgSrc = img.src || img.getAttribute('data-src') || img.getAttribute('ng-src') || '';
                            
                            if (!title) {
                                const heading = container.querySelector('h1, h2, h3, h4, h5, h6, .title, .name, .txt');
                                if (heading) title = heading.innerText.trim();
                            }
                        }
                        
                        if (!imgSrc) {
                            const img = a.querySelector('img');
                            if (img) imgSrc = img.src || img.getAttribute('data-src') || '';
                        }
                        
                        if (title && title.length > 2) {
                            seen.add(url);
                            results.push({ title: title.replace(/\\s+/g, ' '), url: url, cover_image: imgSrc });
                        }
                    });
                    return results;
                }"""
            )
            
            if not events_data:
                print("SCRAPER: [TIXCRAFT] 警告！抓到 0 個活動。印出網頁前 1000 個字元供 Debug:")
                html_preview = await page.content()
                print(html_preview[:1000])

            for item in events_data:
                if not item['url']: continue
                db_event = db.query(Event).filter(Event.external_url == item['url']).first()
                if not db_event:
                    new_event = Event(
                        title=item['title'],
                        external_url=item['url'],
                        cover_image_url=item['cover_image'],
                        source_platform='tixcraft'
                    )
                    db.add(new_event)
                    new_event_titles.append(item['title']) # 記錄新活動
                else:
                    # 如果已存在，但原本沒有圖片，這次抓到了就更新它！
                    if not db_event.cover_image_url and item['cover_image']:
                        db_event.cover_image_url = item['cover_image']
                        db_event.title = item['title']

            db.commit()
            print(f"SCRAPER: [TIXCRAFT] Scrape successful. Upserted {len(events_data)} events into PostgreSQL.")
            
            # 向 Redis 發布通知
            if new_event_titles:
                REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                await redis_client.publish(
                    "data-updates",
                    json.dumps({"source": "tixcraft", "new_events": new_event_titles})
                )
                await redis_client.close()

            # ----------------------------------------------------
            # 深度爬蟲 (Deep Scraping) 階段：進入內頁抓取詳細資訊
            # ----------------------------------------------------
            events_to_deep_scrape = db.query(Event).filter(Event.source_platform == 'tixcraft', Event.description == None).limit(3).all()
            for ev in events_to_deep_scrape:
                try:
                    print(f"SCRAPER: [TIXCRAFT] 深度抓取內頁 -> {ev.title}")
                    await page.goto(ev.external_url, timeout=30000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    desc = await page.evaluate("""() => {
                        // 拓元的資訊通常在 table 裡，擷取前 400 字避免過長
                        const info = document.querySelector('.activity-info, #game-info, .table');
                        return info ? info.innerText.trim().substring(0, 400) + '\\n...' : '請點擊「前往原網站搶票」查看詳細資訊。';
                    }""")
                    ev.description = desc
                    db.commit()
                    
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"SCRAPER: [TIXCRAFT] 深度抓取失敗 {ev.external_url}: {e}")
                    db.rollback()

        except Exception as e:
            db.rollback()
            print(f"SCRAPER: [TIXCRAFT] An error occurred: {e}")
        finally:
            await browser.close()
            
    return new_event_titles
