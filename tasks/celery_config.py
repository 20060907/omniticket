import os
from celery import Celery

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")

celery_app = Celery(
    'tasks',
    broker=f'redis://{REDIS_HOST}:6379/0',
    backend=f'redis://{REDIS_HOST}:6379/1',
    include=['tasks.scheduled_tasks']
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Taipei',
    enable_utc=True,
)
celery_app.conf.beat_schedule = {
    'update-kktix-every-5-minutes': {
        'task': 'tasks.scheduled_tasks.update_kktix_data_task',
        'schedule': 300.0, # 每 300 秒執行一次
    },
    'update-tixcraft-every-7-minutes': {
        'task': 'tasks.scheduled_tasks.update_tixcraft_data_task',
        'schedule': 420.0, # 每 420 秒執行一次
    },
    'update-ticketplus-every-6-minutes': {
        'task': 'tasks.scheduled_tasks.update_ticketplus_data_task',
        'schedule': 360.0, # 每 360 秒執行一次
    },
    'update-vieshow-every-10-minutes': {
        'task': 'tasks.scheduled_tasks.update_vieshow_data_task',
        'schedule': 600.0, # 每 600 秒執行一次
    },
    'cleanup-expired-events-daily': {
        'task': 'tasks.scheduled_tasks.cleanup_expired_events_task',
        'schedule': 86400.0, # 每天執行一次 (24小時 * 60分 * 60秒)
    },
}