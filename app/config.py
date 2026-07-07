"""
Configuration centralisée de l'application.
Toutes les valeurs sensibles/variables (URLs, identifiants) sont lues
depuis des variables d'environnement (.env) et jamais codées en dur.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Base de données
    database_url: str = "sqlite:///./planning.db"

    # Nextcloud (sync Calendar)
    nextcloud_url: str = Field(default="", description="URL de base de ton Nextcloud, ex: https://cloud.example.com")
    nextcloud_username: str = Field(default="", description="Nom d'utilisateur Nextcloud")
    nextcloud_app_password: str = Field(default="", description="Mot de passe d'application Nextcloud (pas ton mdp principal)")
    nextcloud_calendar_name: str = Field(default="planning", description="Nom de l'agenda cible sur Nextcloud")

    # Ollama (utilisé plus tard pour la génération)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3n"

    # Plage horaire à remplir et pauses fixes à respecter (recherche/dev/loisir)
    day_start_time: str = "10:00"
    day_end_time: str = "22:30"
    pause_times: str = "12:30,16:00,20:00"  # heures de pause (repas, coupures)
    pause_duration_minutes: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
