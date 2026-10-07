FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=7860
# One process only: running jobs and the cache live in memory.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
