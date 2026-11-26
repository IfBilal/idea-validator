from common.service import create_app

from .api.routes import router
from .config import Settings

settings = Settings()
app = create_app(name="orchestrator", module="M3", settings=settings)
app.include_router(router)
