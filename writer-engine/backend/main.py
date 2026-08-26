from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from backend import config
from backend.analyzers.external_tools import integration_status
from backend.manuscript import analyze_manuscript, calibrate_corpus
from backend.models import RunRequest
from backend.pipeline import compare_texts, run_pipeline
from backend.profiles import get_profile, list_profiles, save_profile
from backend.projects import agent_setup, create_project, list_projects
from backend.storage import load_run


class TextRequest(BaseModel):
    text: str = ""
    profile: str = config.DEFAULT_PROFILE
    mode: str = "diagnose"
    passes: int = 1
    provider: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None
    preserve: list[str] | None = None
    genre: str | None = None
    aggressiveness: str = "medium"
    persist: StrictBool = False


class CompareRequest(BaseModel):
    before: str = Field(default="")
    after: str = Field(default="")
    profile: str = config.DEFAULT_PROFILE
    persist: StrictBool = False


class VoiceProfileRequest(BaseModel):
    samples: list[str]
    name: str


class ProfileSaveRequest(BaseModel):
    profile: dict[str, Any]


class ProjectRequest(BaseModel):
    name: str
    profile: str = config.DEFAULT_PROFILE


class DocumentInput(BaseModel):
    name: str
    text: str


class ManuscriptRequest(BaseModel):
    documents: list[DocumentInput]
    profile: str = config.DEFAULT_PROFILE
    overrides: dict[str, Any] | None = None
    project: str | None = None
    persist: StrictBool = False


class CalibrationRequest(BaseModel):
    samples: list[str]
    name: str
    reference_samples: list[str] = Field(default_factory=list)


app = FastAPI(title="ThothPad", version=config.ENGINE_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8789", "http://localhost:8789"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def validation_error(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _run(req: TextRequest, mode: str) -> dict[str, Any]:
    return run_pipeline(
        RunRequest(
            text=req.text,
            profile=req.profile,
            mode=mode,
            passes=req.passes,
            provider=req.provider,
            overrides=req.overrides,
            preserve=req.preserve,
            genre=req.genre,
            aggressiveness=req.aggressiveness,
            persist=req.persist,
        )
    )


@app.post("/api/diagnose")
def diagnose(req: TextRequest) -> dict[str, Any]:
    return _run(req, "diagnose")


@app.post("/api/rewrite")
def rewrite(req: TextRequest) -> dict[str, Any]:
    return _run(req, req.mode if req.mode in {"rewrite", "line_edit", "write_from_brief"} else "rewrite")


@app.post("/api/deslop")
def deslop(req: TextRequest) -> dict[str, Any]:
    return _run(req, "deslop")


@app.post("/api/compare")
def compare(req: CompareRequest) -> dict[str, Any]:
    return compare_texts(req.before, req.after, req.profile, persist=req.persist)


@app.post("/api/voice-profile")
def voice_profile(req: VoiceProfileRequest) -> dict[str, Any]:
    from backend.voice_profile import build_voice_profile

    return build_voice_profile(req.samples, req.name)


@app.post("/api/manuscript")
def manuscript(req: ManuscriptRequest) -> dict[str, Any]:
    return analyze_manuscript(
        [document.model_dump() for document in req.documents],
        req.profile,
        overrides=req.overrides,
        project=req.project,
        persist=req.persist,
    )


@app.post("/api/calibrate")
def calibrate(req: CalibrationRequest) -> dict[str, Any]:
    return calibrate_corpus(req.samples, req.name, req.reference_samples)


@app.get("/api/integrations")
def integrations() -> dict[str, dict[str, Any]]:
    return integration_status()


@app.get("/api/profiles")
def profiles() -> list[dict[str, Any]]:
    return list_profiles()


@app.get("/api/profiles/{name}")
def profile_get(name: str) -> dict[str, Any]:
    return get_profile(name)


@app.put("/api/profiles/{name}")
def profile_put(name: str, req: ProfileSaveRequest) -> dict[str, Any]:
    return save_profile(name, req.profile)


@app.get("/api/projects")
def projects_get() -> list[dict[str, Any]]:
    return list_projects()


@app.post("/api/projects")
def projects_post(req: ProjectRequest) -> dict[str, Any]:
    return create_project(req.name, req.profile)


@app.get("/api/agent-setup")
def agent_setup_get() -> dict[str, Any]:
    return agent_setup()


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    try:
        return load_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


if config.FRONTEND_DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST_DIR / "assets"), name="assets")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    dist_index = config.FRONTEND_DIST_DIR / "index.html"
    if dist_index.exists():
        return dist_index.read_text(encoding="utf-8")
    fallback = Path(__file__).resolve().parent / "static_fallback.html"
    return fallback.read_text(encoding="utf-8")
