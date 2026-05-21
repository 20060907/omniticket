FROM node:22-bookworm-slim

WORKDIR /app

# 🎯 終極防禦：安裝編譯工具與系統級 libvips，強制 sharp 跳過容易斷線的 GitHub 下載！
RUN apt-get update && apt-get install -y python3 make g++ pkg-config libvips-dev && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--force"]