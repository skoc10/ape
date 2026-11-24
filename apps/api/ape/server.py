"""FastAPI entrypoint: REST + agent WebSocket gateway (image: ape-api)."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="APE Control Plane API", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
