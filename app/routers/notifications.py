from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, User
from app.errors import ApiError
from app.db import run_tx
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(request: Request, user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    with request.app.state.factory() as session:
        rows = session.scalars(select(Notification).where(Notification.user_id == user.id)
                               .order_by(Notification.created_at.desc()).limit(50)).all()
        return [{"id": str(n.id), "kind": n.kind, "title": n.title, "message": n.message,
                 "is_read": n.is_read, "created_at": n.created_at.isoformat()} for n in rows]


@router.post("/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, str]:
    def work(session: Session) -> None:
        row = session.scalar(select(Notification).where(Notification.id == notification_id,
                          Notification.user_id == user.id).with_for_update())
        if row is None:
            raise ApiError("not_found", 404, "notification not found")
        row.is_read = True
    run_tx(request.app.state.factory, work)
    return {"status": "read"}
