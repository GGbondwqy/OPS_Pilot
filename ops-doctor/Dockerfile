# OpsDoctor 容器镜像（可选部署方式，与 systemd 部署二选一）
# 构建：docker build -t ops-doctor:latest .
# 运行：docker run -d --name ops-doctor -p 8000:8000 -v ops-doctor-data:/data \
#         -e OPSDOCTOR_LLM_BASE_URL=... -e OPSDOCTOR_LLM_API_KEY=... -e OPSDOCTOR_LLM_MODEL=... \
#         ops-doctor:latest
FROM python:3.12-slim

WORKDIR /app
ENV OPSDOCTOR_DATA_DIR=/data \
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

CMD ["sh", "-c", "python -m app.cli init && exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${OPSDOCTOR_PORT:-8000}"]
