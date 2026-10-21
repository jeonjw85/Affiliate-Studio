FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_HOST=0.0.0.0 \
    STUDIO_DATA_DIR=/app/data

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home studio \
    && mkdir /app/data \
    && chown studio:studio /app/data

COPY run.py ./
COPY studio ./studio
USER studio
EXPOSE 8787
CMD ["python", "run.py"]
