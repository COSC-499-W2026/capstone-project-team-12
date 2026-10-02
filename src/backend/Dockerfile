FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# supercronic runs the scheduled jobs in the scheduler container (same image)
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64} /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .

EXPOSE 8000
# One Uvicorn worker: the OCR concurrency limit lives in process memory (see Spec).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
