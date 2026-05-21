import asyncio
import os
import json
import redis
from datetime import datetime, timedelta
from .celery_config import celery_app
from db.database import SessionLocal
from db import models
from .scrapers import scrape_kktix_events, scrape_tixcraft_events
from .vieshow_scraper import scrape_vieshow_events
from .ticketplus_scraper import scrape_ticketplus_events
from .email_service import notify_subscribers

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

@celery_app.task
def update_kktix_data_task():
    print("CELERY TASK: Received task to update KKTIX data.")
    db = SessionLocal()
    try:
        new_titles = asyncio.run(scrape_kktix_events(db))
        if new_titles:
            notify_subscribers(db, new_titles)
        print("CELERY TASK: KKTIX data update task finished.")
    finally:
        db.close()

@celery_app.task
def update_tixcraft_data_task():
    print("CELERY TASK: Received task to update TIXCRAFT data.")
    db = SessionLocal()
    try:
        new_titles = asyncio.run(scrape_tixcraft_events(db))
        if new_titles:
            notify_subscribers(db, new_titles)
        print("CELERY TASK: TIXCRAFT data update task finished.")
    finally:
        db.close()

@celery_app.task
def update_ticketplus_data_task():
    print("CELERY TASK: Received task to update TICKETPLUS data.")
    db = SessionLocal()
    try:
        new_titles = asyncio.run(scrape_ticketplus_events(db))
        if new_titles:
            notify_subscribers(db, new_titles)
        print("CELERY TASK: TICKETPLUS data update task finished.")
    finally:
        db.close()

@celery_app.task
def update_vieshow_data_task():
    print("CELERY TASK: Received task to update VIESHOW data.")
    db = SessionLocal()
    try:
        new_titles = asyncio.run(scrape_vieshow_events(db))
        if new_titles:
            notify_subscribers(db, new_titles)
        print("CELERY TASK: VIESHOW data update task finished.")
    except Exception as e:
        db.rollback()
        print(f"CELERY TASK: VIESHOW data update failed: {e}")
    finally:
        db.close()

@celery_app.task
def cleanup_expired_events_task():
    """每天執行一次：刪除建立超過 30 天的舊活動資料"""
    print("CELERY TASK: Running cleanup for expired events...")
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=30)
        deleted_count = db.query(models.Event).filter(models.Event.created_at < cutoff_date).delete()
        db.commit()
        print(f"CELERY TASK: Cleanup finished. Deleted {deleted_count} expired events.")
    except Exception as e:
        db.rollback()
        print(f"CELERY TASK: Cleanup failed: {e}")
    finally:
        db.close()