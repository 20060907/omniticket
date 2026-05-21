from sqlalchemy.orm import Session
from db import models

def get_events_by_source(db: Session, source: str, skip: int = 0, limit: int = 100):
    return db.query(models.Event).filter(models.Event.source_platform == source).offset(skip).limit(limit).all()

def update_device_subscriptions(db: Session, device_id: str, keywords: list[str]):
    # 先刪除該設備原本所有的訂閱紀錄
    db.query(models.KeywordSubscription).filter(models.KeywordSubscription.device_id == device_id).delete()
    # 建立新的訂閱紀錄
    new_subs = [models.KeywordSubscription(device_id=device_id, keyword=kw) for kw in keywords]
    if new_subs:
        db.add_all(new_subs)
    db.commit()
    return True