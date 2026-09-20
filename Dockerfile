FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DECISION_DB=/app/data/decision.db

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码
COPY app ./app

# SQLite 数据目录（Compose 中挂载卷持久化）
RUN mkdir -p /app/data

EXPOSE 8000

# 启动时自动建库建表（见 app/main.py 的 lifespan）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
