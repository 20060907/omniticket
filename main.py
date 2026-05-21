import asyncio
import json
from typing import List
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
import redis.asyncio as redis
from fastapi.responses import Response, RedirectResponse
from fastapi import Request
import urllib.request
import urllib.parse
import ssl
import re

from app import crud, schemas
from db import models
from db.database import engine, get_db

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

async def redis_listener(manager: ConnectionManager):
    r = await redis.from_url("redis://redis:6379/0")
    pubsub = r.pubsub()
    await pubsub.subscribe("data-updates")
    async for message in pubsub.listen():
        if message["type"] == "message":
            print(f"Received update from Redis: {message['data']}")
            await manager.broadcast(message['data'].decode('utf-8'))

app = FastAPI(title="Ticketing Platform API")

# 設定 CORS，允許前端 (Vite 預設 port 5173) 存取 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 🎯 允許所有來源，確保部署至雲端後 IP 或網域不會被 CORS 阻擋
    allow_credentials=True,
    allow_methods=["*"], # 允許所有 HTTP 方法 (GET, POST 等)
    allow_headers=["*"], # 允許所有標頭
)

@app.on_event("startup")
async def startup_event():
    # 應用啟動時，建立表格，並自動嘗試為舊的訂閱表加入 email 欄位
    models.Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS email VARCHAR;"))
    except Exception as e:
        print(f"DB 欄位檢查/更新提示 (可忽略): {e}")
        
    asyncio.create_task(redis_listener(manager))

