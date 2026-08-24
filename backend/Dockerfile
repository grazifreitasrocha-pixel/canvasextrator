FROM python:3.11-slim

# Garante que os prints apareçam imediatamente no log do Railway,
# sem ficar "segurados" em buffer até acumular um bloco de saída
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

WORKDIR /app/app

# O Railway injeta a porta certa na variável $PORT automaticamente
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
