"""Collections ("spaces") that group documents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from kb.api.deps import require_token
from kb.ingest import service
from kb.schemas import Collection, CollectionIn

router = APIRouter(tags=["collections"])


@router.get("/collections", response_model=list[Collection])
async def list_collections() -> list[Collection]:
    return await service.list_collections()


@router.post(
    "/collections",
    response_model=Collection,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_token)],
)
async def create_collection(body: CollectionIn) -> Collection:
    try:
        return await service.create_collection(body)
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete(
    "/collections/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_token)],
)
async def delete_collection(slug: str) -> Response:
    try:
        deleted = await service.delete_collection(slug)
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"collection {slug!r} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
