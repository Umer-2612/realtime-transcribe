FROM python:3.12-slim

ARG MODEL_ID=iic/SenseVoiceSmall

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765 \
    MODEL_ID=$MODEL_ID \
    MODELSCOPE_CACHE=/home/user/.cache/modelscope \
    TORCH_HOME=/home/user/.cache/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        git \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
RUN python -c "import os; from funasr import AutoModel; model_id = os.environ['MODEL_ID']; AutoModel(model=model_id, disable_update=True, disable_pbar=True)"

COPY --chown=user . .

EXPOSE 8765

CMD ["python", "main.py"]
