FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000

WORKDIR /app

# ffmpeg: server-side thumbnails and durations (used for API uploads)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py translations.py ./
COPY templates ./templates
COPY static ./static

# Videos, thumbnails and the database live here - mount a volume to keep them.
VOLUME ["/app/data"]
EXPOSE 5000

CMD ["python", "app.py"]
