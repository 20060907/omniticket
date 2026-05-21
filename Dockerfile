# 🎯 升級到與你環境匹配的 Playwright 官方映像檔，避免版本衝突
FROM mcr.microsoft.com/playwright/python:v1.59.1-jammy

# 設定工作目錄
WORKDIR /app

# 複製依賴清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🎯 同時安裝 Chromium 與 WebKit，並加上 --with-deps 確保 Linux 底層依賴自動安裝齊全
RUN playwright install --with-deps chromium webkit

# 複製專案原始碼
COPY . .