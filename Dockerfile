FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Bake SQLite into the image (Cloud Run filesystem is ephemeral)
RUN python data/seed.py

ENV PORT=8080
ENV FOODIE_DATA_BACKEND=auto
ENV FOODIE_CACHE=on
ENV FOODIE_MODEL_DEFAULT=claude-sonnet-4-5

# Bind 0.0.0.0:$PORT — required on Cloud Run
CMD exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT}
