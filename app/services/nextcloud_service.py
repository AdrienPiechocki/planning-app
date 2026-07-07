"""
Service de synchronisation Nextcloud : Calendar (via CalDAV).

Principe :
- Un seul client CalDAV est résolu/créé une fois par synchronisation, puis
  réutilisé pour tous les blocs du planning.
- Chaque bloc est synchronisé indépendamment : un échec sur un bloc n'empêche
  pas la synchronisation des autres. Tout est tracé dans SyncLog.
- Si Nextcloud n'est pas configuré (.env incomplet), la sync est ignorée
  proprement avec un message clair, plutôt que de faire planter la validation.
"""
import logging
import uuid
from datetime import datetime, timedelta

import caldav
from icalendar import Calendar as ICalCalendar, Event as ICalEvent
from sqlalchemy.orm import Session

from app import models
from app.config import settings

logger = logging.getLogger(__name__)


class NextcloudNotConfigured(Exception):
    """Levée quand les identifiants Nextcloud ne sont pas renseignés dans .env."""


def _check_configured():
    if not (settings.nextcloud_url and settings.nextcloud_username and settings.nextcloud_app_password):
        raise NextcloudNotConfigured(
            "Nextcloud non configuré : renseigne NEXTCLOUD_URL, NEXTCLOUD_USERNAME et "
            "NEXTCLOUD_APP_PASSWORD dans le fichier .env."
        )


# ---------------------------------------------------------------------------
# Calendar (CalDAV)
# ---------------------------------------------------------------------------
def get_caldav_client() -> caldav.DAVClient:
    _check_configured()
    return caldav.DAVClient(
        url=f"{settings.nextcloud_url.rstrip('/')}/remote.php/dav",
        username=settings.nextcloud_username,
        password=settings.nextcloud_app_password,
    )


def get_or_create_calendar(client: caldav.DAVClient):
    principal = client.principal()
    for cal in principal.calendars():
        display_name = getattr(cal, "name", None) or cal.get_display_name()
        if display_name == settings.nextcloud_calendar_name:
            return cal
    logger.info("Agenda '%s' introuvable sur Nextcloud, création...", settings.nextcloud_calendar_name)
    return principal.make_calendar(name=settings.nextcloud_calendar_name)


def build_ical_event(block: models.PlanningBlock) -> tuple[str, str]:
    """Retourne (uid, contenu_ics) pour un bloc de planning."""
    uid = str(uuid.uuid4())
    cal = ICalCalendar()
    cal.add("prodid", "-//PlanningApp//FR")
    cal.add("version", "2.0")

    event = ICalEvent()
    event.add("uid", uid)
    event.add("summary", block.titre)
    if block.description:
        event.add("description", block.description)

    if block.heure_debut and block.heure_fin:
        event.add("dtstart", datetime.combine(block.jour, block.heure_debut))
        event.add("dtend", datetime.combine(block.jour, block.heure_fin))
    else:
        # Pas d'horaire précisé -> événement "toute la journée"
        event.add("dtstart", block.jour)
        event.add("dtend", block.jour + timedelta(days=1))

    cal.add_component(event)
    return uid, cal.to_ical().decode("utf-8")


def sync_block_to_calendar(calendar, block: models.PlanningBlock) -> str:
    uid, ics = build_ical_event(block)
    calendar.save_event(ics)
    return uid


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def sync_planning(db: Session, planning: models.GeneratedPlanning) -> list[str]:
    """
    Synchronise tous les blocs du planning vers Calendar.
    Retourne la liste des avertissements rencontrés (vide si tout s'est bien passé).
    Chaque bloc est indépendant : un échec n'empêche pas les autres.
    """
    warnings: list[str] = []

    try:
        _check_configured()
    except NextcloudNotConfigured as exc:
        return [str(exc)]

    calendar = None
    try:
        client = get_caldav_client()
        calendar = get_or_create_calendar(client)
    except Exception as exc:
        logger.error("Connexion Calendar Nextcloud échouée: %s", exc)
        warnings.append(f"Calendar Nextcloud injoignable, événements non créés : {exc}")

    for block in planning.blocks:
        if calendar is not None:
            try:
                uid = sync_block_to_calendar(calendar, block)
                block.nextcloud_event_uid = uid
                db.add(models.SyncLog(
                    planning_id=planning.id, cible="calendar", succes=True,
                    message=f"Événement créé pour '{block.titre}'",
                ))
            except Exception as exc:
                logger.error("Sync calendar échouée pour bloc %s: %s", block.id, exc)
                db.add(models.SyncLog(
                    planning_id=planning.id, cible="calendar", succes=False,
                    message=f"Échec pour '{block.titre}': {exc}",
                ))

    db.commit()
    return warnings
