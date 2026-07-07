from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/media", tags=["Bibliothèque médias"])


@router.get("/", response_model=list[schemas.MediaItemOut])
def list_media(db: Session = Depends(get_db)):
    return db.query(models.MediaItem).all()


@router.post("/", response_model=schemas.MediaItemOut, status_code=201)
def create_media(payload: schemas.MediaItemCreate, db: Session = Depends(get_db)):
    obj = models.MediaItem(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.patch("/{media_id}", response_model=schemas.MediaItemOut)
def update_media(media_id: int, payload: schemas.MediaItemCreate, db: Session = Depends(get_db)):
    obj = db.query(models.MediaItem).get(media_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Média introuvable")
    for key, value in payload.model_dump().items():
        setattr(obj, key, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{media_id}", status_code=204)
def delete_media(media_id: int, db: Session = Depends(get_db)):
    obj = db.query(models.MediaItem).get(media_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Média introuvable")
    db.delete(obj)
    db.commit()
