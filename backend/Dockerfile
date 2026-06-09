FROM python:3.9-slim

WORKDIR /app

# Install system compilation packages for chroma, pgvector, and psutil
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    g++ \
    make \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set pythonpath so Python can find core module
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["sh", "-c", "python db_init.py && uvicorn main:app --host 0.0.0.0 --port 8000"]
