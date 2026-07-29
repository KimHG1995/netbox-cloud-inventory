import hmac
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from cloud_inventory.api.dependencies import (
    get_artifact_store,
    get_import_repository,
    get_job_queue,
)
from cloud_inventory.api.imports import (
    ApplyRequest,
    ImportRepositoryPort,
    JobQueuePort,
    SourceFileRecord,
    apply_import,
    create_import,
)
from cloud_inventory.config import Settings, get_settings
from cloud_inventory.domain.models import Provider, Realm
from cloud_inventory.ingest.artifact_store import ArtifactStore

router = APIRouter(prefix="/ui")
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
CSRF_COOKIE = "cloud_inventory_csrf"
CSRF_MAX_AGE_SECONDS = 2 * 60 * 60


class UiRepository(ImportRepositoryPort, Protocol):
    async def get_source_files(
        self,
        import_id: UUID,
    ) -> list[SourceFileRecord]: ...


class CsrfManager:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(
            secret,
            salt="cloud-inventory-csrf",
        )

    def issue(self) -> str:
        return self._serializer.dumps(secrets.token_urlsafe(32))

    def validate(self, cookie_token: str | None, form_token: str | None) -> None:
        if (
            cookie_token is None
            or form_token is None
            or not hmac.compare_digest(cookie_token, form_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid CSRF token",
            )
        try:
            self._serializer.loads(
                form_token,
                max_age=CSRF_MAX_AGE_SECONDS,
            )
        except (BadSignature, SignatureExpired) as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid or expired CSRF token",
            ) from error


def _csrf_manager(settings: Settings) -> CsrfManager:
    return CsrfManager(settings.csrf_secret.get_secret_value())


def _set_csrf_cookie(response: HTMLResponse | RedirectResponse, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/ui",
    )


@router.get("/imports", response_class=HTMLResponse)
async def upload_form(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    token = _csrf_manager(settings).issue()
    response = templates.TemplateResponse(
        request=request,
        name="imports.html",
        context={"csrf_token": token},
    )
    _set_csrf_cookie(response, token)
    return response


@router.post("/imports")
async def upload_from_ui(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    provider: Annotated[Provider, Form()],
    realm: Annotated[Realm, Form()],
    account_id: Annotated[str, Form(min_length=1, max_length=128)],
    export_type: Annotated[str, Form(min_length=1, max_length=64)],
    exported_at: Annotated[datetime, Form()],
    csrf_token: Annotated[str | None, Form()] = None,
    region: Annotated[str | None, Form(max_length=64)] = None,
    *,
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
    queue: Annotated[JobQueuePort, Depends(get_job_queue)],
    artifact_store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> RedirectResponse:
    _csrf_manager(settings).validate(
        request.cookies.get(CSRF_COOKIE),
        csrf_token,
    )
    result = await create_import(
        files=files,
        provider=provider,
        realm=realm,
        account_id=account_id,
        export_type=export_type,
        exported_at=exported_at,
        region=region,
        settings=settings,
        repository=repository,
        queue=queue,
        artifact_store=artifact_store,
    )
    document = json.loads(bytes(result.body))
    response = RedirectResponse(
        f"/ui/imports/{document['import_id']}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_csrf_cookie(response, _csrf_manager(settings).issue())
    return response


@router.get("/imports/{import_id}", response_class=HTMLResponse)
async def preview_page(
    request: Request,
    import_id: UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[UiRepository, Depends(get_import_repository)],
) -> HTMLResponse:
    preview = await repository.get_preview_page(import_id, 0, 500)
    sources = await repository.get_source_files(import_id)
    token = _csrf_manager(settings).issue()
    response = templates.TemplateResponse(
        request=request,
        name="preview.html",
        context={
            "csrf_token": token,
            "import_id": import_id,
            "preview": preview,
            "sources": sources,
        },
        status_code=(
            status.HTTP_200_OK
            if preview is not None
            else status.HTTP_202_ACCEPTED
        ),
    )
    _set_csrf_cookie(response, token)
    return response


@router.post("/imports/{import_id}/apply")
async def apply_from_ui(
    request: Request,
    import_id: UUID,
    batch_hash: Annotated[str, Form(pattern=r"^[0-9a-f]{64}$")],
    csrf_token: Annotated[str | None, Form()] = None,
    apply_valid_only: Annotated[bool, Form()] = False,
    *,
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[ImportRepositoryPort, Depends(get_import_repository)],
    queue: Annotated[JobQueuePort, Depends(get_job_queue)],
) -> RedirectResponse:
    _csrf_manager(settings).validate(
        request.cookies.get(CSRF_COOKIE),
        csrf_token,
    )
    result = await apply_import(
        import_id,
        ApplyRequest(
            batch_hash=batch_hash,
            apply_valid_only=apply_valid_only,
        ),
        repository,
        queue,
    )
    document = json.loads(bytes(result.body))
    response = RedirectResponse(
        f"/ui/imports/{import_id}?run_id={document['run_id']}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_csrf_cookie(response, _csrf_manager(settings).issue())
    return response
