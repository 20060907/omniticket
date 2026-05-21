import asyncio
import json
from typing import List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import redis.asyncio as redis

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
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                print(f"Received update from Redis: {message['data']}")
                await manager.broadcast(message['data'].decode('utf-8'))
    except asyncio.CancelledError:
        # 當任務被取消時，安全地關閉 Redis 連線
        await pubsub.unsubscribe("data-updates")
        await r.aclose()

# 應用啟動時，建立所有資料庫表格
models.Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時：建立背景任務
    task = asyncio.create_task(redis_listener(manager))
    yield
    # 關閉時：取消背景任務
    task.cancel()

app = FastAPI(title="Ticketing Platform API", lifespan=lifespan)

# 設定 CORS，允許前端 (Vite 預設 port 5173) 存取 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], # 允許的來源
    allow_credentials=True,
    allow_methods=["*"], # 允許所有 HTTP 方法 (GET, POST 等)
    allow_headers=["*"], # 允許所有標頭
)

@app.get("/api/events/{source}", response_model=List[schemas.Event])
def read_events(source: str, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    從 PostgreSQL 資料庫中讀取指定來源的活動資料。
    """
    events = crud.get_events_by_source(db, source=source, skip=skip, limit=limit)
    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No events found for source '{source}'. The background worker might be running."
        )
    return events

@app.post("/api/subscriptions")
def update_subscriptions(sub_req: schemas.SubscriptionRequest, db: Session = Depends(get_db)):
    """
    接收前端傳來的匿名設備 ID 與關鍵字列表，並儲存到資料庫。
    """
    crud.update_device_subscriptions(db, sub_req.device_id, sub_req.keywords)
    return {"message": "Subscriptions updated successfully"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # 保持連線開啟
    except WebSocketDisconnect:
        manager.disconnect(websocket)