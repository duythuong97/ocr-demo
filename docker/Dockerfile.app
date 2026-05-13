# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile some wheels (tree-sitter, lxml, pillow, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libffi-dev \
        libssl-dev \
        libjpeg-dev \
        zlib1g-dev \
        libpoppler-cpp-dev \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install PyTorch CPU-only first to avoid downloading 1.5GB of CUDA libraries
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --retries 10 --timeout 300 \
    --index-url https://download.pytorch.org/whl/cpu \
    torch
# Install remaining heavy ML packages (torch already present, skips CUDA pull)
RUN pip install --no-cache-dir --retries 10 --timeout 300 \
    sentence-transformers \
    tree-sitter-languages \
    qdrant-client
RUN pip install --no-cache-dir --retries 10 --timeout 300 -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Runtime system deps:
#   tesseract      — OCR for image/scanned-PDF support
#   poppler-utils  — pdf2image needs pdftoppm
#   libmagic       — file-type detection (markitdown)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-jpn \
        tesseract-ocr-eng \
        poppler-utils \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy all installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Non-root user for security
RUN useradd -m -u 1000 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 5100

# gunicorn is not in requirements (app uses socketio which needs eventlet/gevent
# or the built-in werkzeug async mode). Use the app's own runner.
CMD ["python", "app.py"]
