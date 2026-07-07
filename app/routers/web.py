import json
from datetime import date, time as time_cls
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.utils import next_monday, week_dates, WEEKDAY_NAMES_FR
from app.web_templates import templates
from app.services.llm_service import generate_planning as generate_planning_service, regenerate_llm_blocks
from app.services import nextcloud_service

router = APIRouter(include_in_schema=False)


def _empty_str_to_none(value: str | None):
    return value if value else None


def _parse_time_field(value: str | None) -> time_cls | None:
    if not value:
        return None
    h, m = value.split(":")
    return time_cls(hour=int(h), minute=int(m))


# ---------------------------------------------------------------------------
# Accueil
# ---------------------------------------------------------------------------
@router.get("/")
def home():
    return RedirectResponse("/semaine")


# ---------------------------------------------------------------------------
# Saisie hebdomadaire (le formulaire du week-end)
# ---------------------------------------------------------------------------
@router.get("/semaine", response_class=HTMLResponse)
def semaine_form(request: Request, db: Session = Depends(get_db)):
    recurring = db.query(models.RecurringConstraint).filter_by(actif=True).all()
    jeux = (
        db.query(models.MediaItem)
        .filter(
            models.MediaItem.type == models.MediaType.JEU_VIDEO,
            models.MediaItem.statut != models.MediaStatus.TERMINE,
        )
        .order_by(models.MediaItem.titre)
        .all()
    )
    films_series = (
        db.query(models.MediaItem)
        .filter(
            models.MediaItem.type.in_([models.MediaType.FILM, models.MediaType.SERIE]),
            models.MediaItem.statut != models.MediaStatus.TERMINE,
        )
        .order_by(models.MediaItem.titre)
        .all()
    )
    skill_goals = db.query(models.SkillGoal).order_by(models.SkillGoal.nom).all()
    recent_inputs = (
        db.query(models.WeeklyInput).order_by(models.WeeklyInput.semaine_du.desc()).limit(5).all()
    )
    return templates.TemplateResponse(request, "semaine_form.html", {
        "active": "semaine",
        "recurring_constraints": recurring,
        "jeux": jeux,
        "films_series": films_series,
        "skill_goals": skill_goals,
        "default_monday": next_monday().isoformat(),
        "recent_inputs": recent_inputs,
    })


@router.post("/semaine")
async def semaine_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    semaine_du = date.fromisoformat(form["semaine_du"])
    recurring_ids = [int(v) for v in form.getlist("recurring_ids")]
    jeu_ids = [int(v) for v in form.getlist("loisir_jeu_media_ids")]
    film_serie_ids = [int(v) for v in form.getlist("loisir_film_serie_media_ids")]
    projet_dev_ids = [int(v) for v in form.getlist("projet_dev_skill_ids")]

    def _int_field(name: str, default: int) -> int:
        raw = form.get(name)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return default

    weekly_input = models.WeeklyInput(
        semaine_du=semaine_du,
        recurring_constraints_actives=json.dumps(recurring_ids),
        jeu_mode=form.get("jeu_mode") or "priorise",
        nombre_jeux=_int_field("nombre_jeux", 1),
        loisir_jeu_media_ids=json.dumps(jeu_ids),
        film_serie_mode=form.get("film_serie_mode") or "priorise",
        nombre_films_series=_int_field("nombre_films_series", 1),
        loisir_film_serie_media_ids=json.dumps(film_serie_ids),
        dev_mode=form.get("dev_mode") or "aleatoire",
        projet_dev_skill_ids=json.dumps(projet_dev_ids),
        notes_jeu=form.get("notes_jeu", ""),
        notes_film_serie=form.get("notes_film_serie", ""),
        notes_projet_dev=form.get("notes_projet_dev", ""),
    )
    db.add(weekly_input)
    db.flush()

    exc_noms = form.getlist("exc_nom")
    exc_jours = form.getlist("exc_jour")
    exc_debuts = form.getlist("exc_heure_debut")
    exc_fins = form.getlist("exc_heure_fin")
    for nom, jour, hd, hf in zip(exc_noms, exc_jours, exc_debuts, exc_fins):
        if not nom or not jour:
            continue
        db.add(models.ExceptionalConstraint(
            weekly_input_id=weekly_input.id,
            nom=nom,
            jour=date.fromisoformat(jour),
            heure_debut=_parse_time_field(hd),
            heure_fin=_parse_time_field(hf),
        ))

    db.commit()
    db.refresh(weekly_input)

    planning = generate_planning_service(db, weekly_input)

    params = {}
    if getattr(planning, "generation_warning", None):
        params["generation_warning"] = planning.generation_warning
    url = f"/planning/{planning.id}"
    if params:
        url += "?" + urlencode(params)
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------------------
# Grille de planning éditable
# ---------------------------------------------------------------------------
def _grouped_blocks(planning: models.GeneratedPlanning, days: list[date]) -> dict:
    grouped = {d: [] for d in days}
    for block in sorted(planning.blocks, key=lambda b: (b.heure_debut is None, b.heure_debut or time_cls(0, 0))):
        if block.jour in grouped:
            grouped[block.jour].append(block)
    return grouped


