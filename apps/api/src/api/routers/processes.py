"""Background processes of a session: list, read output, stop.

The agent starts them with the ``process_*`` tools; this is the person's view of the same
processes. Listing reconciles with the sandbox when it is running and otherwise returns the
stored history, so a finished session still shows what it ran.
"""

from __future__ import annotations

from agent_core.tools.processes import is_valid_id, read_output, stop_process
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from api.auth.access import _check_session_access, _get_request_identity
from api.services.process_tracker import ProcessTracker, TrackedProcess
from api.services.settings import effective_settings
from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionRecord, SessionStoreProtocol

router = APIRouter(prefix="/v1", tags=["Processes"])


class ProcessView(BaseModel):
    process_id: str
    name: str
    command: str
    state: str
    port: int | None = None
    exit_code: int | None = None
    started_at: int
    ended_at: int | None = None

    @classmethod
    def of(cls, process: TrackedProcess) -> ProcessView:
        return cls(**process.as_payload())


class ProcessList(BaseModel):
    processes: list[ProcessView]
    sandbox_running: bool


class ProcessOutput(BaseModel):
    process_id: str
    output: str


class StopResult(BaseModel):
    process_id: str
    message: str


async def _authorised_session(
    session_id: str, request: Request, *, require_write: bool
) -> SessionRecord:
    _, user_id, roles = _get_request_identity(request)
    store: SessionStoreProtocol = request.app.state.session_store
    record = await store.get_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_access(record, user_id, roles, require_write=require_write)
    return record


async def _running_driver(request: Request, session_id: str) -> SandboxDriverProtocol | None:
    driver: SandboxDriverProtocol | None = getattr(request.app.state, "sandbox_driver", None)
    if driver is None or await driver.get_status(session_id) != SandboxStatus.RUNNING:
        return None
    return driver


def _tracker(request: Request) -> ProcessTracker:
    return ProcessTracker.for_app(
        getattr(request.app.state, "db_manager", None), request.app.state.event_bus
    )


async def _keepalive_seconds(request: Request, org_id: str) -> float:
    cfg = await effective_settings(
        getattr(request.app.state, "db_manager", None), org_id, "sandboxes"
    )
    return float(cfg.get("process_keepalive_minutes") or 0) * 60


@router.get("/sessions/{session_id}/processes", response_model=ProcessList)
async def list_session_processes(session_id: str, request: Request) -> ProcessList:
    record = await _authorised_session(session_id, request, require_write=False)
    tracker = _tracker(request)
    driver = await _running_driver(request, session_id)
    processes = await tracker.refresh(
        session_id,
        record.tenant_org_id,
        driver,
        keepalive_seconds=await _keepalive_seconds(request, record.tenant_org_id),
    )
    return ProcessList(
        processes=[ProcessView.of(p) for p in processes], sandbox_running=driver is not None
    )


@router.get("/sessions/{session_id}/processes/{process_id}/output", response_model=ProcessOutput)
async def read_process_output(
    session_id: str,
    process_id: str,
    request: Request,
    lines: int = Query(default=100, ge=1, le=500),
) -> ProcessOutput:
    await _authorised_session(session_id, request, require_write=False)
    if not is_valid_id(process_id):
        raise HTTPException(status_code=404, detail="Process not found")
    driver = await _running_driver(request, session_id)
    if driver is None:
        raise HTTPException(
            status_code=409,
            detail="The sandbox is not running, so the process log is no longer available.",
        )
    return ProcessOutput(
        process_id=process_id, output=await read_output(driver, session_id, process_id, lines)
    )


@router.post("/sessions/{session_id}/processes/{process_id}/stop", response_model=StopResult)
async def stop_session_process(session_id: str, process_id: str, request: Request) -> StopResult:
    record = await _authorised_session(session_id, request, require_write=True)
    if not is_valid_id(process_id):
        raise HTTPException(status_code=404, detail="Process not found")
    driver = await _running_driver(request, session_id)
    if driver is None:
        raise HTTPException(status_code=409, detail="The sandbox is not running.")
    message = await stop_process(driver, session_id, process_id)
    if message.startswith("Error:"):
        raise HTTPException(status_code=404, detail=message.removeprefix("Error: "))
    await _tracker(request).refresh(session_id, record.tenant_org_id, driver)
    return StopResult(process_id=process_id, message=message)
