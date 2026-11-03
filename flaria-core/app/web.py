"""Serve the built web app from the API process (single-container deploys such as Render).

Every app page gets the same empty shell (``app.html``) with ``noindex``: they are
sign-in-only, per-user screens. The public landing page is deployed separately
as a static site, so the app's root redirects to sign-in rather than duplicating it.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

HTML_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self'; "
    "font-src 'self'; img-src 'self' data: blob: https://commons.wikimedia.org https://upload.wikimedia.org "
    "https://images.unsplash.com; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)
HTML_HEADERS = {
    "Content-Security-Policy": HTML_CSP,
    # The app needs the camera and microphone on its own pages (API responses deny both).
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-cache",
}
ROOT_FILES = ("favicon.svg", "favicon-48.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
              "og-image.png", "site.webmanifest")
RESERVED = ("api/", "health/", "ws/")


def mount_web(app: FastAPI, dist: Path) -> None:
    shell = dist / "app.html"
    if not shell.is_file():
        raise RuntimeError(f"{shell} not found: build the web app with `npm run build` first")
    for sub in ("assets", "mediapipe", "worklets"):
        if (dist / sub).is_dir():
            app.mount(f"/{sub}", StaticFiles(directory=dist / sub), name=f"web-{sub}")

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        # Pages here carry noindex; crawlers must be allowed to fetch them to see it.
        return PlainTextResponse("User-agent: *\nDisallow: /api/\nDisallow: /ws/\n")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith(RESERVED):
            raise HTTPException(status_code=404, detail="Not found")
        if path in ROOT_FILES:
            return FileResponse(dist / path, headers={"Cache-Control": "public, max-age=86400"})
        if path == "":
            return RedirectResponse("/login", status_code=307)
        return FileResponse(shell, media_type="text/html", headers=HTML_HEADERS)
