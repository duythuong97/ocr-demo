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
# CPU-only wheel from the PyTorch index; avoids pulling the large CUDA build
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --retries 10 --timeout 300 \
    --index-url https://download.pytorch.org/whl/cpu \
    torch
# Other heavy ML packages — separate layer for better cache reuse
RUN pip install --no-cache-dir --retries 10 --timeout 300 \
    sentence-transformers \
    tree-sitter-languages \
    qdrant-client
RUN pip install --no-cache-dir --retries 10 --timeout 300 -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Runtime system deps:
#   poppler-utils  — pdf2image needs pdftoppm
#   libmagic       — file-type detection (markitdown)
#   libgl1/libglib2/libgomp — OpenCV + PaddleOCR runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libmagic1 \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy all installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy entrypoint first (explicit — docker/ is largely excluded by .dockerignore)
COPY docker/entrypoint.sh /entrypoint.sh
# Strip Windows CRLF line endings that Git may have introduced on a Windows host.
RUN sed -i 's/\r$//' /entrypoint.sh

# Copy application source
COPY . .

RUN chmod +x /entrypoint.sh

EXPOSE 5100

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "app.py"]
