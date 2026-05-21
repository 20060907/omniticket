from pydantic import BaseModel
from typing import Optional, List

class Event(BaseModel):
    id: int
    title: str
    external_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    description: Optional[str] = None
    source_platform: str

    class Config:
        from_attributes = True

class SubscriptionRequest(BaseModel):
    device_id: str
    keywords: List[str]


class Cinema(BaseModel):
    id: int
    name: str
    region: Optional[str] = None

    class Config:
        from_attributes = True

class Showtime(BaseModel):
    id: int
    movie_id: int
    cinema_id: int
    show_time: Optional[str] = None
    booking_url: Optional[str] = None

    class Config:
        from_attributes = True

class Movie(BaseModel):
    id: int
    title: str
    cover_image_url: Optional[str] = None
    release_date: Optional[str] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True
