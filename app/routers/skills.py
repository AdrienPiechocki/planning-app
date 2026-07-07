from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/skills", tags=["Compétences & envies d'apprentissage"])


@router.get("/", response_model=list[schemas.SkillGoalOut])
def list_skills(db: Session = Depends(get_db)):
    return db.query(models.SkillGoal).all()


@router.post("/", response_model=schemas.SkillGoalOut, status_code=201)
def create_skill(payload: schemas.SkillGoalCreate, db: Session = Depends(get_db)):
    obj = models.SkillGoal(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/{skill_id}", response_model=schemas.SkillGoalOut)
def update_skill(skill_id: int, payload: schemas.SkillGoalCreate, db: Session = Depends(get_db)):
    obj = db.query(models.SkillGoal).get(skill_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Compétence introuvable")
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{skill_id}", status_code=204)
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.SkillGoal).get(skill_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Compétence introuvable")
    db.delete(obj)
    db.commit()
