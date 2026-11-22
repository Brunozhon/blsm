import uvicorn

from .config import Settings

settings = Settings.from_env()
uvicorn.run("blsm.main:app", host=settings.host, port=settings.port, proxy_headers=False)

