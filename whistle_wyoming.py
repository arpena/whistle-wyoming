"""Wyoming STT server for Cactus Whistle, with keyword biasing from Home Assistant."""

import argparse
import array
import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import cactus
from cactus.agent.whistle import LANGUAGES as _NEEDLE_LANGUAGES, SAMPLE_RATE

from websockets.asyncio.client import connect
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger("whistle_wyoming")

LANGUAGES = list(_NEEDLE_LANGUAGES)

# Fallback filter, used only if HA reports no entities exposed to Assist.
VOICE_DOMAINS = {
    "light", "switch", "climate", "cover", "media_player", "fan", "lock",
    "vacuum", "scene", "script", "input_boolean",
}

LANG_DATA = {
    "en": {
        "keywords": {
            "tell", "turn", "switch", "on", "off", "up", "down", "open", "close",
            "activate", "deactivate", "what", "which", "how", "is", "was",
            "time", "temperature", "status", "set", "play", "pause", "stop",
            "toggle", "lock", "unlock", "run", "execute", "weather", "lights",
            "thermostat", "blind", "shade", "curtain", "door", "window", "fan"
        },
        "stopwords": {
            "the", "and", "a", "an", "of", "to", "in", "for", "with", "on", "at", "by", "from", "please"
        }
    },
    "es": {
        "keywords": {
            "dime", "enciende", "prende", "apaga", "sube", "baja", "pon", "abre", "cierra",
            "activa", "desactiva", "qué", "cuál", "cuánto", "está", "encendida", "encendido",
            "apagada", "apagado", "hora", "temperatura", "estado", "fija", "reproduce", "pausa",
            "detén", "bloquea", "desbloquea", "ejecuta", "clima", "luces", "persiana", "puerta", "ventana"
        },
        "stopwords": {
            "del", "las", "los", "con", "para", "por", "una", "uno", "de", "la", "el", "un", "por favor"
        }
    },
    "de": {
        "keywords": {
            "schalte", "an", "aus", "auf", "zu", "wie", "ist", "zeit", "temperatur",
            "aktiviere", "deaktiviere", "öffne", "schließe", "sperre", "entsperre", "starte", "stoppe",
            "pausiere", "status", "grad", "wetter", "licht", "lampen", "rollo", "rollladen", "tür", "fenster", "lüfter"
        },
        "stopwords": {
            "der", "die", "das", "ein", "eine", "und", "zu", "von", "mit", "für", "im", "in", "den", "dem", "bitte"
        }
    },
    "fr": {
        "keywords": {
            "allume", "éteins", "eteins", "ouvre", "ferme", "active", "désactive", "desactive", "quel", "quelle", "temps", "heure",
            "température", "temperature", "statut", "monte", "baisse", "mets", "lance", "arrête", "arrete", "verrouille", "déverrouille",
            "météo", "meteo", "lumière", "lumiere", "lampe", "volet", "store", "porte", "fenêtre", "fenetre", "ventilateur"
        },
        "stopwords": {
            "le", "la", "les", "un", "une", "de", "et", "en", "pour", "dans", "par", "du", "au", "aux", "s'il", "te", "plaît", "plait"
        }
    },
    "it": {
        "keywords": {
            "accendi", "spegni", "apri", "chiudi", "attiva", "disattiva", "che", "quale", "ora", "tempo",
            "temperatura", "stato", "alza", "abbassa", "imposta", "avvia", "ferma", "blocca", "sblocca",
            "meteo", "luce", "luci", "tapparella", "tenda", "porta", "finestra", "ventilatore"
        },
        "stopwords": {
            "il", "lo", "la", "i", "gli", "le", "un", "una", "di", "e", "per", "in", "da", "con", "su", "per", "favore"
        }
    },
    "nl": {
        "keywords": {
            "zet", "doe", "schakel", "draai", "aan", "uit", "open", "dicht", "start", "stop",
            "activeer", "deactiveer", "verhoog", "verlaag", "wat", "hoe", "hoeveel", "is", "was",
            "tijd", "temperatuur", "status", "weer", "verlichting", "lamp", "knop", "omhoog", "omlaag",
            "vergrendel", "ontgrendel", "rolluik", "gordijn", "deur", "raam", "ventilator"
        },
        "stopwords": {
            "de", "het", "een", "en", "van", "in", "voor", "met", "op", "te", "om", "bij", "alsjeblieft"
        }
    },
    "pl": {
        "keywords": {
            "włącz", "wyłącz", "otwórz", "zamknij", "aktywuj", "deaktywuj", "co", "jak", "czas", "godzina",
            "temperatura", "status", "podnieś", "opuść", "ustaw", "graj", "pauza", "zatrzymaj", "zablokuj",
            "odblokuj", "pogoda", "światło", "światła", "lampa", "roleta", "drzwi", "okno", "wentylator"
        },
        "stopwords": {
            "i", "w", "z", "na", "do", "że", "o", "to", "a", "po", "za", "dla", "pod", "ze", "proszę"
        }
    }
}

