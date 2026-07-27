"""Présence en ligne — endpoint de battement appelé par un petit script côté
client pour garder un membre "en ligne" pendant qu'un onglet reste ouvert
sans navigation. Le battement lui-même est géré par get_current_user
(app/dependencies.py) — cette route n'a rien de plus à faire."""
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.dependencies import require_auth

router = APIRouter(prefix="/presence", tags=["presence"])


@router.post("/ping", status_code=204)
async def presence_ping(ctx: Annotated[tuple, Depends(require_auth)]):
    return Response(status_code=204)
