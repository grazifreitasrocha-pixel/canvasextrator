FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# poppler-utils fornece o comando `pdftotext`, usado pelo parser_pgdas.py
# para extrair o RBT12 do PDF do PGDAS-D
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

WORKDIR /app/app

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