# What people actually say to Assist: imperatives and question words, not infinitives
# (A/B test: infinitives biased "apaga" -> "apagar"; these forms fixed "enciende"/"encendida").
BASE_KEYWORDS = set()
# Short/function words that add noise to biasing.
STOPWORDS = set()

def _split_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in cleaned.split() if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]


def _ws_url(ha_url: str) -> str:
    return re.sub(r"^http", "ws", ha_url.rstrip("/")) + "/api/websocket"


async def _ha_ws_fetch(ha_url: str, token: str, commands: list[str | dict]) -> list:
    """Authenticate to the HA websocket API and run commands, returning their results in order.

    Each command is a message type, or a dict with "type" plus extra fields."""
    async with connect(_ws_url(ha_url), max_size=64 * 1024 * 1024, open_timeout=10) as ws:
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"unexpected HA hello: {msg.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"HA auth failed: {msg.get('message', msg.get('type'))}")

        messages = [c if isinstance(c, dict) else {"type": c} for c in commands]
        for i, message in enumerate(messages, start=1):
            await ws.send(json.dumps({**message, "id": i}))
        results: dict[int, object] = {}
        while len(results) < len(commands):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if msg.get("type") != "result":
                continue
            if not msg.get("success"):
                raise RuntimeError(f"{messages[msg['id'] - 1]['type']} failed: {msg.get('error')}")
            results[msg["id"]] = msg["result"]
        return [results[i] for i in range(1, len(commands) + 1)]


def _add_name(keywords: set[str], name: str | None) -> None:
    if name and name.strip():
        keywords.add(name.strip().lower())
        keywords.update(_split_words(name))


async def fetch_ha_keywords(ha_url: str, token: str, max_keywords: int) -> list[str]:
    """Names + aliases of entities exposed to Assist, plus area names + aliases."""
    exposure, areas, states = await _ha_ws_fetch(ha_url, token, [
        "homeassistant/expose_entity/list",
        "config/area_registry/list",
        "get_states",
    ])

    exposed = {eid for eid, opts in exposure.get("exposed_entities", {}).items()
               if opts.get("conversation")}
    if not exposed:
        _LOGGER.warning("No entities exposed to Assist; falling back to domain filter")
        exposed = {s["entity_id"] for s in states if s["entity_id"].split(".", 1)[0] in VOICE_DOMAINS}

    # config/entity_registry/list omits aliases; get_entries returns the full entries.
    (entries,) = await _ha_ws_fetch(ha_url, token, [
        {"type": "config/entity_registry/get_entries", "entity_ids": sorted(exposed)},
    ])
    keywords: set[str] = set(BASE_KEYWORDS)
    # Aliases are what people actually say; when an entity has them, skip its (often technical) name.
    aliases_by_entity = {
        entity_id: [a for a in (entry.get("aliases") or []) if a]
        for entity_id, entry in entries.items() if entry
    }
    for state in states:
        entity_id = state["entity_id"]
        if entity_id not in exposed:
            continue
        aliases = aliases_by_entity.get(entity_id)
        if aliases:
            for alias in aliases:
                _add_name(keywords, alias)
        else:
            _add_name(keywords, (state.get("attributes") or {}).get("friendly_name"))
    for area in areas:
        for name in [area.get("name"), *(area.get("aliases") or [])]:
            if name:
                keywords.add(name.strip().lower())

    _LOGGER.info("%d entities exposed to Assist -> %d keywords", len(exposed), len(keywords))
    # Prefer full phrases (longer) when trimming.
    return sorted(keywords, key=lambda k: (-len(k.split()), k))[:max_keywords]


