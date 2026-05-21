# 🎯 放棄難搞的微軟 Tag，改用最穩定且萬用的 Python 官方映像檔！
FROM python:3.11-bookworm

# 設定工作目錄
WORKDIR /app

# 複製依賴清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🎯 同時安裝 Chromium 與 WebKit，並加上 --with-deps 確保 Linux 底層依賴自動安裝齊全
RUN playwright install --with-deps chromium webkit

# 複製專案原始碼
COPY . .