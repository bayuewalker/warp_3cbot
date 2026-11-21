"""FastAPI surface. Route behavior lives in httpapi.dispatch."""


def create_app():
    from fastapi import FastAPI, Request

    app = FastAPI(title="WARP 3C", docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/", methods=["GET", "POST"])
    async def root(request: Request):
        return await _respond(request, "/")

    @app.api_route("/{full_path:path}", methods=["GET", "POST"])
    async def every(full_path: str, request: Request):
        return await _respond(request, "/" + full_path)

    return app


async def _respond(request, path: str):
    from fastapi import Response

    from .httpapi import dispatch

    body = await request.body()
    status, headers, payload = dispatch(request.method, path, request.headers, body)
    return Response(content=payload, status_code=status, headers=dict(headers))
