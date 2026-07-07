#!/usr/bin/env bash
#
# Script de lancement de Planning App.
#
# Usage :
#   ./start.sh                # lancement normal (production, un seul process)
#   ./start.sh --dev          # mode développement (rechargement auto)
#   ./start.sh --port 8080    # changer le port (défaut : 8000)
#   ./start.sh --host 0.0.0.0 # changer l'host (défaut : 127.0.0.1)
#
set -euo pipefail

# Se placer dans le dossier du script, quel que soit l'endroit d'où il est appelé
cd "$(dirname "${BASH_SOURCE[0]}")"

usage() {
  cat <<'EOF'
Usage :
  ./start.sh                # lancement normal (production, un seul process)
  ./start.sh --dev          # mode développement (rechargement auto)
  ./start.sh --port 8080    # changer le port (défaut : 8000)
  ./start.sh --host 0.0.0.0 # changer l'host (défaut : 127.0.0.1)
EOF
}

# ---------- Valeurs par défaut ----------
HOST="127.0.0.1"
PORT="8000"
DEV_MODE=false

# ---------- Lecture des arguments ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      DEV_MODE=true
      shift
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Option inconnue : $1"
      echo "Utilise --help pour voir les options disponibles."
      exit 1
      ;;
  esac
done

echo "==> Planning App"

# ---------- Environnement virtuel ----------
if [ ! -d ".venv" ]; then
  echo "==> Aucun environnement virtuel trouvé, création de .venv..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# ---------- Dépendances ----------
# On ne réinstalle que si requirements.txt a changé depuis la dernière install,
# pour ne pas ralentir chaque démarrage inutilement.
REQ_HASH_FILE=".venv/.requirements.sha256"
CURRENT_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
PREVIOUS_HASH="$(cat "$REQ_HASH_FILE" 2>/dev/null || echo '')"

if [ "$CURRENT_HASH" != "$PREVIOUS_HASH" ]; then
  echo "==> Installation/mise à jour des dépendances..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
else
  echo "==> Dépendances déjà à jour."
fi

# ---------- Fichier .env ----------
if [ ! -f ".env" ]; then
  echo "==> Aucun fichier .env trouvé, copie de .env.example."
  cp .env.example .env
  echo "    Pense à éditer .env avec tes identifiants Nextcloud et ta config Ollama."
fi

# ---------- Vérification Ollama (informatif, non bloquant) ----------
OLLAMA_URL="$(grep -E '^OLLAMA_BASE_URL=' .env | cut -d '=' -f2- || true)"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if command -v curl >/dev/null 2>&1; then
  if curl -s --max-time 2 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    echo "==> Ollama détecté sur ${OLLAMA_URL}."
  else
    echo "==> ⚠ Ollama injoignable sur ${OLLAMA_URL} (la génération IA échouera tant qu'il n'est pas lancé)."
  fi
fi

# ---------- Lancement ----------
echo "==> Démarrage sur http://${HOST}:${PORT}"

if [ "$DEV_MODE" = true ]; then
  echo "==> Mode développement (rechargement automatique activé)."
  exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
else
  exec uvicorn app.main:app --host "$HOST" --port "$PORT"
fi
