FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EASYOCR_MODULE_PATH=/opt/easyocr

# supercronic runs the scheduled jobs in the scheduler container (same image)
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.33
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64} /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY requirements.txt ./
# requirements.txt selects the CPU-only PyTorch build; fail loudly if a CUDA build slipped in
# (it would add several GB to the image).
RUN pip install -r requirements.txt \
    && python -c "import torch; assert torch.version.cuda is None, 'CUDA build of PyTorch'"

# Bake the EasyOCR models (CRAFT detector + English recogniser) into the image so no request
# ever downloads them. The second command proves they load with downloads disabled.
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False)" \
    && python -c "import easyocr; easyocr.Reader(['en'], gpu=False, download_enabled=False)"

COPY . .

EXPOSE 8000
# One Uvicorn worker: the OCR concurrency limit lives in process memory (see Spec).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
