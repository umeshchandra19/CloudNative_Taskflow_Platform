FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY frontend frontend
RUN useradd --system --uid 1001 appuser
USER appuser
EXPOSE 8000
# gthread workers: each live-update (SSE) connection holds one thread, so threads are plentiful.
# initdb is idempotent and uses a Postgres advisory lock, so several containers can start together.
CMD ["sh", "-c", "python -m app.initdb && exec gunicorn 'app:create_app()' --bind 0.0.0.0:${PORT} --worker-class gthread --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-100} --timeout 120 --graceful-timeout 20 --access-logfile -"]
