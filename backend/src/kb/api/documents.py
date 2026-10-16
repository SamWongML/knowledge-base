"""Document CRUD, uploads and re-indexing. Ingestion itself runs in the worker."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from kb.api.deps import require_token
from kb.config import get_settings
from kb.ingest import service
from kb.ingest.parsers import ParseError
from kb.schemas import (
    Document,
    DocumentIn,
    DocumentPatch,
    DocumentStatus,
    DocumentSummary,
    IngestAccepted,
    Model,
    Page,
)

router = APIRouter(tags=["documents"])
_write = [Depends(require_token)]


class ChunkOut(Model):
    id: int
    ord: int
    heading_path: str
    content: str
    context: str
    token_count: int
    char_start: int
    char_end: int
    embedded: bool


def _not_found(id: UUID) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"document {id} not found")


@router.get("/documents", response_model=Page[DocumentSummary])
async def list_documents(
    collection: str | None = None,
    status_: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    tag: str | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentSummary]:
    try:
        return await service.list_documents(
            collection=collection, status=status_, tag=tag, q=q, limit=limit, offset=offset
        )
    except service.UnknownCollectionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "/documents",
    response_model=IngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_write,
)
async def create_document(body: DocumentIn) -> IngestAccepted:
    try:
        return await service.create_document(body)
    except service.UnknownCollectionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while chunk := await upload.read(1 << 20):
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"{upload.filename}: larger than {limit // (1 << 20)} MB",
            )
    return bytes(buf)


@router.post(
    "/documents/upload",
    response_model=list[IngestAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_write,
)
async def upload_documents(
    files: Annotated[list[UploadFile], File(description="One or more files")],
    collection: Annotated[str, Form()] = "default",
    tags: Annotated[str, Form(description="Comma-separated tags")] = "",
) -> list[IngestAccepted]:
    limit = get_settings().max_upload_mb * (1 << 20)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    results: list[IngestAccepted] = []
    for upload in files:
        data = await _read_limited(upload, limit)
        try:
            results.append(
                await service.create_from_upload(
                    upload.filename or "upload",
                    data,
                    collection,
                    tag_list,
                    upload.content_type,
                )
            )
        except service.UnknownCollectionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except service.ConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return results


@router.get("/documents/{id}", response_model=Document)
async def get_document(id: UUID) -> Document:
    doc = await service.get_document(id)
    if doc is None:
        raise _not_found(id)
    return doc


@router.patch("/documents/{id}", response_model=Document, dependencies=_write)
async def update_document(id: UUID, body: DocumentPatch) -> Document:
    try:
        doc = await service.update_document(id, body)
    except service.UnknownCollectionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except service.ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if doc is None:
        raise _not_found(id)
    return doc


@router.delete("/documents/{id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_write)
async def delete_document(id: UUID) -> Response:
    if not await service.delete_document(id):
        raise _not_found(id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{id}/chunks", response_model=list[ChunkOut])
async def get_chunks(id: UUID) -> list[dict]:
    chunks = await service.get_chunks(id)
    if not chunks and await service.get_document(id) is None:
        raise _not_found(id)
    return chunks


@router.post(
    "/documents/{id}/reindex",
    response_model=IngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_write,
)
async def reindex_document(id: UUID) -> IngestAccepted:
    accepted = await service.reindex_document(id)
    if accepted is None:
        raise _not_found(id)
    return accepted
