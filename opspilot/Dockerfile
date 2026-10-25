# OpsPilot 容器镜像（可选部署方式，与 systemd 部署二选一）
# 构建：docker build -t opspilot:latest .
# 运行：docker run -d --name opspilot -p 8000:8000 -v opspilot-data:/data \
#         -e OPSPILOT_LLM_BASE_URL=... -e OPSPILOT_LLM_API_KEY=... -e OPSPILOT_LLM_MODEL=... \
#         opspilot:latest
FROM python:3.12-slim

WORKDIR /app
ENV OPSPILOT_DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY knowledge/ ./knowledge/
COPY fixtures/ ./fixtures/

# 数据（数据库/令牌/通知）放卷里，容器重建不丢
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "python -m app.cli init && exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${OPSPILOT_PORT:-8000}"]
