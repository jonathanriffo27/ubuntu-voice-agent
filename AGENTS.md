# Contexto del proyecto para agentes (Atlas / voice_agent)

Asistente de voz de escritorio en Python 3.14 sobre Gemini Live API (`gemini-3.8-live`),
con wake word local ("Alexa", openWakeWord), herramientas de GUI/sistema, memoria local,
HUD web y subagente de desarrollo vía CLIProxy. Todo el código, comentarios y commits
están en **español**.

## Comandos

- Correr: `venv/bin/python jarvis.py` (requiere `GEMINI_API_KEY` en `.env`)
- Tests: `venv/bin/python -m pytest tests/ -q` (suite completa, ~524 tests)
- Un archivo: `venv/bin/python -m pytest tests/test_wake_word.py -q`
- El venv es `venv/` (Python 3.14). No hay `pip install` global: siempre `venv/bin/pip`.

## Arquitectura (mapa rápido)

```
jarvis.py  → bootstrap: config.yaml, plugins, HUD, subagente, provider
src/providers/gemini.py        → LiveConnectConfig (resumption, compresión, VAD)
src/providers/gemini_session.py → normaliza eventos Live API (AudioChunk, TextChunk,
                                 UserTextChunk, ToolCallRequest, Interrupted,
                                 TurnComplete, GoAway, ToolCallsCancelled)
src/brain/assistant.py         → receive_and_route: enruta eventos, ejecuta tools,
                                 maneja barge-in y flags de voz en el recorder
src/voice/recorder.py          → máquina de estados STANDBY / ACTIVE / FOLLOW_UP /
                                 MUTED; wake word local + VAD RMS; pre-roll
src/voice/player.py            → reproducción con jitter buffer (0.5s)
src/plugins/<nombre>/          → patrón: __init__.py con setup(registry, dependencies)
                                 + tools.py con clases BaseTool
src/agents/                    → DeveloperAgent (CLIProxy :8317, gemini-3.8-flash-high),
                                 worktrees aislados + bubblewrap sandbox + HITL
src/ui/web_overlay.py          → HUD en http://127.0.0.1:7890 (solo loopback)
src/brain/prompts.py           → SYS_PROMPT_BASE + bloque IDENTIDAD (inyecta config)
```

Flujo de voz por turno: wake word (STANDBY) → ACTIVE → streaming a Gemini
(server VAD) → eventos → player → `TurnComplete` → ventana FOLLOW_UP (7s) →
STANDBY. El cierre explícito usa la tool `entrar_en_espera` (sueño deliberado
sin ventana follow-up).

## Trampas duras (no las rompas)

Estas defensas existen porque cada una corrigió un bug real medido. Al tocar
provider/voz, repásalas antes de "simplificar":

1. **`gemini-3.8-live` ignora `audio_stream_end`** (medido empíricamente): el turno
   nunca cierra. Por eso se fuerza `server_vad=True` en modelos 3.8
   (`config/models.py: enforce_model_requirements`) aunque `config.yaml` diga lo
   contrario.
2. **NO bajes `temperature`** en Live 3.8: `0.1` produce silencio puro (aunque la
   transcripción salga bien). El anti "parametric fallback" se resuelve con tools
   declaradas `Behavior.BLOCKING` + instrucción de override en el FunctionResponse
   de `buscar_en_internet`, no con temperatura.
3. **Cada turno emite DOS cierres** (`generation_complete` + `turn_complete`, con
   delay por playback). La señal autoritativa de fin de turno es
   `interaction_status == IDLE`. El provider mapea con `strict_turn_end=True` para
   3.8 (un solo `TurnComplete` por turno); modelos legacy conservan el mapeo OR.
4. **`Behavior.BLOCKING` explícito** en todas las function declarations: en 3.8 el
   default es NON_BLOCKING y el modelo hablaba "de memoria" mientras la tool corría.
5. **No actives `Tool(google_search)` nativo en Live**: el servidor rechaza con
   `1011 quota exceeded` en este tier. La sustituta es nuestra tool `buscar_en_internet`.
6. **Session resumption**: guardar el handle; si una reconexión con handle falla,
   descartarlo y abrir sesión limpia (los handles caducan en el servidor).
7. **No envíes `proactive_audio: false`, `thinking_level` ni
   `enable_affective_dialog`** en 3.8: la API los rechaza o los retiró (ya hay
   warnings en `gemini.py`).
8. **Anti-eco y anti-autodisparo del wake word**: `player.is_speaking`/`last_speech_time`
   bloquean la detección mientras Atlas habla; además hay grace de 1.2s tras entrar a
   STANDBY (el chime "sleep" por altavoces auto-disparaba "alexa" con 0.85). Umbral
   0.62 en `config.yaml` (reales: 0.94–0.99; falsos típicos: ~0.55).
9. **El HUD SOLO escucha en loopback** (expone trayectoria/correos/aprobaciones).
10. **Seguridad**: `.env` y `latest_session.log` están en `.gitignore` — nunca
    commitearlos. El CredentialBroker redacta secretos de todo output de tools;
    `leer_archivo_proyecto` deniega `.env`/claves/binarios (defensa en profundidad).

## Convenciones

- **Commits**: conventional commits en español (`fix(voice): ...`, `feat(agent): ...`).
- **Tests de regresión**: docstring que empieza con `Bug real:` + descripción del
  síntoma observado en producción. En tests async de voz, bombea hasta predicado
  (`_pump_listen(..., until=...)`), no sleeps fijos de 50ms (flake bajo carga).
- **Tools nuevas**: registrarlas en un plugin propio (`src/plugins/<nombre>/`) con
  docstring de contexto; hablar siempre en términos de lo que el usuario pide.
- **Voz**: respuestas habladas breves por defecto; documentos/informes largos van
  a `crear_documento_onlyoffice` o se imprimen solo si el usuario lo pide.

## Punteros (lee antes de cambios grandes)

- `DEVELOPMENT_NOTES.md` — historia de decisiones y debugging de audio (51KB).
- `COMPUTER_USE_PLAN.md` — fases de automatización GUI, sandbox y worktrees.
- `ACTION_PLAN.md`, `TEST_PLAN.md` — planes y cobertura de tests.
- `config.yaml` — todos los flags de voz/modelo/HUD/subagente con comentarios.
