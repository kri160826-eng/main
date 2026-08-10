# ---------------------------------------------------------------------------
# Data Modeler Agent - Streamlit container for Cloud Run
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY src ./src
COPY app.py .

# Cloud Run sends traffic to $PORT (default 8080). Streamlit must bind 0.0.0.0.
EXPOSE 8080

CMD streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
