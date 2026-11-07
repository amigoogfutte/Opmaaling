"""Vinduesopmåling: gemmer opmålinger fra mobil-appen i Home Assistant og viser appen."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import APP_URL, DOMAIN, MAX_FOTO_BYTES, MAX_SAG_BYTES, PANEL_URL_PATH, STORAGE_DIR

_LOGGER = logging.getLogger(__name__)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
WWW = Path(__file__).parent / "www"


class Store:
    """Filbaseret lager i /config/opmaaling."""

    def __init__(self, root: str) -> None:
        self.sager = Path(root) / "sager"
        self.fotos = Path(root) / "fotos"

    def ensure(self) -> None:
        self.sager.mkdir(parents=True, exist_ok=True)
        self.fotos.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def list_sager(self) -> list[dict]:
        out = []
        for f in self.sager.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": d.get("id"), "updated": d.get("updated", 0), "deleted": bool(d.get("deleted"))})
            except (OSError, ValueError):
                _LOGGER.warning("Kunne ikke læse %s", f)
        return out

    def get_sag(self, sag_id: str) -> bytes | None:
        f = self.sager / f"{sag_id}.json"
        return f.read_bytes() if f.exists() else None

    def put_sag(self, sag_id: str, obj: dict) -> tuple[bool, dict | None]:
        f = self.sager / f"{sag_id}.json"
        if f.exists():
            try:
                cur = json.loads(f.read_text(encoding="utf-8"))
                if cur.get("updated", 0) > obj.get("updated", 0):
                    return False, cur
            except (OSError, ValueError):
                pass
        self._write(f, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        return True, None

    def list_fotos(self) -> list[str]:
        return [f.stem for f in self.fotos.glob("*.jpg")]

    def get_foto(self, foto_id: str) -> bytes | None:
        f = self.fotos / f"{foto_id}.jpg"
        return f.read_bytes() if f.exists() else None

    def put_foto(self, foto_id: str, data: bytes) -> None:
        self._write(self.fotos / f"{foto_id}.jpg", data)


class _Base(HomeAssistantView):
    requires_auth = True

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        self.hass = hass
        self.store = store


class SagerView(_Base):
    url = "/api/opmaaling/sager"
    name = "api:opmaaling:sager"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(await self.hass.async_add_executor_job(self.store.list_sager))


class SagView(_Base):
    url = "/api/opmaaling/sager/{sag_id}"
    name = "api:opmaaling:sag"

    async def get(self, request: web.Request, sag_id: str) -> web.Response:
        if not _ID_RE.match(sag_id):
            return self.json_message("Ugyldigt id", 400)
        data = await self.hass.async_add_executor_job(self.store.get_sag, sag_id)
        if data is None:
            return self.json_message("Findes ikke", 404)
        return web.Response(body=data, content_type="application/json")

    async def put(self, request: web.Request, sag_id: str) -> web.Response:
        if not _ID_RE.match(sag_id):
            return self.json_message("Ugyldigt id", 400)
        body = await request.read()
        if len(body) > MAX_SAG_BYTES:
            return self.json_message("For stor", 413)
        try:
            obj = json.loads(body)
        except ValueError:
            return self.json_message("Ugyldig JSON", 400)
        if not isinstance(obj, dict) or obj.get("id") != sag_id:
            return self.json_message("Forkert indhold", 400)
        if not obj.get("deleted") and not isinstance(obj.get("pos"), list) and not isinstance(obj.get("adresser"), list):
            return self.json_message("Mangler positioner", 400)
        ok, cur = await self.hass.async_add_executor_job(self.store.put_sag, sag_id, obj)
        if not ok:
            return self.json({"conflict": True, "sag": cur}, 409)
        return self.json({"ok": True})


class FotosView(_Base):
    url = "/api/opmaaling/fotos"
    name = "api:opmaaling:fotos"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(await self.hass.async_add_executor_job(self.store.list_fotos))


class FotoView(_Base):
    url = "/api/opmaaling/fotos/{foto_id}"
    name = "api:opmaaling:foto"

    async def get(self, request: web.Request, foto_id: str) -> web.Response:
        if not _ID_RE.match(foto_id):
            return self.json_message("Ugyldigt id", 400)
        data = await self.hass.async_add_executor_job(self.store.get_foto, foto_id)
        if data is None:
            return self.json_message("Findes ikke", 404)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000"})

    async def put(self, request: web.Request, foto_id: str) -> web.Response:
        if not _ID_RE.match(foto_id):
            return self.json_message("Ugyldigt id", 400)
        body = await request.read()
        if not body or len(body) > MAX_FOTO_BYTES:
            return self.json_message("Ugyldig størrelse", 413)
        if body[:3] != b"\xff\xd8\xff":
            return self.json_message("Kun JPEG", 415)
        await self.hass.async_add_executor_job(self.store.put_foto, foto_id, body)
        return self.json({"ok": True})


class AppRedirectView(HomeAssistantView):
    """Kort adresse: /opmaaling → appen."""

    url = "/opmaaling-start"
    name = "opmaaling:start"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        raise web.HTTPFound(f"{APP_URL}/index.html")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Sæt integrationen op."""
    store = Store(hass.config.path(STORAGE_DIR))
    await hass.async_add_executor_job(store.ensure)

    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("http_registered"):
        # HTTP-ruter kan ikke fjernes igen, så de registreres kun én gang pr. opstart.
        await hass.http.async_register_static_paths([StaticPathConfig(APP_URL, str(WWW), False)])
        for view in (SagerView(hass, store), SagView(hass, store), FotosView(hass, store), FotoView(hass, store), AppRedirectView()):
            hass.http.register_view(view)
        data["http_registered"] = True

    frontend.async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Opmåling",
        sidebar_icon="mdi:window-closed-variant",
        frontend_url_path=PANEL_URL_PATH,
        config={"url": f"{APP_URL}/index.html"},
        require_admin=False,
        update=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Fjern sidepanelet."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    return True
