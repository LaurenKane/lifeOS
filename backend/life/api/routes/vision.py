"""vision router — the Vision board: images, phrases, and the media files.

Media lives on disk ("links to local media, not copies in the database" — a
discovery commitment), under the configured `MEDIA_DIR`, uploaded through
this router and served back through it. Filenames are generated server-side
(uuid + validated extension), so a client-supplied name can never reach the
filesystem; the serve route accepts only the pattern that generation
produces — path traversal has no spelling that matches it.
"""

from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Annotated

from config import get_settings
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import VisionPhraseRequest, VisionUpdateRequest
from life.domain.models.vision import VisionItem
from life.public import Area, VisionItemSummary, VisionKind

router = APIRouter(tags=["life"], prefix="/life/vision")

SessionDep = Annotated[Session, Depends(get_session)]

_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_MEDIA_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-f0-9-]{36}\.[a-z0-9]{3,4}$")

_MAX_MEDIA_BYTES: int = 5 * 1024 * 1024


def _summary(item: VisionItem) -> VisionItemSummary:
    return VisionItemSummary(
        id=item.id,
        kind=VisionKind(item.kind),
        text=item.text,
        media_path=item.media_path,
        area=Area(item.area) if item.area else None,
        is_active=item.is_active,
    )


@router.get("/items", response_model=list[VisionItemSummary])
def list_items(session: SessionDep) -> list[VisionItemSummary]:
    """Every active Vision-board item, oldest first."""
    rows = (
        session.execute(
            select(VisionItem)
            .where(VisionItem.is_active)
            .order_by(VisionItem.created_at)
        )
        .scalars()
        .all()
    )
    return [_summary(i) for i in rows]


@router.post(
    "/items",
    response_model=VisionItemSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def add_phrase(body: VisionPhraseRequest, session: SessionDep) -> VisionItemSummary:
    """A phrase ("Build a life I'm excited to wake up to."), optionally
    tagged to an area."""
    with session.begin():
        item = VisionItem(kind="phrase", text=body.text, area=body.area)
        session.add(item)
        session.flush()
        return _summary(item)


@router.post(
    "/items/upload",
    response_model=VisionItemSummary,
    status_code=http_status.HTTP_201_CREATED,
)
async def upload(
    session: SessionDep,
    file: UploadFile,
    area: Area | None = None,
) -> VisionItemSummary:
    """One image. Stored on disk under the media dir; the row carries only
    the generated file name (the database is out of the byte business).

    The 8-area check is the DB's job too, but the query is checked here the
    same way accounts.py restates its CHECK: the useful refusal is at the
    edge with a message, not an IntegrityError from inside the COMMIT."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"only {', '.join(sorted(_ALLOWED_SUFFIXES))} are accepted",
        )

    media_dir = Path(get_settings().MEDIA_DIR)
    media_dir.mkdir(parents=True, exist_ok=True)

    name = f"{uuid.uuid4()}{suffix}"
    target = media_dir / name
    payload = await file.read()
    if len(payload) > _MAX_MEDIA_BYTES:
        raise HTTPException(
            http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "images are limited to 5 MB",
        )
    target.write_bytes(payload)

    with session.begin():
        item = VisionItem(kind="image", media_path=name, area=area)
        session.add(item)
        session.flush()
        return _summary(item)


@router.get("/media/{filename}")
def media(filename: str) -> FileResponse:
    """Serve one stored image file. Filenames follow the upload generator's
    exact shape; anything else is a 404 before the filesystem is asked."""
    if not _MEDIA_NAME_PATTERN.match(filename):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND)
    path = Path(get_settings().MEDIA_DIR) / filename
    if not path.is_file():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND)
    guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=guessed)


@router.patch("/items/{item_id}", response_model=VisionItemSummary)
def update_item(
    item_id: int, body: VisionUpdateRequest, session: SessionDep
) -> VisionItemSummary:
    """Retitle, re-tag or drop a Vision item. `is_active=False` rests it:
    the item is kept, the board just stops showing it."""
    with session.begin():
        item = session.get(VisionItem, item_id)
        if item is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such vision item")
        if body.text is not None:
            if item.kind != "phrase":
                raise HTTPException(
                    http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "only a phrase can be retitled",
                )
            item.text = body.text
        if body.area is not None:
            item.area = str(body.area)
        if body.is_active is not None:
            item.is_active = body.is_active
        session.flush()
        return _summary(item)


@router.delete("/items/{item_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, session: SessionDep) -> None:
    """Delete one Vision item. The referenced file, if any, is unlinked too —
    an orphaned 5 MB image on a small VPS is a real cost."""
    with session.begin():
        item = session.get(VisionItem, item_id)
        if item is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such vision item")
        media_name = item.media_path
        session.delete(item)
        session.flush()
    if media_name is not None:
        stored = Path(get_settings().MEDIA_DIR) / media_name
        if stored.is_file():
            stored.unlink()
