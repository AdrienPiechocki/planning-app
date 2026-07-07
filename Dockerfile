# Image légère, suffisante pour une app FastAPI + SQLite (pas de compilation lourde).
FROM python:3.12-slim

WORKDIR /app

# Dépendances d'abord (mise en cache Docker : ne se réinstalle que si requirements.txt change).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Code de l'application.
COPY app ./app

# Répertoire pour la base SQLite persistée via un volume Docker (voir docker-compose.yml).
# Créé ici pour être sûr qu'il existe même si le volume est vide au premier lancement.
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Pas de --reload en prod : un seul process uvicorn, comme ./start.sh sans --dev.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