@router.get("/planning/{planning_id}", response_class=HTMLResponse)
def planning_view(planning_id: int, request: Request, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        return HTMLResponse("Planning introuvable", status_code=404)

    days = week_dates(planning.weekly_input.semaine_du)
    grouped = _grouped_blocks(planning, days)

    return templates.TemplateResponse(request, "planning_view.html", {
        "active": "semaine",
        "planning": planning,
        "days": list(zip(days, WEEKDAY_NAMES_FR)),
        "grouped": grouped,
        "categories": [
            ("loisir", "Loisir"),
            ("projet_dev", "Projet dev"),
            ("imperatif_recurrent", "Impératif récurrent"),
            ("imperatif_exceptionnel", "Impératif exceptionnel"),
        ],
        "generation_warning": request.query_params.get("generation_warning"),
        "sync_warnings": request.query_params.getlist("w"),
        "validated_ok": request.query_params.get("validated") == "1",
    })


@router.post("/planning/{planning_id}/regenerate")
def regenerate_web(planning_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning or planning.statut == models.PlanningStatus.VALIDE:
        return RedirectResponse(f"/planning/{planning_id}", status_code=303)

    planning = regenerate_llm_blocks(db, planning)

    params = {}
    if getattr(planning, "generation_warning", None):
        params["generation_warning"] = planning.generation_warning
    url = f"/planning/{planning_id}"
    if params:
        url += "?" + urlencode(params)
    return RedirectResponse(url, status_code=303)


@router.post("/planning/{planning_id}/blocks", response_class=HTMLResponse)
async def add_block_web(planning_id: int, request: Request, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning or planning.statut == models.PlanningStatus.VALIDE:
        return HTMLResponse("", status_code=400)

    form = await request.form()
    block = models.PlanningBlock(
        planning_id=planning_id,
        jour=date.fromisoformat(form["jour"]),
        heure_debut=_parse_time_field(form.get("heure_debut")),
        heure_fin=_parse_time_field(form.get("heure_fin")),
        titre=form["titre"],
        description=form.get("description", ""),
        categorie=models.ActivityCategory(form["categorie"]),
    )
    db.add(block)
    db.commit()
    db.refresh(block)

    return templates.TemplateResponse(request, "partials/block_card.html", {"block": block, "planning": planning})


@router.get("/planning/{planning_id}/blocks/{block_id}/edit", response_class=HTMLResponse)
def edit_block_form(planning_id: int, block_id: int, request: Request, db: Session = Depends(get_db)):
    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if not block:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/block_edit_form.html", {"block": block})


@router.get("/planning/{planning_id}/blocks/{block_id}/view", response_class=HTMLResponse)
def view_block_card(planning_id: int, block_id: int, request: Request, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if not block or not planning:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/block_card.html", {"block": block, "planning": planning})


@router.patch("/planning/{planning_id}/blocks/{block_id}", response_class=HTMLResponse)
async def update_block_web(planning_id: int, block_id: int, request: Request, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if not block or not planning or planning.statut == models.PlanningStatus.VALIDE:
        return HTMLResponse("", status_code=400)

    form = await request.form()
    block.jour = date.fromisoformat(form["jour"])
    block.heure_debut = _parse_time_field(form.get("heure_debut"))
    block.heure_fin = _parse_time_field(form.get("heure_fin"))
    block.titre = form["titre"]
    block.description = form.get("description", "")
    block.categorie = models.ActivityCategory(form["categorie"])
    db.commit()
    db.refresh(block)

    return templates.TemplateResponse(request, "partials/block_card.html", {"block": block, "planning": planning})


@router.delete("/planning/{planning_id}/blocks/{block_id}", response_class=HTMLResponse)
def delete_block_web(planning_id: int, block_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if block and planning and planning.statut != models.PlanningStatus.VALIDE:
        db.delete(block)
        db.commit()
    return HTMLResponse("")


@router.post("/planning/{planning_id}/validate")
def validate_web(planning_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning or planning.statut == models.PlanningStatus.VALIDE or not planning.blocks:
        return RedirectResponse(f"/planning/{planning_id}", status_code=303)

    from datetime import datetime
    planning.statut = models.PlanningStatus.VALIDE
    planning.validated_at = datetime.utcnow()
    db.commit()
    db.refresh(planning)

    warnings = nextcloud_service.sync_planning(db, planning)

    params = [("validated", "1")] + [("w", w) for w in warnings]
    return RedirectResponse(f"/planning/{planning_id}?" + urlencode(params), status_code=303)


@router.post("/planning/{planning_id}/delete")
def planning_delete(planning_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if planning:
        weekly_input = db.query(models.WeeklyInput).get(planning.weekly_input_id)
        if weekly_input:
            # Supprime toute la semaine (cascade delete-orphan sur le planning,
            # ses blocs et les contraintes exceptionnelles) pour ne rien laisser
            # d'orphelin en base.
            db.delete(weekly_input)
        else:
            db.delete(planning)
        db.commit()
    return RedirectResponse("/semaine", status_code=303)


# ---------------------------------------------------------------------------
# Bibliothèque média
# ---------------------------------------------------------------------------
@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request, db: Session = Depends(get_db)):
    items = db.query(models.MediaItem).order_by(models.MediaItem.created_at.desc()).all()
    return templates.TemplateResponse(request, "media.html", {"active": "media", "items": items})


@router.post("/media")
async def media_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    raw_duree = form.get("duree_session_minutes")
    raw_sessions_restantes = form.get("sessions_restantes")
    db.add(models.MediaItem(
        titre=form["titre"],
        type=models.MediaType(form["type"]),
        statut=models.MediaStatus(form.get("statut", "a_faire")),
        duree_session_minutes=int(raw_duree) if raw_duree else 90,
        sessions_restantes=int(raw_sessions_restantes) if raw_sessions_restantes else None,
    ))
    db.commit()
    return RedirectResponse("/media", status_code=303)


@router.get("/media/{media_id}/edit", response_class=HTMLResponse)
def media_edit_form(media_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.MediaItem).get(media_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/media_row_edit.html", {"item": item})


@router.get("/media/{media_id}/view", response_class=HTMLResponse)
def media_view_row(media_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.MediaItem).get(media_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/media_row_view.html", {"item": item})


@router.patch("/media/{media_id}", response_class=HTMLResponse)
async def media_update(media_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.MediaItem).get(media_id)
    if not item:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    raw_duree = form.get("duree_session_minutes")
    raw_sessions_restantes = form.get("sessions_restantes")
    item.titre = form["titre"]
    item.type = models.MediaType(form["type"])
    item.statut = models.MediaStatus(form.get("statut", "a_faire"))
    item.duree_session_minutes = int(raw_duree) if raw_duree else 90
    item.sessions_restantes = int(raw_sessions_restantes) if raw_sessions_restantes else None
    db.commit()
    db.refresh(item)
    return templates.TemplateResponse(request, "partials/media_row_view.html", {"item": item})


@router.post("/media/{media_id}/delete")
def media_delete(media_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.MediaItem).get(media_id)
    if obj:
        db.delete(obj)
        db.commit()
    return RedirectResponse("/media", status_code=303)


# ---------------------------------------------------------------------------
# Compétences & envies d'apprentissage
# ---------------------------------------------------------------------------
@router.get("/skills", response_class=HTMLResponse)
def skills_page(request: Request, db: Session = Depends(get_db)):
    items = db.query(models.SkillGoal).all()
    return templates.TemplateResponse(request, "skills.html", {"active": "skills", "items": items})


@router.post("/skills")
async def skills_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    db.add(models.SkillGoal(
        nom=form["nom"],
        niveau_actuel=_empty_str_to_none(form.get("niveau_actuel")),
        est_envie_apprentissage=form.get("est_envie_apprentissage") == "on",
    ))
    db.commit()
    return RedirectResponse("/skills", status_code=303)


@router.get("/skills/{skill_id}/edit", response_class=HTMLResponse)
def skills_edit_form(skill_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.SkillGoal).get(skill_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/skill_row_edit.html", {"item": item})


@router.get("/skills/{skill_id}/view", response_class=HTMLResponse)
def skills_view_row(skill_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.SkillGoal).get(skill_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/skill_row_view.html", {"item": item})


@router.patch("/skills/{skill_id}", response_class=HTMLResponse)
async def skills_update(skill_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.SkillGoal).get(skill_id)
    if not item:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    item.nom = form["nom"]
    item.niveau_actuel = _empty_str_to_none(form.get("niveau_actuel"))
    item.est_envie_apprentissage = form.get("est_envie_apprentissage") == "on"
    db.commit()
    db.refresh(item)
    return templates.TemplateResponse(request, "partials/skill_row_view.html", {"item": item})


@router.post("/skills/{skill_id}/delete")
def skills_delete(skill_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.SkillGoal).get(skill_id)
    if obj:
        db.delete(obj)
        db.commit()
    return RedirectResponse("/skills", status_code=303)


# ---------------------------------------------------------------------------
# Impératifs récurrents
# ---------------------------------------------------------------------------
@router.get("/constraints", response_class=HTMLResponse)
def constraints_page(request: Request, db: Session = Depends(get_db)):
    items = db.query(models.RecurringConstraint).all()
    return templates.TemplateResponse(request, "constraints.html", {"active": "constraints", "items": items})


@router.post("/constraints")
async def constraints_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    db.add(models.RecurringConstraint(
        nom=form["nom"],
        jour_prefere=_empty_str_to_none(form.get("jour_prefere")),
        heure_debut=_parse_time_field(form.get("heure_debut")),
        heure_fin=_parse_time_field(form.get("heure_fin")),
    ))
    db.commit()
    return RedirectResponse("/constraints", status_code=303)


@router.get("/constraints/{constraint_id}/edit", response_class=HTMLResponse)
def constraints_edit_form(constraint_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.RecurringConstraint).get(constraint_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/constraint_row_edit.html", {"item": item})


@router.get("/constraints/{constraint_id}/view", response_class=HTMLResponse)
def constraints_view_row(constraint_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.RecurringConstraint).get(constraint_id)
    if not item:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/constraint_row_view.html", {"item": item})


@router.patch("/constraints/{constraint_id}", response_class=HTMLResponse)
async def constraints_update(constraint_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.query(models.RecurringConstraint).get(constraint_id)
    if not item:
        return HTMLResponse("", status_code=404)
    form = await request.form()
    item.nom = form["nom"]
    item.jour_prefere = _empty_str_to_none(form.get("jour_prefere"))
    item.heure_debut = _parse_time_field(form.get("heure_debut"))
    item.heure_fin = _parse_time_field(form.get("heure_fin"))
    db.commit()
    db.refresh(item)
    return templates.TemplateResponse(request, "partials/constraint_row_view.html", {"item": item})


@router.post("/constraints/{constraint_id}/delete")
def constraints_delete(constraint_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.RecurringConstraint).get(constraint_id)
    if obj:
        db.delete(obj)
        db.commit()
    return RedirectResponse("/constraints", status_code=303)
