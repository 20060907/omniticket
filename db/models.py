from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, func
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()

class Venue(Base):
    """場地或影城 (例如：台北小巨蛋、信義威秀)"""
    __tablename__ = 'venues'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    city = Column(String(50))
    address = Column(String(255))
    
    events = relationship("Event", back_populates="venue")

class Event(Base):
    """活動本體 (例如：周杰倫嘉年華世界巡迴演唱會、沙丘2)"""
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    category = Column(String(50), default="concert") # 例如：concert, movie, exhibition
    source_platform = Column(String(50)) # 例如：kktix, tixcraft, vscinemas
    external_url = Column(String(512), unique=True) # 原始售票連結
    cover_image_url = Column(String(512))
    
    venue_id = Column(Integer, ForeignKey('venues.id'))
    venue = relationship("Venue", back_populates="events")
    
    sessions = relationship("EventSession", back_populates="event", cascade="all, delete-orphan")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class EventSession(Base):
    """具體場次與售票時間 (例如：12/25 晚上 7 點場，開賣時間 11/01 中午 12 點)"""
    __tablename__ = 'event_sessions'
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey('events.id'), nullable=False)
    
    show_time = Column(DateTime(timezone=True)) # 演出/上映時間
    ticket_open_time = Column(DateTime(timezone=True)) # 開賣時間 (搶票雷達的核心)
    
    status = Column(String(50), default="upcoming") # available, sold_out, upcoming
    has_available_tickets = Column(Boolean, default=True) # 供前端快速判斷
    
    event = relationship("Event", back_populates="sessions")

class KeywordSubscription(Base):
    """使用者關鍵字訂閱 (匿名設備機制)"""
    __tablename__ = 'keyword_subscriptions'
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(255), index=True, nullable=False) # 存放前端隨機生成的設備 ID
    keyword = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Movie(Base):
    """電影資料 (例如：沙丘2、奧本海默)"""
    __tablename__ = 'movies'
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    release_date = Column(String(50))
    cover_image_url = Column(String(512))
    
    showtimes = relationship("Showtime", back_populates="movie", cascade="all, delete-orphan")

class Cinema(Base):
    """影城資料 (例如：台北信義威秀影城)"""
    __tablename__ = 'cinemas'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    region = Column(String(50)) # 例如：台北市、台中市
    
    showtimes = relationship("Showtime", back_populates="cinema", cascade="all, delete-orphan")

class Showtime(Base):
    """電影放映時刻表與訂票連結"""
    __tablename__ = 'showtimes'
    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey('movies.id'), nullable=False)
    cinema_id = Column(Integer, ForeignKey('cinemas.id'), nullable=False)
    show_time = Column(String(50)) # 例如: 2026-05-04 14:30
    booking_url = Column(String(512)) # 直達該場次的購票網址
    
    movie = relationship("Movie", back_populates="showtimes")
    cinema = relationship("Cinema", back_populates="showtimes")