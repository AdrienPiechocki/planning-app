"""
Modèles de données.

Vue d'ensemble :
- RecurringConstraint   : impératifs présents toutes les semaines (ex: recherche d'emploi)
- ExceptionalConstraint : impératifs ponctuels, liés à une semaine précise (ex: entretien)
- MediaItem             : jeux/films/séries possédés, avec statut de progression
- SkillGoal             : compétences connues + envies d'apprentissage (pour les projets dev)
- WeeklyInput           : la saisie faite chaque week-end pour préparer la semaine
- GeneratedPlanning     : le planning généré par l'IA (brouillon -> validé)
- PlanningBlock         : chaque créneau/activité individuel du planning
- SyncLog               : historique des synchronisations vers Nextcloud
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Date, Time,
    ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import relationship

from app.database import Base


class MediaType(str, enum.Enum):
    JEU_VIDEO = "jeu_video"
    FILM = "film"
    SERIE = "serie"


class MediaStatus(str, enum.Enum):
    A_FAIRE = "a_faire"
    EN_COURS = "en_cours"
    TERMINE = "termine"


class PlanningStatus(str, enum.Enum):
    BROUILLON = "brouillon"
    VALIDE = "valide"


class ActivityCategory(str, enum.Enum):
    IMPERATIF_RECURRENT = "imperatif_recurrent"
    IMPERATIF_EXCEPTIONNEL = "imperatif_exceptionnel"
    LOISIR = "loisir"
    PROJET_DEV = "projet_dev"


class RecurringConstraint(Base):
    __tablename__ = "recurring_constraints"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(200), nullable=False)
    description = Column(Text, default="")
    jour_prefere = Column(String(20), nullable=True)  # ex: "lundi", None si flexible
    heure_debut = Column(Time, nullable=True)
    heure_fin = Column(Time, nullable=True)
    actif = Column(Boolean, default=True)  # permet de suspendre sans supprimer
    created_at = Column(DateTime, default=datetime.utcnow)


class ExceptionalConstraint(Base):
    __tablename__ = "exceptional_constraints"

    id = Column(Integer, primary_key=True, index=True)
    weekly_input_id = Column(Integer, ForeignKey("weekly_inputs.id"), nullable=False)
    nom = Column(String(200), nullable=False)
    description = Column(Text, default="")
    jour = Column(Date, nullable=False)
    heure_debut = Column(Time, nullable=True)
    heure_fin = Column(Time, nullable=True)

    weekly_input = relationship("WeeklyInput", back_populates="exceptional_constraints")


class MediaItem(Base):
    __tablename__ = "media_items"

    id = Column(Integer, primary_key=True, index=True)
    titre = Column(String(300), nullable=False)
    type = Column(SAEnum(MediaType), nullable=False)
    statut = Column(SAEnum(MediaStatus), default=MediaStatus.A_FAIRE)
    duree_session_minutes = Column(Integer, default=90)  # durée cible d'une session de cette activité
    sessions_restantes = Column(Integer, nullable=True)  # optionnel : plafonne le nb de séances/semaine (ex: épisodes restants)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SkillGoal(Base):
    __tablename__ = "skill_goals"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(150), nullable=False)  # ex: "Rust", "TUI", "API REST"
    niveau_actuel = Column(String(50), nullable=True)  # débutant/intermédiaire/avancé
    est_envie_apprentissage = Column(Boolean, default=False)
    notes = Column(Text, default="")


class WeeklyInput(Base):
    __tablename__ = "weekly_inputs"

    id = Column(Integer, primary_key=True, index=True)
    semaine_du = Column(Date, nullable=False)  # lundi de la semaine concernée
    recurring_constraints_actives = Column(Text, default="")  # liste d'IDs JSON
    # Algorithme de sélection (jeu/film-série) : "priorise" = dans toute la
    # bibliothèque, en_cours prioritaire sur à_faire (repli aléatoire en cas
    # d'égalité) ; "aleatoire" = tirage sans priorité, uniquement parmi les IDs
    # cochés par l'utilisateur (loisir_..._media_ids).
    jeu_mode = Column(String(20), default="priorise")
    nombre_jeux = Column(Integer, default=1)
    loisir_jeu_media_ids = Column(Text, default="[]")  # candidats pour le mode "aleatoire"

    film_serie_mode = Column(String(20), default="priorise")
    nombre_films_series = Column(Integer, default=1)
    loisir_film_serie_media_ids = Column(Text, default="[]")  # candidats pour le mode "aleatoire"

    # Algorithme de sélection (projet dev) : "aleatoire" = choix unique au
    # hasard parmi les compétences (priorité aux "envies d'apprentissage") ;
    # "manuel" = utilise directement la sélection de l'utilisateur (1 ou
    # plusieurs sujets combinés en une seule idée de projet par l'IA).
    dev_mode = Column(String(20), default="aleatoire")
    projet_dev_skill_ids = Column(Text, default="[]")  # sélection directe pour le mode "manuel"

    notes_jeu = Column(Text, default="")
    notes_film_serie = Column(Text, default="")
    notes_projet_dev = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    exceptional_constraints = relationship(
        "ExceptionalConstraint", back_populates="weekly_input", cascade="all, delete-orphan"
    )
    generated_planning = relationship(
        "GeneratedPlanning", back_populates="weekly_input", uselist=False, cascade="all, delete-orphan"
    )


class GeneratedPlanning(Base):
    __tablename__ = "generated_plannings"

    id = Column(Integer, primary_key=True, index=True)
    weekly_input_id = Column(Integer, ForeignKey("weekly_inputs.id"), nullable=False)
    statut = Column(SAEnum(PlanningStatus), default=PlanningStatus.BROUILLON)
    raw_llm_response = Column(Text, nullable=True)  # réponse brute d'Ollama, pour debug/regénération
    created_at = Column(DateTime, default=datetime.utcnow)
    validated_at = Column(DateTime, nullable=True)

    weekly_input = relationship("WeeklyInput", back_populates="generated_planning")
    blocks = relationship("PlanningBlock", back_populates="planning", cascade="all, delete-orphan")


class PlanningBlock(Base):
    __tablename__ = "planning_blocks"

    id = Column(Integer, primary_key=True, index=True)
    planning_id = Column(Integer, ForeignKey("generated_plannings.id"), nullable=False)
    jour = Column(Date, nullable=False)
    heure_debut = Column(Time, nullable=True)
    heure_fin = Column(Time, nullable=True)
    titre = Column(String(300), nullable=False)
    description = Column(Text, default="")
    categorie = Column(SAEnum(ActivityCategory), nullable=False)
    ordre = Column(Integer, default=0)  # pour l'affichage/tri dans la journée

    # Référence de sync Nextcloud, remplie après validation
    nextcloud_event_uid = Column(String(300), nullable=True)

    planning = relationship("GeneratedPlanning", back_populates="blocks")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    planning_id = Column(Integer, ForeignKey("generated_plannings.id"), nullable=False)
    cible = Column(String(50), nullable=False)  # "calendar"
    succes = Column(Boolean, default=False)
    message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
