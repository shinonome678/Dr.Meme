from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from backends import ImageNotFoundError
from config import BASE_DIR, Settings

from .manager import InvalidTokenError, KarutaManager


LOGGER = logging.getLogger(__name__)


class KarutaWebServer:
    def __init__(self, *, manager: KarutaManager, settings: Settings) -> None:
        self.manager = manager
        self.settings = settings
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.template_path = BASE_DIR / "web" / "karuta.html"
        self.static_dir = BASE_DIR / "web" / "static"
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/karuta/{game_id}", self.index)
        self.app.router.add_get("/karuta/ws/{game_id}", self.websocket)
        self.app.router.add_get(
            "/karuta/api/games/{game_id}/memes/{meme_id}/image",
            self.meme_image,
        )
        self.app.router.add_get("/karuta/api/games/{game_id}/memes", self.meme_list)
        self.app.router.add_get("/karuta/audio/{game_id}/{filename}", self.audio)
        self.app.router.add_static("/karuta/static", self.static_dir, show_index=False)

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.settings.web_host, self.settings.web_port)
        await self.site.start()
        LOGGER.info(
            "Karuta web server started at http://%s:%s",
            self.settings.web_host,
            self.settings.web_port,
        )

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None

    async def index(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]
        token = request.query.get("token", "")
        try:
            self.manager.authenticate(game_id=game_id, token=token)
        except InvalidTokenError:
            return web.Response(text="Invalid karuta URL.", status=403)

        html = self.template_path.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        game_id = request.match_info["game_id"]
        token = request.query.get("token", "")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        try:
            player = await self.manager.connect(game_id=game_id, token=token, websocket=ws)
        except InvalidTokenError:
            await ws.send_json({"type": "error", "message": "invalid token"})
            await ws.close()
            return ws

        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(message.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "bad json"})
                        continue
                    await self.manager.handle_client_message(
                        game_id=game_id,
                        token=token,
                        data=data,
                    )
                elif message.type == WSMsgType.ERROR:
                    LOGGER.warning("Karuta websocket error: %s", ws.exception())
        finally:
            await self.manager.disconnect(game_id=game_id, user_id=player.user_id, websocket=ws)
        return ws

    async def meme_image(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]
        token = request.query.get("token", "")
        try:
            meme_id = int(request.match_info["meme_id"])
            meme = await self.manager.get_meme_for_image(
                game_id=game_id,
                token=token,
                meme_id=meme_id,
            )
            payload = await self.manager.backend.read_meme_image(meme)
        except (InvalidTokenError, ValueError):
            return web.Response(status=403)
        except ImageNotFoundError:
            return web.Response(status=404)

        return web.Response(
            body=payload.data,
            content_type=payload.content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    async def meme_list(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]
        token = request.query.get("token", "")
        query = request.query.get("q", "")
        try:
            page = int(request.query.get("page", "1"))
            payload = await self.manager.list_memes_for_session(
                game_id=game_id,
                token=token,
                query=query,
                page=page,
            )
        except (InvalidTokenError, ValueError):
            return web.json_response({"error": "invalid request"}, status=403)
        return web.json_response(payload)

    async def audio(self, request: web.Request) -> web.Response:
        game_id = request.match_info["game_id"]
        token = request.query.get("token", "")
        filename = request.match_info["filename"]
        if Path(filename).name != filename or not filename.endswith(".wav"):
            return web.Response(status=404)

        try:
            session, _ = self.manager.authenticate(game_id=game_id, token=token)
        except InvalidTokenError:
            return web.Response(status=403)

        path = session.audio_dir / filename
        try:
            path.resolve().relative_to(session.audio_dir.resolve())
        except ValueError:
            return web.Response(status=404)
        if not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(
            path,
            headers={"Cache-Control": "private, max-age=3600"},
        )
