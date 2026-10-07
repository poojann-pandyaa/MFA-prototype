# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 AMFA_MODELS_DIR=/app/models AMFA_DATA_DIR=/var/lib/amfa \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libgomp1 libsndfile1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 amfa \
 && useradd --system --uid 10001 --gid amfa --home-dir /app --shell /usr/sbin/nologin amfa \
 && mkdir -p /var/lib/amfa && chown amfa:amfa /var/lib/amfa
WORKDIR /app
COPY requirements/base.txt requirements/base.txt

FROM base AS llama-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake \
 && rm -rf /var/lib/apt/lists/*
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF"
RUN pip wheel --wheel-dir /wheels llama_cpp_python==0.3.35

FROM base AS core
COPY --from=llama-build /wheels /wheels
COPY requirements/core.txt requirements/core.txt
RUN pip install --find-links /wheels -r requirements/core.txt && rm -rf /wheels
COPY amfa ./amfa
COPY scripts ./scripts
COPY --chown=root:root models/llm/qwen2.5-3b-instruct-q4_k_m.gguf models/llm/
RUN python -c "from pathlib import Path; from amfa.common.artifacts import verify_llm; \
verify_llm(Path('models/llm/qwen2.5-3b-instruct-q4_k_m.gguf'))"
USER amfa
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s CMD \
  python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "amfa.cli"]
CMD ["serve", "core", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

FROM base AS face
COPY requirements/face.txt requirements/face.txt
RUN pip install -r requirements/face.txt
COPY amfa ./amfa
COPY scripts ./scripts
COPY --chown=root:root models/face models/face
USER amfa
RUN python -c "from amfa.common.artifacts import PINS, verified; \
[verified(p.path) for p in PINS if p.path.startswith('face/')]"
EXPOSE 8101
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s CMD \
  python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8101/health',timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "amfa.cli"]
CMD ["serve", "face", "--host", "0.0.0.0", "--port", "8101", "--workers", "2"]

FROM base AS voice
COPY requirements/voice.txt requirements/voice.txt
RUN pip install -r requirements/voice.txt
COPY amfa ./amfa
COPY scripts ./scripts
COPY --chown=root:root models/voice models/voice
USER amfa
RUN python -c "from amfa.common import artifacts as a; a.verified('voice/pre_trained_DF_RawNet2.pth'); \
a.verify_directory(a.ECAPA_DIR, a.ECAPA_PINS); a.verify_directory(a.ASR_DIR, a.ASR_PINS)"
EXPOSE 8102
HEALTHCHECK --interval=15s --timeout=3s --start-period=90s CMD \
  python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8102/health',timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "amfa.cli"]
CMD ["serve", "voice", "--host", "0.0.0.0", "--port", "8102", "--workers", "1"]

FROM base AS ueba
COPY requirements/ueba.txt requirements/ueba.txt
RUN pip install -r requirements/ueba.txt
COPY amfa ./amfa
COPY scripts ./scripts
RUN python -m amfa.factors.ueba.training.train --users 150 --behaviour-users 40
USER amfa
EXPOSE 8103
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s CMD \
  python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8103/health',timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "amfa.cli"]
CMD ["serve", "ueba", "--host", "0.0.0.0", "--port", "8103", "--workers", "2"]

FROM base AS demo-bank
RUN pip install -r requirements/base.txt
COPY sdk ./sdk
COPY examples ./examples
RUN mkdir -p /var/lib/bank && chown amfa:amfa /var/lib/bank
USER amfa
EXPOSE 8050
ENTRYPOINT ["uvicorn", "examples.demo_bank.bank:app", "--host", "0.0.0.0", "--port", "8050", \
            "--proxy-headers", "--forwarded-allow-ips", "172.30.0.2", "--no-server-header"]