class KeywordStore:
    def __init__(self, ha_url: str | None, token: str | None, max_keywords: int):
        self.ha_url, self.token, self.max_keywords = ha_url, token, max_keywords
        self.keywords: list[str] = sorted(BASE_KEYWORDS)

    async def refresh(self) -> None:
        if not (self.ha_url and self.token):
            _LOGGER.warning("HA_URL/HA_TOKEN not set: using only base keywords")
            return
        try:
            self.keywords = await fetch_ha_keywords(self.ha_url, self.token, self.max_keywords)
            _LOGGER.info("Loaded %d keywords from HA, e.g. %s", len(self.keywords), self.keywords[:10])
        except Exception as err:
            _LOGGER.error("Keyword refresh from HA failed (keeping previous list): %s", err)

    async def refresh_forever(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.refresh()


class WhistleEventHandler(AsyncEventHandler):
    def __init__(self, info: Info, args, store: KeywordStore, executor: ThreadPoolExecutor,
                 *handler_args, **handler_kwargs):
        super().__init__(*handler_args, **handler_kwargs)
        self.info = info
        self.args = args
        self.store = store
        self.executor = executor
        self.converter = AudioChunkConverter(rate=SAMPLE_RATE, width=2, channels=1)
        self.audio = bytearray()
        self.language = args.language

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.info.event())
            return True

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            if transcribe.language in LANGUAGES:
                self.language = transcribe.language
            return True

        if AudioStart.is_type(event.type):
            self.audio = bytearray()
            return True

        if AudioChunk.is_type(event.type):
            chunk = self.converter.convert(AudioChunk.from_event(event))
            self.audio.extend(chunk.audio)
            return True

        if AudioStop.is_type(event.type):
            # cactus wants float32 samples in [-1, 1]; raw bytes would be misread as float32.
            pcm = array.array("h")
            pcm.frombytes(bytes(self.audio))
            samples = array.array("f", (s / 32768.0 for s in pcm))
            self.audio = bytearray()

            start = time.monotonic()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self.executor,
                partial(cactus.transcribe, samples, language=self.language,
                        keywords=self.store.keywords or None),
            )
            text = (result.get("text") or "").strip()
            _LOGGER.info("[%s] %.1fs audio -> %r (%.0f ms)", self.language,
                         len(samples) / SAMPLE_RATE, text, (time.monotonic() - start) * 1000)
            try:
                await self.write_event(Transcript(text=text).event())
            except (ConnectionResetError, BrokenPipeError):
                # HA gave up on this pipeline run (timeout/cancel) before we answered.
                _LOGGER.warning("Client disconnected before the transcript was sent")
            return False  # one transcription per connection, like other Wyoming STT servers

        return True


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="tcp://0.0.0.0:10300")
    parser.add_argument("--language", default=os.getenv("WHISTLE_LANGUAGE", "en"), choices=LANGUAGES)
    parser.add_argument("--ha-url", default=os.getenv("HA_URL"))
    parser.add_argument("--max-keywords", type=int, default=int(os.getenv("WHISTLE_MAX_KEYWORDS", "200")))
    parser.add_argument("--refresh-interval", type=int, default=int(os.getenv("WHISTLE_REFRESH", "3600")),
                        help="Seconds between keyword refreshes from HA (0 = only at startup)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    
    # Inject the language profile before init
    lang_profile = LANG_DATA.get(args.language, LANG_DATA["en"])
    BASE_KEYWORDS.update(lang_profile["keywords"])
    STOPWORDS.update(lang_profile["stopwords"])
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    store = KeywordStore(args.ha_url, os.getenv("HA_TOKEN"), args.max_keywords)
    await store.refresh()
    if args.refresh_interval > 0:
        asyncio.create_task(store.refresh_forever(args.refresh_interval))

    # Load engine + weights once (downloads on first run), off the event loop.
    # The engine holds one global model, so all inference goes through a single worker thread.
    executor = ThreadPoolExecutor(max_workers=1)
    await asyncio.get_running_loop().run_in_executor(
        executor, partial(cactus.transcribe, array.array("f", [0.0] * SAMPLE_RATE), language=args.language)
    )
    _LOGGER.info("Whistle model loaded")

    info = Info(asr=[AsrProgram(
        name="whistle",
        description="Cactus Whistle speech-to-text",
        attribution=Attribution(name="Cactus Compute", url="https://huggingface.co/Cactus-Compute/whistle"),
        installed=True,
        version=None,
        models=[AsrModel(
            name="whistle",
            description="Whistle 16.9 MB",
            attribution=Attribution(name="Cactus Compute", url="https://huggingface.co/Cactus-Compute/whistle"),
            installed=True,
            languages=LANGUAGES,
            version=None,
        )],
    )])

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Listening on %s", args.uri)
    await server.run(partial(WhistleEventHandler, info, args, store, executor))


if __name__ == "__main__":
    asyncio.run(main())