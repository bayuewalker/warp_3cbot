"""FastAPI surface. Route behavior lives in httpapi.dispatch."""


def create_app():
    from fastapi import FastAPI, Request

    app = FastAPI(title="WARP 3C", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/stream")
    async def stream(request: Request):
        import threading

        from fastapi.responses import JSONResponse, StreamingResponse

        from .httpapi import authorized
        from .live import BUS, iter_server_events

        header = request.headers.get("authorization")
        cookie = request.headers.get("cookie")
        if not authorized(header, cookie):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        inbox = BUS.subscribe()
        if inbox is None:
            return JSONResponse({"error": "too_many_streams"}, status_code=503)
        stop = threading.Event()

        def chunks():
            try:
                yield from iter_server_events(inbox, BUS, stop, lambda: authorized(header, cookie))
            finally:
                BUS.unsubscribe(inbox)

        return StreamingResponse(
            chunks(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
