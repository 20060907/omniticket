FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# 設定工作目錄
WORKDIR /app

# 複製依賴清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安裝 Playwright 瀏覽器 (官方 image 預設已安裝部分，但保險起見再執行一次)
RUN playwright install chromium
RUN playwright install-deps chromium

# 複製專案原始碼
COPY . .