FROM python:3.10-slim

RUN apt-get update && \
    apt-get install --yes --no-install-recommends gcc g++ libffi-dev git && \
    rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

ENV PYTHONIOENCODING=utf-8
ENV MKL_NUM_THREADS=4

WORKDIR /app

RUN adduser --disabled-password --gecos "app" app && chown -R app:app /app
USER app
ENV PATH="/home/app/.local/bin:${PATH}"

RUN pip install --user "pip<24.1" && \
    pip install --user setuptools wheel cython "numpy<1.24.0"

COPY services/translation-worker/requirements.txt .
RUN pip install --user -r requirements.txt --no-cache-dir && \
    rm requirements.txt && \
    python -c "import nltk; nltk.download('punkt_tab', quiet=True)"

COPY services/translation-worker/ ./

ENTRYPOINT ["python", "main.py"]
