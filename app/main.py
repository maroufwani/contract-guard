"""FastAPI application: health check and the service registry pages."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError, api_error_handler
from app.api.consumers import router as consumers_router
from app.api.services import router as services_router
from app.db.models import Service
from app.db.session import get_session, init_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the database tables on startup."""
    init_db()
    yield


app = FastAPI(title="Contract Guard", lifespan=lifespan)
app.include_router(services_router)
app.include_router(consumers_router)
app.add_exception_handler(ApiError, api_error_handler)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def home(request: Request, db: Session = Depends(get_session)):
    services = list(db.scalars(select(Service).order_by(Service.name)))
    return templates.TemplateResponse(
        request=request, name="index.html", context={"services": services}
    )
