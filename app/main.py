"""
Point d'entrée de l'application.
Lancement en dev : uvicorn app.main:app --reload
"""
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.routers import constraints, media, skills, planning, web
from app.services import nextcloud_service

# Crée les tables si elles n'existent pas encore (suffisant pour le MVP ;
# on passera à Alembic si le schéma devient plus complexe).
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Planning App",
    description="Génération assistée par IA locale d'un planning hebdomadaire, synchronisé avec Nextcloud.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(constraints.router)
app.include_router(media.router)
app.include_router(skills.router)
app.include_router(planning.router)
app.include_router(web.router)


@app.get("/api/health", tags=["Santé"])
def health_check():
    return {"status": "ok"}


@app.get("/api/ollama/health", tags=["Santé"])
def ollama_health_check():
    """Vérifie qu'Ollama est joignable et liste les modèles installés."""
    url = f"{settings.ollama_base_url}/api/tags"
    try:
        response = httpx.get(url, timeout=5)
        response.raise_for_status()
        models_installed = [m["name"] for m in response.json().get("models", [])]
        target_model_present = settings.ollama_model in models_installed or any(
            m.startswith(settings.ollama_model) for m in models_installed
        )
        return {
            "ollama_joignable": True,
            "modeles_installes": models_installed,
            "modele_configure": settings.ollama_model,
            "modele_configure_present": target_model_present,
        }
    except httpx.HTTPError as exc:
        return {
            "ollama_joignable": False,
            "erreur": str(exc),
            "url_testee": url,
        }


@app.get("/api/nextcloud/health", tags=["Santé"])
def nextcloud_health_check():
    """Vérifie la connectivité Calendar (CalDAV)."""
    result = {"configure": bool(settings.nextcloud_url and settings.nextcloud_username and settings.nextcloud_app_password)}
    if not result["configure"]:
        result["erreur"] = "NEXTCLOUD_URL / NEXTCLOUD_USERNAME / NEXTCLOUD_APP_PASSWORD manquants dans .env"
        return result

    try:
        client = nextcloud_service.get_caldav_client()
        calendar = nextcloud_service.get_or_create_calendar(client)
        result["calendar_joignable"] = True
        result["calendar_nom"] = settings.nextcloud_calendar_name
    except Exception as exc:
        result["calendar_joignable"] = False
        result["calendar_erreur"] = str(exc)

    return result
