import asyncio
import json
from playwright.async_api import async_playwright
import redis.asyncio as redis
from sqlalchemy.orm import Session
from db.models import Event
import os

async def scrape_ticketplus_events(db: Session):
    """
    使用 Playwright 抓取遠大售票 (Ticket Plus) 活動
    """
    print("SCRAPER: [TICKETPLUS] Starting scrape job...")
    new_event_titles = []
    async with async_playwright() as p:
        # 啟動無頭瀏覽器，加入全套反反爬蟲參數
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
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        # 【終極大絕招】網路攔截器：不管前端怎麼隱藏，直接攔截底層的 JSON API 資料！
        api_responses = []
        async def handle_response(response):
            if "json" in response.headers.get("content-type", ""):
                try:
                    data = await response.json()
                    api_responses.append(data)
                except Exception:
                    pass
        page.on("response", handle_response)

        # 注入 JS 隱藏 webdriver 標記
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            """
        )

        try:
            # 遠大售票首頁或探索頁面
            await page.goto("https://ticketplus.com.tw/", timeout=60000)
            
            try:
                # 明確等待 Vue 應用程式掛載 (Vuetify 通常會有 .v-application，或 id="app")
                await page.wait_for_selector('.v-application, #app', timeout=20000)
                
                print("SCRAPER: [TICKETPLUS] 觸發自動向下捲動，攔截更多底層 API...")
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
                await page.wait_for_timeout(5000) # 加長等待時間，確保觸發所有底層 API
            except Exception as e:
                title = await page.title()
                print(f"SCRAPER: [TICKETPLUS] 頁面載入可能未完全，目前標題: '{title}'。錯誤: {e}")

            # 獲取 Nuxt/Vue 的初始狀態 (SSR 網頁不會發 API，資料藏在 window 裡)
            try:
                state_data = await page.evaluate("() => window.__NUXT__ || window.__INITIAL_STATE__ || {}")
                api_responses.append(state_data)
            except Exception:
                pass

            page_title = await page.title()
            print(f"SCRAPER: [TICKETPLUS] 網頁標題: '{page_title}'，共攔截到 {len(api_responses)} 個底層 API 回應")

            events_data = []
            seen_ids = set()
            
            # 遞迴解析 JSON，把所有看起來像活動的物件通通抓出來
            def extract_events_from_json(obj, possible_id=""):
                if isinstance(obj, dict):
                    act_id = str(obj.get("activityId") or obj.get("productId") or obj.get("id") or possible_id)
                    title = obj.get("title") or obj.get("activityName") or obj.get("name")
                    
                    if len(act_id) > 10 and title and isinstance(title, str):
                        if act_id not in seen_ids:
                            seen_ids.add(act_id)
                            cover = obj.get("picBigHomeThumbnail") or obj.get("picBigBanner") or obj.get("mobileCover") or obj.get("cover") or obj.get("imageUrl") or ""
                            events_data.append({
                                "title": title.strip().replace('\n', ' '),
                                "url": f"https://ticketplus.com.tw/activity/{act_id}",
                                "cover_image": cover
                            })
                    for k, v in obj.items():
                        extract_events_from_json(v, possible_id=k)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_events_from_json(item)

            # 掃描所有攔截到的網路回應與 Vue 狀態
            for data in api_responses:
                extract_events_from_json(data)
                
            if not events_data:
                print(f"SCRAPER: [TICKETPLUS] 無法從 API 找到活動，攔截包數量: {len(api_responses)}")
                for i, data in enumerate(api_responses):
                    snippet = str(data)[:500]
                    print(f"SCRAPER: [TICKETPLUS] 攔截包 {i} 預覽: {snippet}")


            for item in events_data:
                if not item['url']: continue
                db_event = db.query(Event).filter(Event.external_url == item['url']).first()
                if not db_event:
                    new_event = Event(
                        title=item['title'],
                        external_url=item['url'],
                        cover_image_url=item['cover_image'],
                        source_platform='ticketplus'
                    )
                    db.add(new_event)
                    new_event_titles.append(item['title'])
                else:
                    if not db_event.cover_image_url and item['cover_image']:
                        db_event.cover_image_url = item['cover_image']
                        db_event.title = item['title']

            db.commit()
            print(f"SCRAPER: [TICKETPLUS] Scrape successful. Upserted {len(events_data)} events.")
            
            if new_event_titles:
                REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                await redis_client.publish(
                    "data-updates",
                    json.dumps({"source": "ticketplus", "new_events": new_event_titles})
                )
                await redis_client.close()

        except Exception as e:
            db.rollback()
            print(f"SCRAPER: [TICKETPLUS] An error occurred: {e}")
        finally:
            await browser.close()
            
    return new_event_titles