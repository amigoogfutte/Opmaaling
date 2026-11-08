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
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import Unauthorized

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

    def _alle(self) -> list[dict]:
        out = []
        for f in self.sager.glob("*.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                _LOGGER.warning("Kunne ikke læse %s", f)
        return out

    def list_sager(self, user) -> list[dict]:
        return [
            {"id": d.get("id"), "updated": d.get("updated", 0), "deleted": bool(d.get("deleted"))}
            for d in self._alle()
            if adgang(user, d)
        ]

    def read_sag(self, sag_id: str) -> dict | None:
        f = self.sager / f"{sag_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put_sag(self, sag_id: str, obj: dict, user) -> tuple[int, dict | None]:
        """Gem en sag. Returnerer (status, data): 200 ok, 409 konflikt (nyere findes), 403 ingen adgang."""
        f = self.sager / f"{sag_id}.json"
        cur = self.read_sag(sag_id)
        if cur is not None:
            if not adgang(user, cur):
                return 403, None
            if cur.get("updated", 0) > obj.get("updated", 0):
                return 409, cur
            ejer_el_admin = user.is_admin or (cur.get("ejer") and cur.get("ejer") == user.id)
            if obj.get("deleted") and not ejer_el_admin:
                return 403, None   # en sag, der er delt med en, kan man ikke slette for de andre
            # ejeren kan ikke ændres; kun ejer og administratorer må ændre hvem sagen er delt med
            obj["ejer"] = cur.get("ejer")
            if not ejer_el_admin:
                obj["delt"] = cur.get("delt", [])
        else:
            obj["ejer"] = user.id
            if not user.is_admin:
                obj["delt"] = []
        delt = obj.get("delt")
        obj["delt"] = [u for u in delt if isinstance(u, str)][:50] if isinstance(delt, list) else []
        self._write(f, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        return 200, {"ejer": obj.get("ejer"), "delt": obj["delt"]}

    def fotos_med_adgang(self, user) -> set[str] | None:
        """Billeder i sager, brugeren har adgang til (None = alle, for administratorer)."""
        if user.is_admin:
            return None
        ids: set[str] = set()
        for d in self._alle():
            if adgang(user, d):
                _find_foto_ids(d, ids)
        return ids

    def get_firma(self) -> dict:
        f = self.sager.parent / "firma.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            return {}

    def put_firma(self, obj: dict) -> None:
        self._write(self.sager.parent / "firma.json", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def list_fotos(self, user) -> list[str]:
        alle = [f.stem for f in self.fotos.glob("*.jpg")]
        ok = self.fotos_med_adgang(user)
        return alle if ok is None else [i for i in alle if i in ok]

    def get_foto(self, foto_id: str) -> bytes | None:
        f = self.fotos / f"{foto_id}.jpg"
        return f.read_bytes() if f.exists() else None

    def put_foto(self, foto_id: str, data: bytes) -> None:
        self._write(self.fotos / f"{foto_id}.jpg", data)


def adgang(user, sag: dict) -> bool:
    """Administratorer ser alle sager; andre kun dem, de selv har oprettet eller som er delt med dem."""
    if user is None:
        return False
    if user.is_admin:
        return True
    return sag.get("ejer") == user.id or user.id in (sag.get("delt") or [])


def _find_foto_ids(obj, ids: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "fotoId" and isinstance(v, str):
                ids.add(v)
            else:
                _find_foto_ids(v, ids)
    elif isinstance(obj, list):
        for v in obj:
            _find_foto_ids(v, ids)


class _Base(HomeAssistantView):
    requires_auth = True

    def __init__(self, hass: HomeAssistant, store: Store) -> None:
        self.hass = hass
        self.store = store


class SagerView(_Base):
    url = "/api/opmaaling/sager"
    name = "api:opmaaling:sager"

    async def get(self, request: web.Request) -> web.Response:
        user = request["hass_user"]
        return self.json(await self.hass.async_add_executor_job(self.store.list_sager, user))


class SagView(_Base):
    url = "/api/opmaaling/sager/{sag_id}"
    name = "api:opmaaling:sag"

    async def get(self, request: web.Request, sag_id: str) -> web.Response:
        if not _ID_RE.match(sag_id):
            return self.json_message("Ugyldigt id", 400)
        d = await self.hass.async_add_executor_job(self.store.read_sag, sag_id)
        if d is None or not adgang(request["hass_user"], d):
            return self.json_message("Findes ikke", 404)
        return self.json(d)

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
        status, res = await self.hass.async_add_executor_job(self.store.put_sag, sag_id, obj, request["hass_user"])
        if status == 403:
            return self.json_message("Ingen adgang", 403)
        if status == 409:
            return self.json({"conflict": True, "sag": res}, 409)
        return self.json({"ok": True, **(res or {})})


class FotosView(_Base):
    url = "/api/opmaaling/fotos"
    name = "api:opmaaling:fotos"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(await self.hass.async_add_executor_job(self.store.list_fotos, request["hass_user"]))


class FotoView(_Base):
    url = "/api/opmaaling/fotos/{foto_id}"
    name = "api:opmaaling:foto"

    async def get(self, request: web.Request, foto_id: str) -> web.Response:
        if not _ID_RE.match(foto_id):
            return self.json_message("Ugyldigt id", 400)
        ok = await self.hass.async_add_executor_job(self.store.fotos_med_adgang, request["hass_user"])
        if ok is not None and foto_id not in ok:
            return self.json_message("Findes ikke", 404)
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
        # et eksisterende billede må kun overskrives af nogen, der har adgang til det
        user = request["hass_user"]
        findes = await self.hass.async_add_executor_job(self.store.get_foto, foto_id)
        if findes is not None:
            ok = await self.hass.async_add_executor_job(self.store.fotos_med_adgang, user)
            if ok is not None and foto_id not in ok:
                return self.json_message("Ingen adgang", 403)
        await self.hass.async_add_executor_job(self.store.put_foto, foto_id, body)
        return self.json({"ok": True})


class MigView(_Base):
    """Hvem er logget ind (bruges til at vise deling i appen)."""

    url = "/api/opmaaling/mig"
    name = "api:opmaaling:mig"

    async def get(self, request: web.Request) -> web.Response:
        u = request["hass_user"]
        return self.json({"id": u.id, "navn": u.name, "admin": u.is_admin})


class FirmaView(_Base):
    """Firmanavn og logo: alle kan læse det, kun administratorer kan rette det."""

    url = "/api/opmaaling/firma"
    name = "api:opmaaling:firma"

    async def get(self, request: web.Request) -> web.Response:
        return self.json(await self.hass.async_add_executor_job(self.store.get_firma))

    async def put(self, request: web.Request) -> web.Response:
        if not request["hass_user"].is_admin:
            return self.json_message("Kun administratorer kan rette firma", 403)
        body = await request.read()
        if len(body) > 1_500_000:
            return self.json_message("For stor", 413)
        try:
            obj = json.loads(body)
        except ValueError:
            return self.json_message("Ugyldig JSON", 400)
        if not isinstance(obj, dict):
            return self.json_message("Forkert indhold", 400)
        logo = obj.get("logo") or ""
        if logo and not (isinstance(logo, str) and logo.startswith("data:image/")):
            return self.json_message("Ugyldigt logo", 400)
        lev = obj.get("levAdr")
        lev = [str(x)[:120] for x in lev if str(x).strip()][:30] if isinstance(lev, list) else None
        ren = {"navn": str(obj.get("navn") or "")[:120], "logo": logo, "kunLogo": bool(obj.get("kunLogo")) and bool(logo), "updated": obj.get("updated") or 0}
        if lev is not None:
            ren["levAdr"] = lev
        await self.hass.async_add_executor_job(self.store.put_firma, ren)
        return self.json({"ok": True})


class BrugereView(_Base):
    """Brugere i Home Assistant, som en sag kan deles med."""

    url = "/api/opmaaling/brugere"
    name = "api:opmaaling:brugere"

    async def get(self, request: web.Request) -> web.Response:
        users = await self.hass.auth.async_get_users()
        return self.json([
            {"id": u.id, "navn": u.name, "admin": u.is_admin}
            for u in users
            if u.is_active and not u.system_generated and u.credentials
        ])


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
        for view in (SagerView(hass, store), SagView(hass, store), FotosView(hass, store), FotoView(hass, store), MigView(hass, store), BrugereView(hass, store), FirmaView(hass, store), AppRedirectView()):
            hass.http.register_view(view)
        data["http_registered"] = True

    async def saet_firma(call: ServiceCall) -> None:
        """Sæt firmanavn og/eller logo (kun administratorer)."""
        if call.context.user_id:
            u = await hass.auth.async_get_user(call.context.user_id)
            if u is None or not u.is_admin:
                raise Unauthorized()
        cur = await hass.async_add_executor_job(store.get_firma)
        if "navn" in call.data:
            cur["navn"] = str(call.data["navn"])[:120]
        if "logo" in call.data:
            logo = str(call.data["logo"] or "")
            if logo and not logo.startswith("data:image/"):
                raise ValueError("Logo skal være et billede (data:image/...)")
            cur["logo"] = logo
        if "kun_logo" in call.data:
            cur["kunLogo"] = bool(call.data["kun_logo"]) and bool(cur.get("logo"))
        cur["updated"] = int(__import__("time").time() * 1000)
        await hass.async_add_executor_job(store.put_firma, cur)

    if not hass.services.has_service(DOMAIN, "saet_firma"):
        hass.services.async_register(DOMAIN, "saet_firma", saet_firma)

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
