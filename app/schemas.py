"""
Schémas Pydantic (validation entrée/sortie API).
Un schéma "Create" pour ce qu'on reçoit, un schéma simple pour ce qu'on renvoie.
"""
from datetime import date, time, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models import MediaType, MediaStatus, PlanningStatus, ActivityCategory


# ---------- RecurringConstraint ----------
class RecurringConstraintCreate(BaseModel):
    nom: str
    description: str = ""
    jour_prefere: Optional[str] = None
    heure_debut: Optional[time] = None
    heure_fin: Optional[time] = None
    actif: bool = True


class RecurringConstraintOut(RecurringConstraintCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------- MediaItem ----------
class MediaItemCreate(BaseModel):
    titre: str
    type: MediaType
    statut: MediaStatus = MediaStatus.A_FAIRE
    duree_session_minutes: int = 90
    sessions_restantes: Optional[int] = None
    notes: str = ""


class MediaItemOut(MediaItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------- SkillGoal ----------
class SkillGoalCreate(BaseModel):
    nom: str
    niveau_actuel: Optional[str] = None
    est_envie_apprentissage: bool = False
    notes: str = ""


class SkillGoalOut(SkillGoalCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---------- ExceptionalConstraint ----------
class ExceptionalConstraintCreate(BaseModel):
    nom: str
    description: str = ""
    jour: date
    heure_debut: Optional[time] = None
    heure_fin: Optional[time] = None


class ExceptionalConstraintOut(ExceptionalConstraintCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    weekly_input_id: int


# ---------- WeeklyInput ----------
class WeeklyInputCreate(BaseModel):
    semaine_du: date
    recurring_constraints_actives: str = ""  # JSON stringifié des IDs sélectionnés
    jeu_mode: str = "priorise"  # "priorise" ou "aleatoire"
    nombre_jeux: int = 1
    loisir_jeu_media_ids: str = "[]"  # JSON stringifié des IDs candidats (mode "aleatoire")
    film_serie_mode: str = "priorise"
    nombre_films_series: int = 1
    loisir_film_serie_media_ids: str = "[]"
    dev_mode: str = "aleatoire"  # "aleatoire" ou "manuel"
    projet_dev_skill_ids: str = "[]"
    notes_jeu: str = ""
    notes_film_serie: str = ""
    notes_projet_dev: str = ""
    exceptional_constraints: list[ExceptionalConstraintCreate] = []


class WeeklyInputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    semaine_du: date
    recurring_constraints_actives: str
    jeu_mode: str
    nombre_jeux: int
    loisir_jeu_media_ids: str
    film_serie_mode: str
    nombre_films_series: int
    loisir_film_serie_media_ids: str
    dev_mode: str
    projet_dev_skill_ids: str
    notes_jeu: str
    notes_film_serie: str
    notes_projet_dev: str
    created_at: datetime
    exceptional_constraints: list[ExceptionalConstraintOut] = []


# ---------- PlanningBlock ----------
class PlanningBlockCreate(BaseModel):
    jour: date
    heure_debut: Optional[time] = None
    heure_fin: Optional[time] = None
    titre: str
    description: str = ""
    categorie: ActivityCategory
    ordre: int = 0


class PlanningBlockUpdate(BaseModel):
    jour: Optional[date] = None
    heure_debut: Optional[time] = None
    heure_fin: Optional[time] = None
    titre: Optional[str] = None
    description: Optional[str] = None
    categorie: Optional[ActivityCategory] = None
    ordre: Optional[int] = None


class PlanningBlockOut(PlanningBlockCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    planning_id: int
    nextcloud_event_uid: Optional[str] = None


# ---------- GeneratedPlanning ----------
class GeneratedPlanningOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    weekly_input_id: int
    statut: PlanningStatus
    created_at: datetime
    validated_at: Optional[datetime] = None
    blocks: list[PlanningBlockOut] = []
    generation_warning: Optional[str] = None
    sync_warnings: Optional[list[str]] = None
