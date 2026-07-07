from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/recurring-constraints", tags=["Impératifs récurrents"])


@router.get("/", response_model=list[schemas.RecurringConstraintOut])
def list_recurring_constraints(db: Session = Depends(get_db)):
    return db.query(models.RecurringConstraint).all()


@router.post("/", response_model=schemas.RecurringConstraintOut, status_code=201)
def create_recurring_constraint(payload: schemas.RecurringConstraintCreate, db: Session = Depends(get_db)):
    obj = models.RecurringConstraint(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/{constraint_id}", response_model=schemas.RecurringConstraintOut)
def update_recurring_constraint(constraint_id: int, payload: schemas.RecurringConstraintCreate, db: Session = Depends(get_db)):
    obj = db.query(models.RecurringConstraint).get(constraint_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Impératif récurrent introuvable")
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{constraint_id}", status_code=204)
def delete_recurring_constraint(constraint_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.RecurringConstraint).get(constraint_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Impératif récurrent introuvable")
    db.delete(obj)
    db.commit()
