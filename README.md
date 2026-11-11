# wyoming-whistle

A [Wyoming protocol](https://github.com/OHF-Voice/wyoming) speech-to-text server for Home Assistant, built on
[Cactus Whistle](https://huggingface.co/Cactus-Compute/whistle), a 16.9 MB STT model that runs on a CPU,
using the [Cactus Compute](https://github.com/cactus-compute/cactus) runtime.

Whistle supports keyword biasing. This server fills the keyword list from your Home Assistant install, so the
names you actually say get favoured during decoding:

- the names of entities **exposed to Assist**, or their **aliases** when an entity has them (aliases are usually
  what people say, entity names are often technical);
- area names and area aliases;
- a base set of command words for the configured language (e.g. `turn`, `switch` in English; `enciende`, `apaga`
  and the to-do list verbs `agrega` / `agregá`, `poné`, `sumá` in Spanish; `schalte` in German). They're
  imperatives, not infinitives: in testing, the infinitive "apagar" pulled the spoken imperative "apaga" toward
  "apagar";
- any extra words you list in `WHISTLE_EXTRA_KEYWORDS`.

The base and extra keywords are always kept; the HA-derived ones fill the rest of `WHISTLE_MAX_KEYWORDS`.

The list is read over the HA websocket API at startup and refreshed periodically. If HA can't be reached, the server
keeps the previous list and keeps transcribing.

Whistle supports the following language codes: `en`, `de`, `fr`, `es`, `it`, `nl`, `pl`.

The server loads the appropriate keyword biasing and stopword rules matching your configured language string from script argument: `--language` or environment variable: `WHISTLE_LANGUAGE`. Defaults to `en`.

## Run

A prebuilt multi-arch image (linux/amd64 and linux/arm64, e.g. Raspberry Pi 4/5) is published at
`ghcr.io/arpena/whistle-wyoming`. Tags: `latest` (main branch), `X.Y.Z` / `X.Y` (releases).

Deploy `portainer-stack.yml`, either as a Portainer stack (set `HA_TOKEN` under the stack's environment
variables) or with Compose:

```bash
HA_TOKEN=... docker compose -f portainer-stack.yml up -d
```

The stack uses host networking and expects Home Assistant at `http://127.0.0.1:8123`; change `HA_URL` if it lives
elsewhere. On first start the engine and weights are downloaded from Hugging Face into the `whistle-cache` volume
(mounted at `/root/.cache`). Keep it: with it the server also starts when Hugging Face is unreachable.

To build the image yourself instead: `docker build -t whistle-wyoming .` and point `image:` at it.

In Home Assistant, add the **Wyoming Protocol** integration with host `127.0.0.1` and port `10300`, then pick
`whistle` as speech-to-text in your voice assistant pipeline.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `HA_URL` | `http://127.0.0.1:8123` | Home Assistant base URL |
| `HA_TOKEN` | (required) | Long-lived access token, used to read the keywords |
| `WHISTLE_LANGUAGE` | `en` | Default language if the pipeline doesn't send a supported one |
| `WHISTLE_MAX_KEYWORDS` | `200` | Cap on the keyword list (full phrases are kept first) |
| `WHISTLE_REFRESH` | `3600` | Seconds between keyword refreshes (`0` = only at startup) |
| `WHISTLE_EXTRA_KEYWORDS` | (empty) | Comma-separated words or phrases always added, e.g. `yerba, dulce de leche` |
| `WHISTLE_SAVE_AUDIO` | (empty = off) | Directory to save every request in, e.g. `/recordings` (mount a volume there) |
| `WHISTLE_SAVE_AUDIO_MAX` | `500` | Keep only the newest N recordings (`0` = no limit) |

`CACTUS_TELEMETRY=0` is set in the stack: cactus-compute sends anonymous usage counts by default.

### Saving recordings

With `WHISTLE_SAVE_AUDIO` set, each request is saved as `<UTC timestamp>.wav` (the audio exactly as Whistle got
it: 16 kHz, mono, 16-bit) plus a `.json` with the transcript, language, duration, inference time and the keyword
list used. That is enough to replay real requests against another model or other settings. It records everything
said after the wake word, so leave it off unless you need it.

## Tips

- Keep Assist exposure tight. Every exposed entity adds keywords, and technical names (`doorbell chime active`,
  `moving target`) can pull a non-English transcription the wrong way.
- Add aliases in the language you speak. Uncommon or foreign words (e.g. "freezer" in Spanish) may still be
  transcribed poorly; a more common word usually works better.
- Whistle can produce text from music or noise. Put a wake word or VAD in front of an always-on microphone.

## Implementation notes

- `cactus.transcribe()` takes a WAV path or float32 samples in [-1, 1]. Raw int16 bytes are silently misread as
  float32, so audio is converted first. Incoming audio of any rate/width/channels is converted to 16 kHz mono.
- The engine holds one global model, so all inference runs on a single worker thread off the event loop.
- Entity aliases come from `config/entity_registry/get_entries`; `config/entity_registry/list` omits them.

## License

MIT. Whistle itself (model and engine) is Apache-2.0, by Cactus Compute.
