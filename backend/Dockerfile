FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app/backend
RUN useradd --create-home --uid 10001 windlens && mkdir -p /data && chown windlens:windlens /data
USER windlens
ENV DATABASE_URL=sqlite:////data/windlens.sqlite
EXPOSE 8000
CMD ["sh", "-c", "python -m backend.app.ingest && exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-level info"]
