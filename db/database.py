import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 從環境變數讀取資料庫連線 URL
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/ticketing_db")

engine = create_engine(DATABASE_URL)

# 建立一個可重複使用的 SessionLocal 工廠
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()