@app.get("/api/events")
def read_all_events(
    request: Request,
    skip: int = 0, 
    limit: int = 12, 
    platform: str = "all", 
    search: str = "",
    fav_ids: str = "",
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    # 🎯 核心修復：將前端請求的「電影類別」重新導向正確的 Movie 資料表！
    if platform == "movie_all" or platform == "vieshow":
        query = db.query(models.Movie)
        if search:
            query = query.filter(models.Movie.title.ilike(f"%{search}%"))
        if fav_ids:
            ids = [int(x) for x in fav_ids.split(",") if x.isdigit()]
            if ids:
                query = query.filter(models.Movie.id.in_(ids))
        total = query.count()
        movies = query.order_by(models.Movie.id.desc()).offset(skip).limit(limit).all()
        
        events = []
        for m in movies:
            events.append({
                "id": m.id,
                "title": m.title,
                "external_url": "#",
                "cover_image_url": m.cover_image_url,
                "description": m.description,
                "source_platform": "atmovies" # 標示為開眼電影網，前端就絕對不會顯示威秀標籤
            })
        return {"total": total, "events": events}

    query = db.query(models.Event)
    
    if platform == "general_all":
        query = query.filter(models.Event.source_platform.in_(['kktix', 'tixcraft', 'ticketplus']))
    elif platform != "all":
        query = query.filter(models.Event.source_platform == platform)
        
    # 🎯 終極除靈：當在首頁「全部」分頁時，強制過濾掉 Event 資料庫裡舊的威秀幽靈資料！
    if platform == "all":
        query = query.filter(models.Event.source_platform.notin_(['vieshow', 'atmovies']))

    if search:
        query = query.filter(models.Event.title.ilike(f"%{search}%"))
    if fav_ids:
        ids = [int(x) for x in fav_ids.split(",") if x.isdigit()]
        if ids:
            query = query.filter(models.Event.id.in_(ids))
            
    total = query.count()
    events = query.order_by(models.Event.created_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "events": events}

@app.get("/api/events/{source}", response_model=List[schemas.Event])
def read_events(request: Request, source: str, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    從 PostgreSQL 資料庫中讀取指定來源的活動資料。
    """
    base_url = str(request.base_url).rstrip("/")
    # 🎯 核心修復：攔截前端舊有路徑，將威秀請求導向最新的電影資料表！
    if source == "vieshow" or source == "movie_all":
        movies = db.query(models.Movie).order_by(models.Movie.id.desc()).offset(skip).limit(limit).all()
        events = []
        for m in movies:
            events.append({
                "id": m.id,
                "title": m.title,
                "external_url": "#",
                "cover_image_url": m.cover_image_url,
                "description": m.description,
                "source_platform": "atmovies" # 標示為 atmovies，前端的威秀標籤就會被我們寫的 CSS 徹底消滅！
            })
        return events

    events = crud.get_events_by_source(db, source=source, skip=skip, limit=limit)
    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No events found for source '{source}'. The background worker might be running."
        )
    return events

@app.get("/api/movies")
def read_movies(skip: int = 0, limit: int = 24, search: str = "", fav_ids: str = "", db: Session = Depends(get_db)):
    """讀取電影清單"""
    query = db.query(models.Movie)
    if search:
        query = query.filter(models.Movie.title.ilike(f"%{search}%"))
        
    if fav_ids:
        ids = [int(x) for x in fav_ids.split(",") if x.isdigit()]
        if ids:
            query = query.filter(models.Movie.id.in_(ids))
            
    total = query.count()
    movies = query.order_by(models.Movie.id.desc()).offset(skip).limit(limit).all()
    return {"total": total, "movies": movies}

@app.get("/api/movies/{movie_id}/showtimes")
def read_movie_showtimes(movie_id: int, db: Session = Depends(get_db)):
    """取得特定電影的所有影城與時刻表"""
    showtimes = db.query(models.Showtime).filter(models.Showtime.movie_id == movie_id).all()
    result = {}
    for st in showtimes:
        cinema_name = st.cinema.name
        if cinema_name not in result:
            result[cinema_name] = []
        result[cinema_name].append({
            "id": st.id,
            "time": st.show_time,
            "booking_url": st.booking_url
        })
    return [{"cinema": k, "showtimes": v} for k, v in result.items()]

@app.post("/api/subscriptions")
def update_subscriptions(sub_req: dict, db: Session = Depends(get_db)):
    """
    接收前端傳來的匿名設備 ID、關鍵字列表與 Email，並儲存到資料庫。
    """
    device_id = sub_req.get("device_id")
    keywords = sub_req.get("keywords", [])
    email = sub_req.get("email", "")
    if not device_id: return {"error": "Missing device_id"}
    
    try:
        # 使用原生 SQL 儲存，避免 Pydantic models 未更新造成的錯誤
        db.execute(text("""
            INSERT INTO subscriptions (device_id, keywords, email)
            VALUES (:dev_id, :kw, :em)
            ON CONFLICT (device_id)
            DO UPDATE SET keywords = EXCLUDED.keywords, email = EXCLUDED.email
        """), {"dev_id": device_id, "kw": json.dumps(keywords), "em": email})
        db.commit()
    except Exception as e:
        db.rollback()
        print("Update subscription failed", e)
    return {"message": "Subscriptions updated successfully"}


@app.get("/api/proxy-image")
def proxy_image(url: str = ""):
    """
    後端圖片代理：偽裝 Referer 並避開前端憑證與防盜鏈阻擋
    """
    try:
        if not url or url == "null":
            raise ValueError("URL is empty")
            
        url = urllib.parse.unquote(url).strip()
        
        referer = "https://www.atmovies.com.tw/" if ("atmovies" in url or "photowant" in url or "wikia" in url) else "https://www.vscinemas.com.tw/"
        
        # 🎯 關鍵修復：只針對過期的 wikia 或舊版 atmovies / photowant 圖片降級 HTTP，新的 cdn.atmovies.com.tw 支援 HTTPS！
        if "vignette.wikia" in url or "photowant" in url or ("atmovies" in url and "cdn.atmovies" not in url):
            url = url.replace("https://", "http://")
            
        req = urllib.request.Request(
            url, 
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Referer': referer,
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            }
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            return Response(content=response.read(), media_type=response.headers.get('Content-Type', 'image/jpeg'))
    except Exception as e:
        print(f"Proxy Image Error for {url}: {e}")
        return RedirectResponse(url="https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=400&auto=format&fit=crop")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # 保持連線開啟
    except WebSocketDisconnect:
        manager.disconnect(websocket)