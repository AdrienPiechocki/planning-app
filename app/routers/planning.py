from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app import models, schemas
from app.services.llm_service import generate_planning as generate_planning_service, regenerate_llm_blocks
from app.services import nextcloud_service

router = APIRouter(tags=["Planning hebdomadaire"])


# ---------- Saisie du week-end ----------
@router.post("/api/weekly-inputs/", response_model=schemas.WeeklyInputOut, status_code=201)
def create_weekly_input(payload: schemas.WeeklyInputCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"exceptional_constraints"})
    weekly_input = models.WeeklyInput(**data)
    db.add(weekly_input)
    db.flush()  # récupère l'ID avant de créer les impératifs exceptionnels liés

    for exc in payload.exceptional_constraints:
        db.add(models.ExceptionalConstraint(weekly_input_id=weekly_input.id, **exc.model_dump()))

    db.commit()
    db.refresh(weekly_input)
    return weekly_input


@router.get("/api/weekly-inputs/", response_model=list[schemas.WeeklyInputOut])
def list_weekly_inputs(db: Session = Depends(get_db)):
    return db.query(models.WeeklyInput).order_by(models.WeeklyInput.semaine_du.desc()).all()


@router.get("/api/weekly-inputs/{weekly_input_id}", response_model=schemas.WeeklyInputOut)
def get_weekly_input(weekly_input_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.WeeklyInput).get(weekly_input_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Saisie hebdomadaire introuvable")
    return obj


# ---------- Génération (stub, sera branché sur Ollama à l'étape suivante) ----------
@router.post("/api/weekly-inputs/{weekly_input_id}/generate", response_model=schemas.GeneratedPlanningOut, status_code=201)
def generate_planning(weekly_input_id: int, db: Session = Depends(get_db)):
    weekly_input = db.query(models.WeeklyInput).get(weekly_input_id)
    if not weekly_input:
        raise HTTPException(status_code=404, detail="Saisie hebdomadaire introuvable")
    if weekly_input.generated_planning:
        raise HTTPException(status_code=400, detail="Un planning existe déjà pour cette semaine")

    planning = generate_planning_service(db, weekly_input)
    return planning


# ---------- Consultation / édition du planning ----------
@router.get("/api/plannings/{planning_id}", response_model=schemas.GeneratedPlanningOut)
def get_planning(planning_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.GeneratedPlanning).get(planning_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    return obj


@router.post("/api/plannings/{planning_id}/regenerate", response_model=schemas.GeneratedPlanningOut)
def regenerate_planning(planning_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    if planning.statut == models.PlanningStatus.VALIDE:
        raise HTTPException(status_code=400, detail="Planning déjà validé, régénération impossible")

    planning = regenerate_llm_blocks(db, planning)
    return planning


@router.post("/api/plannings/{planning_id}/blocks", response_model=schemas.PlanningBlockOut, status_code=201)
def add_block(planning_id: int, payload: schemas.PlanningBlockCreate, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    if planning.statut == models.PlanningStatus.VALIDE:
        raise HTTPException(status_code=400, detail="Planning déjà validé, modification impossible")

    block = models.PlanningBlock(planning_id=planning_id, **payload.model_dump())
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@router.patch("/api/plannings/{planning_id}/blocks/{block_id}", response_model=schemas.PlanningBlockOut)
def update_block(planning_id: int, block_id: int, payload: schemas.PlanningBlockUpdate, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    if planning.statut == models.PlanningStatus.VALIDE:
        raise HTTPException(status_code=400, detail="Planning déjà validé, modification impossible")

    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if not block:
        raise HTTPException(status_code=404, detail="Bloc introuvable")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(block, key, value)
    db.commit()
    db.refresh(block)
    return block


@router.delete("/api/plannings/{planning_id}/blocks/{block_id}", status_code=204)
def delete_block(planning_id: int, block_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    if planning.statut == models.PlanningStatus.VALIDE:
        raise HTTPException(status_code=400, detail="Planning déjà validé, modification impossible")

    block = db.query(models.PlanningBlock).filter_by(id=block_id, planning_id=planning_id).first()
    if not block:
        raise HTTPException(status_code=404, detail="Bloc introuvable")
    db.delete(block)
    db.commit()


# ---------- Validation (la sync Nextcloud viendra à l'étape suivante) ----------
@router.post("/api/plannings/{planning_id}/validate", response_model=schemas.GeneratedPlanningOut)
def validate_planning(planning_id: int, db: Session = Depends(get_db)):
    planning = db.query(models.GeneratedPlanning).get(planning_id)
    if not planning:
        raise HTTPException(status_code=404, detail="Planning introuvable")
    if planning.statut == models.PlanningStatus.VALIDE:
        raise HTTPException(status_code=400, detail="Planning déjà validé")
    if not planning.blocks:
        raise HTTPException(status_code=400, detail="Impossible de valider un planning vide")

    planning.statut = models.PlanningStatus.VALIDE
    planning.validated_at = datetime.utcnow()
    db.commit()
    db.refresh(planning)

    warnings = nextcloud_service.sync_planning(db, planning)
    db.refresh(planning)
    planning.sync_warnings = warnings or None

    return planning
