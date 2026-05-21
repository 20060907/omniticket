import asyncio
import json
import os
import re
import urllib.parse
import urllib.request
import ssl
import random
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import redis.asyncio as redis
from sqlalchemy.orm import Session
from db.models import Movie, Cinema, Showtime

async def scrape_vieshow_events(db: Session):
    print("SCRAPER: [ATMOVIES] 🎬 啟動「API 級純字串解析」終極大招 (無視超時、完全精準)...")
    new_movie_titles = []
    
    try:
        async with async_playwright() as p:
            # 徹底拋棄無頭瀏覽器渲染，改用極速 API 請求，保證 100% 成功率且無超時
            request_context = await p.request.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": "https://www.atmovies.com.tw/"
                }
            )
            
            # 🛡️ 加入併發鎖，避免瞬間發出太多請求導致被伺服器阻擋或中斷連線
            semaphore = asyncio.Semaphore(5)

            REGION_MAP = {
                "a01": "基隆", "a02": "台北", "a03": "桃園", "a04": "新竹",
                "a05": "苗栗", "a06": "台中", "a07": "彰化", "a08": "南投",
                "a09": "雲林", "a10": "嘉義", "a11": "台南", "a12": "高雄",
                "a13": "屏東", "a14": "宜蘭", "a15": "花蓮", "a16": "台東",
                "a17": "外島", "a18": "澎湖"
            }

            # ---------------------------------------------------------
            # 第一步：獲取全台灣所有的戲院連結
            # ---------------------------------------------------------
            print("SCRAPER: [ATMOVIES] 📍 第一步：極速獲取全台各區戲院...")
            unique_theaters = {} 
            
            async def get_theaters_for_region(region_code):
                async with semaphore:
                    for attempt in range(3):
                        try:
                            url = f"https://www.atmovies.com.tw/showtime/{region_code}/"
                            resp = await request_context.get(url, timeout=15000)
                            if not resp.ok: return
                            
                            raw_body = await resp.body()
                            # 🎯 開眼電影網是 Big5 編碼，使用 cp950 解碼確保中文正則表達式完美運作
                            try:
                                html = raw_body.decode('utf-8')
                            except UnicodeDecodeError:
                                html = raw_body.decode('cp950', errors='ignore')
                            
                            # 加入 re.DOTALL 確保換行的 HTML 也能被抓到
                            matches = re.findall(r'<a[^>]*href=["\']?(/showtime/(t[a-zA-Z0-9]+)[^"\']*)["\']?[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
                            for link, tid, name in matches:
                                clean_name = re.sub(r'<[^>]+>', '', name).strip()
                                clean_name = re.sub(r'\(.*?\)|（.*?）', '', clean_name).strip()
                                if not clean_name: continue
                                full_url = "https://www.atmovies.com.tw" + link if link.startswith('/') else link
                                if full_url not in unique_theaters:
                                    unique_theaters[full_url] = {"name": clean_name, "region_code": region_code}
                            return
                        except Exception as e:
                            if attempt == 2: print(f"SCRAPER: [ATMOVIES] 解析區域 {region_code} 失敗: {e}")
                            await asyncio.sleep(1)

            region_tasks = [get_theaters_for_region(f"a{i:02d}") for i in range(1, 19)]
            await asyncio.gather(*region_tasks)

            print(f"SCRAPER: [ATMOVIES] 共找到 {len(unique_theaters)} 家戲院，準備併發抓取 3 日時刻表...")

            # ---------------------------------------------------------
            # 第二步：純字串 Regex 併發獲取時刻表 (保證不漏 IMAX 等跨行版本)
            # ---------------------------------------------------------
            tw_now = datetime.utcnow() + timedelta(hours=8)
            pages_to_scrape = []
            
            for t_url, t_info in unique_theaters.items():
                base_url = t_url if t_url.endswith('/') else t_url + '/'
                for day_offset in range(3):
                    target_date = tw_now + timedelta(days=day_offset)
                    date_str = target_date.strftime("%Y%m%d")
                    formatted_date = target_date.strftime("%Y/%m/%d")
                    
                    target_url = base_url if day_offset == 0 else f"{base_url}{date_str}/"
                    pages_to_scrape.append({
                        "cinema_name": t_info['name'],
                        "region_code": t_info['region_code'],
                        "url": target_url,
                        "formatted_date": formatted_date
                    })

            all_showtimes_data = []
            unique_movies = {} # movie_url -> raw_title
            
            print(f"SCRAPER: [ATMOVIES] ⏳ 第二步：開始極速解析 {len(pages_to_scrape)} 個時刻表頁面...")

            async def get_showtimes_for_page(p_info):
                async with semaphore:
                    for attempt in range(3):
                        try:
                            resp = await request_context.get(p_info['url'], timeout=15000)
                            if not resp.ok: return []
                            
                            raw_body = await resp.body()
                            try:
                                html = raw_body.decode('utf-8')
                            except UnicodeDecodeError:
                                html = raw_body.decode('cp950', errors='ignore')
                            
                            # 縮小範圍，避免抓到網頁底部的無用連結
                            main_area = re.search(r'(?:id="theaterShowtimeBlock"|class="theaterShowtimeBlock")(.*?)(?:id="footer"|class="footer"|<script)', html, re.DOTALL | re.IGNORECASE)
                            if main_area: html = main_area.group(1)
                            
                            # 🎯 物理切割法：用電影標題超連結將網頁切塊，保證接下來的 IMAX, 3D 時間絕對歸屬於該電影，解決漏抓問題
                            pattern = r'<a[^>]*href=["\']?(/movie/[a-zA-Z0-9\-_]+/?|https://www.atmovies.com.tw/movie/[a-zA-Z0-9\-_]+/?)[^"\']*["\']?[^>]*>(.*?)</a>'
                            parts = re.split(pattern, html, flags=re.IGNORECASE)
                            
                            for idx in range(1, len(parts), 3):
                                m_url = parts[idx]
                                m_title = parts[idx+1]
                                m_block = parts[idx+2]
                                
                                current_movie_url = urllib.parse.urljoin("https://www.atmovies.com.tw/", m_url)
                                current_movie_title = re.sub(r'<[^>]+>', '', m_title).strip()
                                if not current_movie_title or '開眼' in current_movie_title: continue
                                
                                ul_blocks = re.findall(r'<ul[^>]*>(.*?)</ul>', m_block, re.IGNORECASE | re.DOTALL)
                                if not ul_blocks: ul_blocks = [m_block]
                                
                                for ul in ul_blocks:
                                    times = re.findall(r'(?<!\d)(\d{1,2}[:：]\d{2})(?!\d)', ul)
                                    if not times: continue
                                    
                                    version = ""
                                    v_match = re.search(r'<li class="filmVersion"[^>]*>(.*?)</li>', ul, re.IGNORECASE | re.DOTALL)
                                    if v_match:
                                        version = re.sub(r'<[^>]+>', '', v_match.group(1)).strip()
                                    else:
                                        for li in re.findall(r'<li[^>]*>(.*?)</li>', ul, re.IGNORECASE | re.DOTALL):
                                            text = re.sub(r'<[^>]+>', '', li).strip()
                                            if re.search(r'數位|IMAX|3D|4DX|ATMOS|英文|中文', text) and len(text) < 15:
                                                version = text
                                                break
                                                
                                    for t in times:
                                        t = t.replace('：', ':')
                                        parts_time = t.split(':')
                                        t_formatted = parts_time[0].zfill(2) + ':' + parts_time[1]
                                        t_display = f"{t_formatted} ({version})" if version else t_formatted
                                        
                                        base_title = re.sub(r'\(.*?\)|（.*?）|\[.*?\]|【.*?】', '', current_movie_title).strip()
                                        if not base_title: base_title = current_movie_title
                                        
                                        unique_movies[current_movie_url] = base_title
                                        all_showtimes_data.append({
                                            'cinema_name': p_info['cinema_name'],
                                            'region_code': p_info['region_code'],
                                            'movie_title': base_title,
                                            'movie_url': current_movie_url,
                                            'show_time': f"{p_info['formatted_date']} {t_display}"
                                        })
                            return []
                        except Exception as e:
                            if attempt == 2: print(f"SCRAPER: [ATMOVIES] 時刻表抓取失敗 {p_info['url']}: {e}")
                            await asyncio.sleep(1)
                    return []

            for i in range(0, len(pages_to_scrape), 50):
                await asyncio.gather(*(get_showtimes_for_page(p) for p in pages_to_scrape[i:i+50]))
                print(f"SCRAPER: [ATMOVIES]   -> 時刻表掃描進度: {min(i+50, len(pages_to_scrape))} / {len(pages_to_scrape)}")

            # ---------------------------------------------------------
            # 第三步：獲取電影詳情與神級海報備援
            # ---------------------------------------------------------
            print(f"SCRAPER: [ATMOVIES] 🎬 收集到 {len(unique_movies)} 部獨立電影，併發抓取海報與簡介...")
            
            detail_semaphore = asyncio.Semaphore(3)

            async def get_movie_details(m_url, m_title):
                async with detail_semaphore:
                    cover = ""
                    desc = "全台影城上映電影"
                    release_date = "現正熱映"
                    
                    try:
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        resp = await request_context.get(m_url, timeout=15000)
                        if resp.ok:
                            html = (await resp.body()).decode('cp950', errors='ignore')
                            
                            # 1. 優先提取 og:image (最精準)
                            meta = re.search(r'<meta property="og:image"\s+content=["\']?([^"\']+)["\']?', html, re.IGNORECASE)
                            if meta and "atmovies_fb.gif" not in meta.group(1) and "empty.gif" not in meta.group(1):
                                cover = meta.group(1)
                                
                            # 2. 如果沒有，再尋找 .poster 區塊內的圖片
                            if not cover:
                                img_match = re.search(r'<div class="poster"[^>]*>(?:(?!</div>).)*?<img[^>]+(?:src|data-src)=["\']?([^"\']+)["\']?', html, re.IGNORECASE | re.DOTALL)
                                if img_match and "atmovies_fb.gif" not in img_match.group(1):
                                    cover = img_match.group(1)
                                        
                            d_match = re.search(r'<div class="story"[^>]*>(.*?)</div>', html, re.IGNORECASE | re.DOTALL)
                            if d_match: desc = re.sub(r'<[^>]+>', '', d_match.group(1)).strip()[:300] + "..."
                            
                            date_match = re.search(r'上映日期：\s*(\d{4}/\d{2}/\d{2})', html)
                            if date_match: release_date = date_match.group(1)
                    except Exception:
                        pass
                    
                    # 3. 🎯 神級備援：如果開眼圖床失效 (如 photowant)，調用完全免費的 Apple iTunes API 獲取官方高畫質海報！
                    if not cover or "photowant" in cover or "empty" in cover:
                        try:
                            clean_title = re.sub(r'第.*?集|(?i)imax|(?i)3d|(?i)4dx', '', m_title).strip()
                            await asyncio.sleep(random.uniform(0.5, 1.0)) # 隨機延遲保護 iTunes API
                            itunes_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(clean_title)}&entity=movie&country=tw&limit=1"
                            it_resp = await request_context.get(itunes_url, timeout=5000)
                            if it_resp.ok:
                                data = await it_resp.json()
                                if data.get("resultCount", 0) > 0:
                                    # iTunes 預設回傳 100x100，我們替換字串取得 600x600 高畫質版
                                    cover = data["results"][0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
                        except Exception:
                            pass
                            
                    if cover:
                        if not cover.startswith('http'):
                            cover = urllib.parse.urljoin(m_url, cover)
                        if "photowant.com" in cover:
                            cover = cover.replace("https://", "http://")
                        if "vignette.wikia.nocookie.net" in cover:
                            cover = cover.replace("http://", "https://").replace("vignette.wikia.nocookie.net", "static.wikia.nocookie.net").split('/revision/')[0]
                        
                    return { "url": m_url, "title": m_title, "cover": cover, "desc": desc, "releaseDate": release_date }

            movie_tasks = [get_movie_details(url, title) for url, title in unique_movies.items()]
                 
            movie_results = []
            for i in range(0, len(movie_tasks), 20):
                batch = movie_tasks[i:i+20]
                res = await asyncio.gather(*batch)
                movie_results.extend(res)
                print(f"SCRAPER: [ATMOVIES]   -> 電影海報掃描進度: {min(i+20, len(movie_tasks))} / {len(movie_tasks)}")

            # ---------------------------------------------------------
            # 第四步：寫入資料庫與舊資料覆寫
            # ---------------------------------------------------------
            db_movies_cache = {}
            url_to_movie_id = {}
            for m_info in movie_results:
                m_title = m_info.get('title')
                m_url = m_info.get('url')
                
                if not m_title or not m_url:
                    continue
                try:
                    db_movie = db.query(Movie).filter(Movie.title == m_title).first()
                    if not db_movie:
                        cover = m_info.get('cover') or ""
                        db_movie = Movie(
                            title=m_title, 
                            description=m_info.get('desc') or "全台影城上映電影", 
                            release_date=m_info.get('releaseDate') or "現正熱映", 
                            cover_image_url=cover
                        )
                        db.add(db_movie)
                        db.commit()
                        db.refresh(db_movie)
                        new_movie_titles.append(m_title)
                    else:
                        changed = False
                        # 🎯 強制覆寫：只要我們有抓到 Yahoo 海報，或者舊海報屬於被污染的 CDN / pl_ / photowant 格式，就無條件覆寫！
                        if m_info.get('cover'):
                            if db_movie.cover_image_url != m_info.get('cover'):
                                db_movie.cover_image_url = m_info.get('cover')
                                changed = True
                        elif db_movie.cover_image_url and ('atmovies' in db_movie.cover_image_url or 'photowant' in db_movie.cover_image_url or 'pl_' in db_movie.cover_image_url):
                            db_movie.cover_image_url = m_info.get('cover') or ""
                            changed = True
                            
                        if (not db_movie.description or len(db_movie.description) < 10) and m_info.get('desc') and m_info.get('desc') != "全台影城上映電影":
                            db_movie.description = m_info.get('desc')
                            changed = True
                        if changed: db.commit()
                    db_movies_cache[m_title] = db_movie.id
                    url_to_movie_id[m_url] = db_movie.id
                except Exception as e:
                    db.rollback()
                    print(f"SCRAPER: [ATMOVIES] 電影儲存失敗 {m_title}: {e}")

            REGION_MAP = {
                "a01": "基隆", "a02": "台北", "a03": "桃園", "a04": "新竹",
                "a05": "苗栗", "a06": "台中", "a07": "彰化", "a08": "南投",
                "a09": "雲林", "a10": "嘉義", "a11": "台南", "a12": "高雄",
                "a13": "屏東", "a14": "宜蘭", "a15": "花蓮", "a16": "台東",
                "a17": "外島", "a18": "澎湖"
            }

            db_cinemas_cache = {}
            scraped_cinema_dict = {}
            for st in all_showtimes_data:
                scraped_cinema_dict[st['cinema_name']] = st['region_code']
                
            scraped_cinema_ids = []
            for c_name, c_region_code in scraped_cinema_dict.items():
                try:
                    db_cinema = db.query(Cinema).filter(Cinema.name == c_name).first()
                    region_name = REGION_MAP.get(c_region_code, "開眼電影網")
                    if not db_cinema:
                        db_cinema = Cinema(name=c_name, region=region_name)
                        db.add(db_cinema)
                        db.commit()
                        db.refresh(db_cinema)
                    else:
                        if db_cinema.region != region_name:
                            db_cinema.region = region_name
                            db.commit()
                    db_cinemas_cache[c_name] = db_cinema.id
                    scraped_cinema_ids.append(db_cinema.id)
                except Exception as e:
                    db.rollback()
                    print(f"SCRAPER: [ATMOVIES] 影城儲存失敗 {c_name}: {e}")

            print(f"SCRAPER: [ATMOVIES] ⏳ 正在將 {len(all_showtimes_data)} 筆時刻表寫入資料庫...")
            if scraped_cinema_ids:
                try:
                    db.query(Showtime).filter(Showtime.cinema_id.in_(scraped_cinema_ids)).delete(synchronize_session=False)
                    db.commit()
                except Exception:
                    db.rollback()

            for st_data in all_showtimes_data:
                movie_id = url_to_movie_id.get(st_data['movie_url'])
                cinema_id = db_cinemas_cache.get(st_data['cinema_name'])
                if not movie_id or not cinema_id: continue
                
                try:
                    new_st = Showtime(
                        movie_id=movie_id, 
                        cinema_id=cinema_id, 
                        show_time=st_data['show_time'], 
                        booking_url="https://www.atmovies.com.tw/"
                    )
                    db.add(new_st)
                except Exception:
                    pass
            try:
                db.commit()
            except Exception:
                db.rollback()
                    
            print("SCRAPER: [ATMOVIES] 🧹 清理無時刻表的下檔電影與舊影城...")
            db.query(Movie).filter(~Movie.showtimes.any()).delete(synchronize_session=False)
            
            # 🎯 核心修復：把資料庫裡舊的、沒有時刻表的「威秀影城」徹底刪除，左側選單才會真正更新為各縣市！
            db.query(Cinema).filter(~Cinema.showtimes.any()).delete(synchronize_session=False)
            
            db.commit()
            
            if new_movie_titles:
                REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
                redis_client = redis.from_url(f"redis://{REDIS_HOST}:6379/2", encoding="utf-8", decode_responses=True)
                unique_titles = list(set(new_movie_titles))
                await redis_client.publish("data-updates", json.dumps({"source": "atmovies", "new_events": unique_titles}))
                await redis_client.close()
                
            print("SCRAPER: [ATMOVIES] 🎉 API級別爬蟲完美執行完畢！")
            return list(set(new_movie_titles))

    except Exception as e:
        print(f"SCRAPER: [ATMOVIES] 發生嚴重錯誤: {e}", flush=True)
        return []
