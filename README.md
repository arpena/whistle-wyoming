# wyoming-whistle

A [Wyoming protocol](https://github.com/OHF-Voice/wyoming) speech-to-text server for Home Assistant, built on
[Cactus Whistle](https://huggingface.co/Cactus-Compute/whistle), a 16.9 MB STT model that runs on a CPU.

Whistle supports keyword biasing. This server fills the keyword list from your Home Assistant install, so the
names you actually say get favoured during decoding:

- the names of entities **exposed to Assist**, or their **aliases** when an entity has them (aliases are usually
  what people say, entity names are often technical);
- area names and area aliases;
- a base set of command words (`dime`, `enciende`, `apaga`, `qué`, `está`…). They're imperatives, not infinitives:
  infinitives biased "apaga" into "apagar" in testing.

The list is read over the HA websocket API at startup and refreshed periodically. If HA can't be reached, the server
keeps the previous list and keeps transcribing.

Languages: en, de, fr, es, it, nl, pl (Whistle's set). Default: `es`.

## Run

A prebuilt multi-arch image (linux/amd64 and linux/arm64, e.g. Raspberry Pi 4/5) is published at
`ghcr.io/arpena/whistle-wyoming`. Tags: `latest` (main branch), `X.Y.Z` / `X.Y` (releases).

Deploy `portainer-stack.yml`, either as a Portainer stack (set `HA_TOKEN` under the stack's environment
variables) or with Compose:

```bash
HA_TOKEN=... docker compose -f portainer-stack.yml up -d
```

The stack uses host networking and expects Home Assistant at `http://127.0.0.1:8123`; change `HA_URL` if it lives
elsewhere. On first start the engine and weights are downloaded from Hugging Face into the `whistle-cache` volume.

To build the image yourself instead: `docker build -t whistle-wyoming .` and point `image:` at it.

In Home Assistant, add the **Wyoming Protocol** integration with host `127.0.0.1` and port `10300`, then pick
`whistle` as speech-to-text in your voice assistant pipeline.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `HA_URL` | `http://127.0.0.1:8123` | Home Assistant base URL |
| `HA_TOKEN` | (required) | Long-lived access token, used to read the keywords |
| `WHISTLE_LANGUAGE` | `es` | Default language if the pipeline doesn't send a supported one |
| `WHISTLE_MAX_KEYWORDS` | `200` | Cap on the keyword list (full phrases are kept first) |
| `WHISTLE_REFRESH` | `3600` | Seconds between keyword refreshes (`0` = only at startup) |

`NEEDLE_TELEMETRY=0` is set in the stack: cactus-needle sends anonymous usage counts by default.

## Tips

- Keep Assist exposure tight. Every exposed entity adds keywords, and technical names (`doorbell chime active`,
  `moving target`) can pull a non-English transcription the wrong way.
- Add aliases in the language you speak. Uncommon or foreign words (e.g. "freezer" in Spanish) may still be
  transcribed poorly; a more common word usually works better.
- Whistle can produce text from music or noise. Put a wake word or VAD in front of an always-on microphone.

## Implementation notes

- `needle.transcribe()` takes a WAV path or float32 samples in [-1, 1]. Raw int16 bytes are silently misread as
  float32, so audio is converted first. Incoming audio of any rate/width/channels is converted to 16 kHz mono.
- The engine holds one global model, so all inference runs on a single worker thread off the event loop.
- Entity aliases come from `config/entity_registry/get_entries`; `config/entity_registry/list` omits them.

## License

MIT. Whistle itself (model and engine) is Apache-2.0, by Cactus Compute.
