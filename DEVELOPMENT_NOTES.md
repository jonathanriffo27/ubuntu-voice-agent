# Notas de Desarrollo y Troubleshooting de Atlas

## 30. Pausa del Micrófono sin Mute del Sistema y Fallback en Caliente por Saturación del Backend Live (2026-09-28)

### 30.1 Pausa del micrófono: liberar captura en vez de mutear la fuente del sistema
- **Problema Detectado:**
  - Al pausar Atlas (`Tab` / `/mute` / HUD) el estado interno pasaba a `MUTED`, pero el icono de micrófono de GNOME seguía encendido: la fuente de PulseAudio continuaba activa y el stream de PyAudio seguía leyendo.
  - Primer intento (descartado): sincronizar la pausa con `pactl set-source-mute @DEFAULT_SOURCE@ 1`. El icono sí reflejaba el mute, pero afectaba a **todas** las apps del sistema y, si Atlas moría sin cleanup (SIGKILL, cierre de terminal), el micrófono quedaba muteado de forma pegajosa. Se agregó incluso un marcador en `/tmp` con el PID dueño para restaurarlo al arrancar; el enfoque completo se eliminó al adoptar la solución definitiva.
- **Causa Raíz y mediciones:**
  1. `stop_stream()` de PyAudio **no libera** el `source-output` de PulseAudio (medido: seguía apareciendo 2s después; el indicador de privacidad de GNOME no se apaga). `close()` lo elimina al instante.
  2. Un stream cerrado no se puede reabrir: hay que crear uno nuevo con `PyAudio.open(...)`. Medido: ~6ms.
  3. `asyncio.to_thread(read)` **no se puede cancelar**: al cancelar la task, el hilo sigue bloqueado en `read()`. Cerrar el stream en cleanup en paralelo al read produce **segfault de PortAudio** (reproducido en prueba real).
- **Solución Implementada:**
  1. `AudioRecorder` recibe `in_stream_factory` (inyectada por `Assistant._open_input_stream`). En `MUTED` el bucle `listen` llama a `_release_capture()` → `close()` del stream; al reanudar, `_acquire_capture()` abre uno nuevo. Sin fábrica (tests/embebidos) degrada a `stop_stream`/`start_stream`.
  2. `threading.Lock` (`_stream_lock`) serializa `_read_chunk()` con `_release_capture()`, `_acquire_capture()` y `close_capture()`: el cierre espera a que termine el read en vuelo (timeout 1s; si no, se omite y `p.terminate()` libera).
  3. El setter `is_paused` solo cambia el estado (sin I/O de stream): el cierre ocurre dentro del bucle que lee, evitando carreras entre hilos.
  4. `cleanup_async` delega el cierre de captura en `recorder.close_capture()` (el recorder puede haber reemplazado el stream).
  5. Se agregaron handlers SIGTERM/SIGHUP en `run_async` (`loop.add_signal_handler(sig, main_task.cancel)`) para que cerrar la terminal ejecute `cleanup_async` (verificado: el `finally` corre y `CancelledError` se propaga manejada en `run()`).
- **Verificación:** prueba real con PyAudio + PulseAudio: `source-outputs` 1 → 0 al pausar → 1 al reanudar → 0 en cleanup, con `Mute: no` intacto. Suite completa en verde.

### 30.2 Fallback en caliente: `1011 Internal error encountered` masivo (backend Live saturado)
- **Problema Detectado:**
  - `gemini-3.8-live` empezó a cerrar cada sesión con `APIError: 1011 None. Internal error encountered.` ~1-2s después de conectar o al enviar el primer mensaje. Atlas reconectaba en silencio y los mensajes del usuario se perdían sin respuesta (`🔄 [Reconectado]` en bucle).
  - Diagnóstico aislado (sin la app): sesión Live mínima con la misma clave → mismo 1011; REST `generateContent` con `gemini-3.8-flash` → `503 This model is currently experiencing high demand`. La clave era válida (61 modelos listados) y `gemini-3.1-flash-live-preview` respondía correctamente con las 32 tools reales (tool call + tool response + audio + TurnComplete).
- **Causa Raíz:** saturación temporal del backend de Live para la familia 3.8 (no era código, ni cuota agotada, ni la clave).
- **Solución Implementada (solo runtime, config intacta):**
  1. `ProviderConfig`: `fallback_model` (vacío = sin fallback), `fallback_after_failures` (default 2), `fallback_probe_interval` (default 300s). `config.yaml` mantiene `model: gemini-3.8-live` como primario.
  2. `jarvis.py` pasa `provider_factory(model)` al `Assistant`; el asistente mantiene `_primary_provider` / `_active_provider` y el bucle de sesión usa el activo (incluido `reset_session_handle`).
  3. `_track_provider_health()` cuenta **fallos cortos** (<15s) con señales de backend no disponible (`1011`/`503`/`high demand`/`internal error`, incluidos los `ExceptionGroup` de la TaskGroup). Racha ≥ umbral → `_activate_fallback()` (aviso `SystemNotification` y `_active_session = None` para no enviar a un websocket muriendo).
  4. `_probe_primary_loop()` sondea al primario con una sesión desechable ("ping", ≤8s). Si responde, `_return_to_primary()` cambia el proveedor activo, notifica y —si está idle— fuerza la reconexión con `GeminiSession.close()` (nuevo método) marcando `_provider_switch_requested` para que no cuente como fallo. Si está ocupado, espera hasta 5 min a que quede idle.
  5. Un `1011` tras sesión **larga** (>15s) sigue tratándose como idle-timeout normal y no gatilla el fallback.
- **Verificación:** `tests/test_provider_fallback.py` (10 tests: activación, umbral, sesión larga, ExceptionGroup, fallback no se re-activa, sonda OK/fallida, refresco volitivo, integración con `run_async`). Prueba real con primario caído: 2 fallos → fallback `gemini-3.1-flash-live-preview` activo → mensaje de texto respondido (`AssistantTextChunk: ok`). El retorno automático quedó cubierto por tests unitarios (el primario seguía caído al momento de la prueba).

### 30.3 Por qué `prompt_token_count` de Live 3.8 parece "resetearse" (26k → 9.7k)
- **Síntoma:** en una sesión real, el log mostraba prompt=26204 en un turno, 9729 en el siguiente y 20953 en el siguiente, sin reconexiones registradas. Parecía pérdida silenciosa de contexto.
- **Investigación:** sonda headless con las 32 tools reales y un dato de memoria ("el código secreto es TITAN-42"): el patrón se reprodujo exacto (13940 → 7089 → 14917 → 15167) y el modelo **recordó TITAN-42** en el cuarto turno. `cached_content_token_count` fue 0 en todas las mediciones.
- **Conclusión:** el `prompt_token_count` de Live 3.8 no es acumulativo del historial: reporta por ciclo de generación (en turnos con tool round-trip sube; en turnos simples vuelve a la base ~7-8k = system prompt + tools). NO hay pérdida de contexto. Se agregó `cached` al log de telemetría y un comentario en `gemini_session.py` para que un prompt "bajo" no se malinterprete.

## 29. Voz Muda con Texto Visible: Interrupciones Fantasma del VAD de Gemini 3.8 (2026-09-19)

- **Síntoma**: Atlas respondía (el texto de la transcripción se veía en terminal/HUD) pero **no se escuchaba nada** por los altavoces. Intermitente: frases cortas ocasionales sí sonaban.
- **Diagnóstico (instrumentación)**: se añadieron contadores de chunks PCM por turno en `receive_and_route` y de bytes escritos al hardware en `AudioPlayer`. Evidencia capturada: `98 chunks / 1.961.304 bytes` de audio recibidos del proveedor y **descartados** por un evento `Interrupted` del servidor antes de sonar. El texto sobrevivía porque llega por `output_transcription`, no por la cola de audio.
- **Causa raíz (doble)**:
  1. El `recorder` enviaba **todo** el audio ambiente al servidor en estados ACTIVE/FOLLOW_UP (TV, teclado, respiración). El VAD del servidor de gemini-3.8 (agresivo, sin banderas de sensibilidad) lo interpretaba como "usuario hablando" → enviaba `interrupted=true` → `receive_and_route` ejecutaba `player.stop_and_clear()` y vaciaba toda la cola de audio.
  2. Al procesar la interrupción se ponía `waiting_for_model=False`, reabriendo el micrófono → más ruido → más interrupciones (bucle autoalimentado; un turno acumuló ~16 regeneraciones).
- **Solución**:
  1. **Gate anti-ruido en `recorder.listen`**: en fase de búsqueda de voz (`user_spoke=False`) solo se envía al servidor audio con voz real (VAD local), con pre-roll de ~380ms (deque de 12 frames) para no cortar el onset de la frase.
  2. **Filtro de `Interrupted` en `receive_and_route`**: si no hubo voz local real en los últimos 1.5s (`recorder.last_local_voice_time`), la interrupción se ignora — no se vacía la cola — porque el barge-in por voz durante el habla de Atlas es imposible en esta arquitectura half-duplex (el mic no se transmite mientras `player.is_speaking`), así que todo `Interrupted` espurio era ruido.
- **Nota adyacente**: el canal ALSA `Headphone` estaba en `[off]` a nivel hardware (invisible para PipeWire/PulseAudio); se corrigió con `amixer -c 0 sset 'Headphone' unmute && sset 100%`. Si el tono de prueba PyAudio no se oye, mirar ahí antes de culpar al código.
- **Barge-in por teclado (mismo PR)**: `send_text_message` ahora ejecuta `player.stop_and_clear()` y registra `_last_user_text_input`. Antes, un texto nuevo mientras Atlas hablaba encolaba la respuesta nueva DETRÁS del audio viejo: el retraso percibido se acumulaba turno a turno (5s → 10s). El `Interrupted` del servidor también se honra si hubo texto reciente (≤1.5s), no solo voz local.
- **"Delay" percibido NO es latencia de arranque**: medido con los contadores — Gemini 3.8 sintetiza el habla ~3x más rápido de lo que suena (ej: 17.5s de voz generados en ~5s, el texto —sincronizado con la generación— aparece completo mucho antes de que la voz termine). La voz arranca 1-2s tras el Enter; la "cola" es la duración natural del habla. Es comportamiento inherente del streaming Live, no un bug; opción documentada (no adoptada): ritmo de texto sincronizado con la reproducción.
- **Causa raíz FINAL (la más grave, encontrada con sonda A/B contra la API real)**: `temperature=0.1` en `LiveConnectConfig` **degenera el audio nativo de gemini-3.8-live a SILENCIO PURO**: con 0.1 los turnos llegaban como 5.3s o **110.9s de PCM sin un solo frame hablado** (peak RMS 310 vs ~10.000 de voz real, transcripción correcta — "el modelo cree que habló"); con la temperatura por defecto (~1.0) el mismo prompt genera 2.4-2.5s de voz real en 4/4 pruebas. El 0.1 venía de antes, pero estaba dentro de `generation_config` (**deprecado e ignorado por el SDK**) hasta que `7242c86` (18-sep 13:35) lo movió al campo directo `temperature` — desde ese commit la voz empezó a fallar de forma intermitente: a veces bien, a veces turnos mudos, a veces >100s de silencio que "retrasaban" el audio audible ~1 minuto. **Regla permanente: NUNCA fijar `temperature` en sesiones Live** (test `test_connect_sin_temperatura_en_live` lo bloquea). La defensa anti 'parametric fallback' queda cubierta por tools BLOCKING + override del FunctionResponse.
- **Verificación final (13:01)**: saludo = `12 chunks / 127.682 bytes (~2.7s)`, 100% escrito al hardware en tiempo real. Suite: 499 tests en verde.
- **Separado**: un `1011 Resource has been exhausted` (rate limit por minuto del tier free) cerró una sesión a las 12:06; la reconexión limpia lo absorbió en ~6s. Ojo con lanzar instancias duplicadas: queman cuota de audio.

Este documento registra los problemas arquitectónicos, optimizaciones de bajo nivel y soluciones técnicas implementadas en Atlas.

---

## 21. Computer-Use Fase 0+1: Cadena de Input, Sonda DGRAM, Portal RemoteDesktop y Resolver SoM (2026-09-14)

- **Contexto**: Implementación de `COMPUTER_USE_PLAN.md`. Ver detalle completo ahí.
- **Síntoma 1 (falso positivo)**: El health-check marcaba ydotool como operativo porque `/run/user/<uid>/.ydotool_socket` existía en disco, pero el daemon llevaba muerto desde hacía días (socket obsoleto).
- **Causa Raíz 1**: El socket de `ydotoold` es **DGRAM (u_dgr)**, no STREAM: no se puede sondear "conectando" (un connect a DGRAM no valida que haya daemon). 
- **Solución**: El check correcto es **archivo de socket presente + proceso `ydotoold` vivo en `/proc`** (`src/input/backends.py: process_alive`). El auto-fix (`systemctl --user start ydotoold.service`) vive en `src/input/health.py` y corre en el arranque de `jarvis.py`, con sondeo reintentado hasta 1.5s tras el arranque (el daemon tarda unos ms en bind-ear).
- **Síntoma 2 (captura negra)**: La captura por portal XDG fallaba leyendo el PNG antes de que GNOME lo materializara (solo 0.9s de ventana de reintento) y caía silenciosamente al fallback `mss`, que en Wayland devuelve **pantalla negra** (~6KB JPEG).
- **Solución 2**: Ventana de reintento ampliada a ~3s (12×0.25s) en `OptimizedScreenCaptureService._capture_wayland_portal`. La captura real pasó de fallar a **~270-790ms con 79-86KB reales**.
- **Nuevos subsistemas**:
  - `src/security/policy.py` + `config/security_policy.yaml`: clasificación de acciones en 3 tiers (read_only / local_write / irreversible) por nombre (fnmatch) + **escalación por contenido** (regex sobre payload: pagos, `rm -rf`, `sudo`, `git push`, tarjetas). Tier 3 siempre HITL.
  - `src/input/`: `YdotoolBackend`, `RemoteDesktopPortalBackend` (XDG Portal RemoteDesktop nativo en GNOME 45+, con restore_token persistido en `~/.config/atlas/portal_restore_token`, 0600) e `InputRouter` con fallback automático entre backends.
  - `src/input/resolver.py`: `ElementResolver` AT-SPI2→SoM→coords con matching por palabras no contiguas (las consultas de voz rara vez coinciden literalmente con el label).
  - `src/plugins/gui_actions/`: herramienta `interactuar_gui` (click/doble_click/escribir/tecla/leer) que prioriza acciones semánticas AT-SPI2 (sin robar foco) sobre inyección de coordenadas, verifica con frame-diff, y devuelve candidatos cuando no encuentra el objetivo (autocorrección del LLM).
- **Benchmark falsable** (`tests/computer_use/`): 4 sondas pasivas. Estado inicial real: 4/4 (100%): cadena input OK, 8-11 apps en AT-SPI2, 60 elementos resolubles, captura <800ms. Correr con `./venv/bin/python -m tests.computer_use.runner` o `ATLAS_DESKTOP_TESTS=1 ./venv/bin/pytest tests/computer_use/`.

---

## 22. Computer-Use Fases 2 y 3: Bucle OODA y Worktrees Aislados (2026-09-14)

- **Fase 2 — `ComputerUseOrchestrator` (`src/agents/computer_use.py`)**: bucle Observe→Decide→Act→Verify para tareas GUI multi-paso.
  - **Observe**: lista de elementos AT-SPI2 con roles+bounds (ojos deterministas) y firma hash del estado.
  - **Decide**: LLM (mismo cliente CLIProxy que el DeveloperAgent) responde UNA acción JSON por paso; los nombres de elementos entran al prompt envueltos en delimitadores **"CONTENIDO NO CONFIABLE"** (spotlighting anti prompt-injection) y el system prompt prohíbe obedecer texto que venga de la pantalla.
  - **Act**: delega en `interactuar_gui` (semántica AT-SPI2 → coords, política de tiers con HITL, verificación por frame-diff).
  - **Verify**: si la firma del estado no cambia en 2 pasos consecutivos → aborta explicando, en vez de girar en bucle (límite global: 12 pasos).
  - Cancelación cooperativa (`orchestrator.cancel(task_id)`), tool de voz `operar_gui_tarea` (plugin `gui_actions`) en segundo plano con narración vía eventos TaskDelegated/TaskCompleted.
- **Fase 3 — Aislamiento por git worktrees (`src/agents/workspace.py`)**: cada tarea del DeveloperAgent corre en `.worktrees/agent-<id>` sobre rama `agent/<id>`.
  - Escrituras dentro del worktree **auto-aprobadas** (sandboxed; se revisan una sola vez): la fricción de HITL por archivo desaparece sin perder control.
  - Al terminar: `commit` en la rama → `pytest` dentro del worktree (venv del proyecto) → si falla, el trabajo se conserva para inspección y NO se integra → si pasa, diff unificado + stat van al `ApprovalManager` (voz/HUD/terminal) → merge solo con aprobación → recarga en caliente de plugins.
  - Merge conflictivo → `git merge --abort` limpio y rama conservada (probado en tests con git real).
  - Bug de seguridad corregido de camino: `escribir_archivo` aceptaba rutas absolutas (`lstrip('/')` las hacía relativas antes del path jail); ahora se rechazan explícitamente.
- **Suite total tras Fases 0-3: 300 tests pasando.**

---

## 23. Computer-Use Fase 4: Navegador Real del Usuario vía CDP (2026-09-15)

- **Contexto**: `COMPUTER_USE_PLAN.md` Fase 4. El plan pedía "adjuntarse al Chrome/Brave real donde ya viven las sesiones". La investigación (verificada contra el blog oficial de Chrome Developers) reveló una restricción que cambia el diseño: **desde Chromium 136, `--remote-debugging-port` es ignorado por completo si se usa el `--user-data-dir` por defecto** (mitigación contra infostealers que extraían cookies vía CDP). No hay bypass oficial.
- **Diseño adoptado**: Atlas lanza/detecta una instancia dedicada de Brave/Chromium con **perfil propio persistente** en `~/.local/share/atlas/browser-profile`. El usuario inicia sesión ahí una vez; las cookies sobreviven entre reinicios de Atlas. Ventaja colateral: jamás se toca ni se arriesga el perfil personal del usuario.
- **`src/cdp/` (sin Playwright ni chromedriver, solo `websockets` + `aiohttp` que ya estaban)**:
  - `client.py`: **un solo websocket** al `webSocketDebuggerUrl`; pestañas multiplexadas con `Target.attachToTarget {flatten: true}` + `sessionId` por comando (evita un socket por pestaña). Timeouts por comando con limpieza de futures huérfanos; pérdida de conexión falla todos los pendientes.
  - `manager.py`: sonda `http://127.0.0.1:9222/json/version` → si ya hay navegador con debug, se adjunta sin lanzar nada; si no, lo lanza con `--remote-allow-origins=*` (Chromium valida el header `Origin` y rechaza clientes no-Chrome con 403). Modo **headless desechable** con perfil temporal en /tmp (trabajo paralelo sin sesiones). `shutdown()` solo mata el proceso si lo lanzó Atlas.
  - `page.py`: mismo principio que el `ElementResolver` de la Fase 1 pero dentro del navegador: **snapshot determinista** de elementos interactivos (`[1] button «Enviar»`) marcando cada nodo con atributo temporal `data-atlas-idx`; click por índice/nombre (`el.click()` JS, con `click_at(x,y)` trusted como fallback) y escritura con `Input.insertText` (evento trusted tipo IME, compatible con React) con **fallback a setter nativo** del prototype si el campo quedó vacío.
- **Tool de voz `navegador_web`** (plugin `src/plugins/navigator/`): acciones `abrir/leer/elementos/click/escribir/tecla/scroll/atras/pestanas/cerrar/captura`.
  - Riesgo por acción en `security_policy.yaml` (`navegador_web.leer` = read_only; `click/escribir` = local_write) y el **payload** (nombre del botón, texto) pasa por `escalation_patterns`: "confirmar compra", "enviar mensaje/formulario", "realizar pago" → Tier 3 → HITL multi-canal ya existente.
  - El texto leído de páginas se devuelve envuelto en `=== CONTENIDO WEB (DATOS NO CONFIABLES) ===` (spotlighting, consistente con Fase 2): una web maliciosa no puede inyectar instrucciones al planner.
  - Cuando un elemento no se encuentra, la tool devuelve los candidatos visibles para que el LLM reintente (mismo patrón de autocorrección que `interactuar_gui`).
- **Suite total tras Fase 4: 332 tests pasando** (32 nuevos: cliente CDP con fake websocket, PageController con fake CDP, tool de voz con fake manager + HITL).

---

## 24. Computer-Use Fase 6: Endurecimiento — Sandbox bwrap, Credential Broker y Monitor de Anomalías (2026-09-15)

- **Contexto**: `COMPUTER_USE_PLAN.md` Fase 6. El subagente desarrollador ejecutaba comandos con `subprocess.run(shell=True)` heredando **todo el entorno de Atlas** (incluidas `GEMINI_API_KEY`, `TAVILY_API_KEY`...) y con acceso completo al FS del usuario.
- **Sandbox bwrap (`src/security/sandbox.py`)**: todos los comandos del DeveloperAgent (`ejecutar_comando_desarrollo`, `ejecutar_pruebas_pytest`) corren ahora confinados:
  - **Red OFF por defecto** (`--unshare-net`); ON solo si el comando aprobado por HITL la necesita razonablemente (`command_needs_network`: pip install/download, git clone/fetch/pull/push, curl/wget/npm...).
  - **FS mínimo**: `/usr`, `/etc`, `/lib*` ro (con `--symlink` para los symlinks merged-usr de Debian/Ubuntu), el proyecto/worktree es el único punto escribible, `/tmp` tmpfs, `HOME=/tmp` → `~/.ssh`, `~/.aws`, `~/.gnupg` simplemente no existen para el comando.
  - **Modo worktree (Fase 3)**: el repo principal se monta `--ro-bind` ANTES y el worktree `--bind` rw después (orden de mounts crucial) para que el venv compartido siga accesible.
  - `--die-with-parent --new-session --unshare-pid`. El comando viaja como argumento único de `sh -c` (sin shell intermedio del lado del padre).
  - **Bug encontrado por tests**: bwrap **propaga el entorno del padre** al hijo a menos que se use `--clearenv`; el primer diseño (pasar env limpio a `subprocess.run`) era insuficiente. Ahora `--clearenv` + `--setenv` solo con las variables limpias del broker.
  - Degradado sin bwrap instalado: ejecución directa pero SIEMPRE con env limpio + warning en log.
- **Credential broker (`src/security/credentials.py`)**: singleton `get_broker()` que custodia los secretos en memoria. (a) Las herramientas los piden por nombre (`broker.get`) sin exponerlos al LLM; (b) `scrub_env()` genera entornos de subproceso solo con allowlist funcional (PATH, LANG, DISPLAY...) y rechaza nombres secretos aunque se pidan explícitamente; (c) `redact()` sustituye cualquier valor secreto que aparezca en salidas antes de devolverlas al contexto del LLM (aplicado en el dispatch del Assistant y en las salidas del DeveloperAgent). Valores <8 chars no se redactan (ruido de falsos positivos).
- **Monitor de anomalías (`src/security/monitor.py`)**: ventana deslizante de 60s sobre la secuencia de acciones del dispatch central de voz con 3 reglas: ráfaga total (≥10 acciones/60s), ráfaga de riesgo (≥5 Tier≥2/60s) y **repetición** (misma acción+payload 3 veces seguidas → anti-bucle / anti prompt-injection repetitivo). Al disparar: la acción en curso se **pausa** y pide HITL único ("anomaly_pause"); aprobación → reset; rechazo/timeout → bloqueo del turno. Inyectado en `Assistant` como dependencia opcional (tests lo sobreescriben).
- **Verificación en vivo**: curl bloqueado sin red, `cat ~/.ssh/id_rsa` no ve nada, `env` muestra 0 secretos, escritura OK solo dentro del proyecto.
- **Suite total tras Fase 6: 386 tests pasando** (52 nuevos: argv/sandbox real, broker, monitor).

---

## 25. Computer-Use Fase 7: Benchmark con Tiers y Registro Histórico (2026-09-16)

- **Contexto**: `COMPUTER_USE_PLAN.md` Fase 7 ("medición continua"). El benchmark de Fase 0 eran 4 sondas pasivas; sin medición activa la regla de oro ("si una mejora no sube el número, no entra") era letra muerta.
- **Ampliación a 3 tiers** (`tests/computer_use/`):
  - **Escritorio** (4 sondas originales, pasivas): solo con `ATLAS_DESKTOP_TESTS=1`.
  - **Local** (nuevas): sandbox bwrap funcional (HOME aislado, red off) + política de tiers clasificando correctamente. Corren siempre en pytest.
  - **Navegador** (nueva, *activa pero confinada*): `navegador_flujo` corre el ciclo CDP real de la Fase 4 (abrir página → snapshot → click con cambio de firma → escribir y verificar valor → leer contenido) contra una instancia headless en puerto 9223 con perfil desechable en /tmp. Mide la cadena completa sin tocar la sesión del usuario. Corre en pytest si hay binario de navegador.
- **Bug real detectado por el benchmark**: las `data:` URLs se truncan al primer `#` (inicio de fragmento) — la página de prueba con `<a href='#'>` quedaba cortada y solo se veían 2 elementos. Solución: la página de prueba se codifica en base64. Es exactamente la clase de bug que solo sale midiendo.
- **Registro histórico**: cada corrida manual anexa a `tests/computer_use/history.jsonl` (git-ignored, es dato local) con ts, commit, %, segundos y sondas fallidas; el runner muestra **Δ contra la corrida anterior**. La tabla de hitos se consolida en `COMPUTER_USE_PLAN.md` §Fase 7.
- **Baseline: 100% (7/7)**, flujo navegador E2E en ~1.35s.
- **Suite total: 388 tests** (2 nuevos wrappers pytest del benchmark).

---

## 26. Latencia de Búsquedas: Circuito Rápido ante Timeout/Red en Google Grounding (2026-09-16)

- **Síntoma** (sesión real del usuario): las búsquedas por voz tardaban varios segundos aunque Tavily respondía bien.
- **Causa Raíz**: `GoogleGroundingSearchEngine` intentaba siempre Google primero y un **timeout no bloqueaba el modelo** (diseño anterior: "lentitud transitoria"). Con Google lento/caído, cada búsqueda pagaba `timeout(5s) × Nº modelos` — hasta ~15s — antes de caer a Tavily. Solo el 429 (cuota) tenía cooldown.
- **Solución** (`src/plugins/browser/engines/google_grounding.py`):
  - Timeout → cooldown de **4 min** para ese modelo y **corte de la rotación** (un timeout caído afecta a todos los modelos igual: rotar solo multiplica la espera).
  - Errores de red (ConnectionError/OSError/`Connect`/`Socket`/`Timeout` en el nombre) → mismo tratamiento.
  - 429 mantiene su cooldown de 30 min **con** rotación (cada modelo tiene cuota propia).
  - El estado bloqueado persiste en disco como antes (incluye ahora los bloqueos por timeout).
- **Efecto**: tras un fallo de red, las búsquedas siguientes saltan Google en ~0ms durante 4 min → respuesta por Tavily/DDG en <2s. Tras el cooldown, un solo sondeo reintenta Google (auto-recuperación barata).
- Suite: 391 tests.

---

## 27. Freshness de Respuestas de Búsqueda: Anclaje Temporal y Detección de Obsolescencia (2026-09-16)

- **Síntoma** (reporte del usuario): "quién ganó el último mundial" en sept 2026 devolvió "Argentina ganó en 2022" — respuesta ambientada pero **obsoleta** (el último mundial era el 2026).
- **Causa Raíz**: la query salía sin ancla temporal (`ganador último mundial de futbol`) y ninguna capa validaba que el año de la respuesta encajara con "último". El motor respondía con datos viejos presentados como hecho actual.
- **Solución** (3 capas en `MultiEngineSearchManager`, `src/plugins/browser/tools.py`):
  1. **Anclaje temporal**: regex de marcadores relativos (último/hoy/reciente/actual/noticias/quién ganó/...). Si la query es temporal y no trae año explícito, se ancla: `"... (hoy es 16 de septiembre de 2026)"`. (Meses hardcoded en español, sin depender del locale.)
  2. **Doble llave**: la descripción de la tool `buscar_en_internet` ahora instruye al LLM de voz a incluir el año en consultas sensibles (defensa en profundidad: si el LLM ancla bien, el manager no interviene).
  3. **Detección de obsolescencia + reintento**: si la respuesta ganadora es temporal y solo menciona años < año actual → un reintento con `"{query} {año} últimas noticias"` vía Tavily (barato); si mejora, se usa la nueva (trail `TAVILY🔁✅ (frescura)`); si no, el answer se prefija con un **⚠️ AVISO DE ACTUALIZACIÓN** para que el LLM de voz lo transmita con cautela en vez de afirmarlo.
- **Verificación en vivo**: la misma pregunta que falló ahora devuelve "Argentina won the 2026 FIFA World Cup" — el anclaje solo ya corrigió el caso real.
- Suite: 412 tests (21 nuevos de freshness).

---

## 28. Alucinación de Tavily `answer`: Tavily en modo raw + Exa como fallback semántico (2026-09-16)

- **Síntoma** (reporte del usuario): tras el fix §27, la búsqueda ya devolvía el año correcto (2026) pero el ganador era **incorrecto**: "Argentina won the 2026 FIFA World Cup". Verificado con Google Grounding/Wikipedia/ABC News: la verdad era **España 1-0 Argentina** (19-jul-2026, MetLife, gol de Ferran Torres).
- **Causa Raíz**: el campo `answer` de la API de Tavily **es síntesis generada por un LLM interno de Tavily**, no un extracto de fuentes (documentación propia de Tavily). Tavily alucinó ganador Y fecha. Sus snippets crudos (`results[].content`), en cambio, traían el dato correcto (Wikipedia/ESPN).
- **Investigación externa (sept 2026)**: Bing Search API fue **retirada** (ago 2025); Brave exige tarjeta en el free tier; Google Custom Search cerrado a nuevos registros y muere enero 2027; Jina `s.jina.ai` devuelve 401 sin key; **Exa** ofrece ~1400 búsquedas/mes gratis sin tarjeta y devuelve texto crudo parseado.
- **Cambios** en `src/plugins/browser/`:
  - **Tavily → modo raw**: `include_answer=False`. Atlas deja de consumir la síntesis alucinable; solo se usan snippets de fuentes reales; la síntesis para voz la hace el LLM principal de Atlas (Gemini Live) a partir de esos snippets.
  - **Nuevo motor `ExaSearchEngine`** (`engines/exa.py`): activo solo si existe `EXA_API_KEY` (tier gratuito sin tarjeta). Devuelve texto crudo (`contents.text`), nunca síntesis.
  - **Cadena de fallback ampliada**: Google Grounding → Tavily(raw) → **Exa** → DuckDuckGo, con los 3 fallbacks en paralelo y elección por prioridad.
  - **Freshness sobre snippets**: la verificación de obsolescencia de §27 ahora evalúa `answer` O el blob de los 3 snippets superiores (en modo raw no hay `answer`).
- **Regla arquitectónica adoptada**: **nunca confiar respuestas pre-sintetizadas de APIs de fallback**; solo fuentes crudas, síntesis local.
- **Verificación en vivo**: la pregunta original devuelve ahora "La ganadora del encuentro fue España... vencer 1-0 en tiempo extra a Argentina" (Wikipedia cruda). Trail completo visible: `GOOGLE ❌(timeout) → TAVILY ✅ → EXA ❌ → DDG ❌`.
- Suite: 417 tests.

---

---

## 1. Supresión de Errores C de ALSA y PortAudio (Linux PCM Underruns)
- **Síntoma:** Aparecían mensajes repetitivos en la consola como `ALSA lib pcm.c:8787:(snd_pcm_recover) [error.pcm] underrun occurred` y `Expression 'res' failed in 'src/hostapi/alsa/pa_linux_alsa.c'`.
- **Causa Raíz:** Las librerías de bajo nivel en C (`libasound.so.2` y PortAudio) escriben advertencias directamente a `stderr` de C en lugar de usar el logging de Python.
- **Solución:** 
  1. Se implementó `src/voice/alsa_mute.py` usando `ctypes` para invocar `snd_lib_error_set_handler` con un callback nulo al inicio del proceso.
  2. En `src/brain/assistant.py` (`cleanup_async()`), se redirigió temporalmente el file descriptor de `stderr` (`os.dup2(devnull, 2)`) durante el cierre de streams de PyAudio.

---

## 2. Control Multimedia sin Atajos Simulados (Spotify MPRIS D-Bus)
- **Síntoma:** Al pedir reproducir música, el asistente intentaba simular la tecla `Enter` con `ydotool` y realizaba búsquedas web de atajos de teclado sin reproducir sonido.
- **Causa Raíz:** Falta de un canal de control directo para el reproductor en Linux.
- **Solución:** Se implementó el plugin `src/plugins/media/` utilizando el protocolo estándar **MPRIS D-Bus** de Linux a través de `dbus-send` y `busctl`.
  - Envío de URIs nativas: `spotify:search:<busqueda>`
  - Control de estados: `PlayPause`, `Play`, `Pause`, `Next`, `Previous`, `Stop`.
  - Extracción de metadata: Consulta `Metadata` para obtener artista, título de canción y álbum.

---

## 3. Arquitectura Multi-Agente Híbrida (Live 3.1 + Reasoning 3.7 Flash)
- **Desafío:** `Gemini 3.1 Flash Live` es óptimo para conversación en milisegundos (<250ms), pero no tiene capacidad de razonamiento profundo o *thinking mode* para escribir código o crear plugins extensos.
- **Solución:** Se desacopló la arquitectura:
  - **Frontend de Voz**: `Gemini 3.1 Flash Live` atiende la voz en tiempo real.
  - **Subagente Desarrollador Asíncrono**: `DeveloperAgent` corre en una tarea `asyncio.Task` en segundo plano consumiendo `gemini-3.7-flash-high` a través de `CLIProxyAPI` en el servidor Oracle (`http://127.0.0.1:8317/v1`).
  - No bloquea el audio ni el micrófono durante la generación de código.

---

## 4. Compuerta de Aprobación Humana (HITL) Multi-Canal
- **Desafío:** Permitir que el subagente desarrolle y ejecute código en el sistema local sin riesgos de seguridad ni modificaciones no autorizadas.
- **Solución:** `ApprovalManager` gestiona solicitudes con UUIDs cortos y un timeout de 60s.
  - **Sincronización multi-canal**: Se notifica simultáneamente por voz (Atlas pregunta y reconoce *"Apruebo"*), por el Web HUD (banner interactivo con botones) y por la terminal (`[Enter]` en línea vacía para aprobar).

---

## 5. Recarga en Caliente de Plugins (*Hot-Reloading*)
- **Desafío:** Evitar tener que reiniciar Atlas cuando el subagente termina de crear un nuevo plugin o herramienta.
- **Solución:** `reload_plugins()` en `src/plugins/loader.py`:
  1. Invalida cachés de `importlib`.
  2. Limpia el `ToolRegistry`.
  3. Recarga los módulos en `sys.modules` bajo `src.plugins.*`.
  4. Vuelve a ejecutar `setup(registry, dependencies)`.
  5. Las nuevas herramientas quedan activas inmediatamente en memoria.

---

## 6. Jerarquía Multi-Motor de Búsqueda y Trazabilidad en Deep Research
- **Desafío:** Evitar fallos de búsqueda cuando una API externa (Google Grounding o Tavily) agota su cuota o tiene latencia alta.
- **Solución:** `MultiEngineSearchManager` implementa una jerarquía en cascada:
  1. `GoogleGroundingSearchEngine` (primario, timeout 2.5s).
  2. `TavilySearchEngine` (segundo fallback con redacción de secretos).
  3. `DuckDuckGoSearchEngine` (tercer fallback gratuito, 100% disponible).
  - Trazabilidad visual devuelta en tupla `(SearchResponse, trail_badge)`.
  - Desempaquetamiento seguro en `DeepResearchEngine` para soportar tuplas u objetos de respuesta.

---

## 7. Gestión de Terminal cbreak, Auto-Wrap y Atajos Unificados
- **Desafío:** Colisión de múltiples hilos leyendo `sys.stdin` (hotkeys vs prompt de texto) y duplicación de texto en pantalla al escribir líneas largas (>80 cols).
- **Causa Raíz:** El redibujo con `\r` solo retrocede al inicio de la fila actual de la terminal, rompiendo el auto-wrap de múltiples renglones.
- **Solución:** `TerminalInteractionManager` toma control exclusivo de `sys.stdin`:
  - Emite caracteres directamente al escribir al final para que la terminal realice auto-wrap nativo sin duplicación.
  - Decodifica secuencias ANSI de flechas (`←`, `→`), `Inicio`, `Fin`, `Supr` y `Backspace`.
  - Mantiene un historial circular de prompts (`↑` / `↓`).
  - Habilita `modifyOtherKeys` (`\033[>4;2m`) para `Shift+Espacio` y asigna `Tab`, `Ctrl+Espacio` y `F2` como atajos universales de Mute.

---

## 8. Supresión de Desconexiones WebSocket 1008 y Directiva de Inicio Limpia
- **Desafío:** Desconexiones por inactividad de Google WebSocket (`1008 None. The operation was aborted`) arrojaban errores visuales ruidosos, y frases de inicio como `"Iniciando sistema."` provocaban alucinaciones y búsquedas no deseadas.
- **Solución:**
  1. Se filtraron los errores transitorios `1008` en `src/brain/assistant.py` para permitir que el mecanismo de reconexión con backoff actúe de forma silenciosa.
  2. Se reemplazó el texto de arranque por un sonido de inicio (`service-login.oga`) y una directiva explícita de saludo corto con prohibición de ejecutar herramientas al inicio.

---

## 9. Filosofía de Desarrollo App-First y Cero-Fricción (Nativo en Linux)
- **Desafío:** Evitar que los subagentes desarrolladores reinventen la rueda solicitando APIs externas complejas, tokens de desarrollador (ej. Telethon con API ID/Hash, Spotify Web API con client_secrets) o configuraciones pesadas para tareas cotidianas.
- **Solución:**
  1. Se incorporó en el prompt del sistema del subagente y de Atlas la **Regla App-First**:
     - **Comandos CLI nativos**: `playerctl`, `pamixer`, `pactl`, `nmcli`, `bluetoothctl`, `brightnessctl`, etc.
     - **D-Bus del sistema/apps**: `org.mpris.MediaPlayer2`, `org.freedesktop.Notifications`.
     - **Automatización de escritorio en Wayland**: Enfoque garantizado mediante tecla `Super` (`125`), atajos con `ydotool`, portapapeles con `wl-copy` y URLs registradas (`xdg-open`).
  2. Red de seguridad automática (`_rewrite_to_venv`): Cualquier comando de terminal (`pip`, `python`) invocado por subagentes o shell se redirige automáticamente al `./venv/bin/` del proyecto, previniendo fallos PEP 668 (`externally-managed-environment`).

---

## 10. Detección Reactiva de Estado de Aplicaciones vía AT-SPI2 y Visión (Wayland/GNOME)
- **Desafío:** Pausas ciegas (`sleep()`) o comprobaciones superficiales de ventanas provocaban que Atlas reportara tareas como completadas mientras WhatsApp Web PWA o Brave aún se encontraban en el spinner de carga ("Cargando tus chats...", "Descargando mensajes...").
- **Causa Raíz:** Brave/Chromium registra la ventana top-level en AT-SPI2 inmediatamente, pero el árbol DOM accesible interno del SPA web tarda varios segundos en renderizar los elementos interactivos reales (caja de búsqueda, lista de chats, entrada de mensajes).
- **Solución:**
  1. **Sensor de Accesibilidad AT-SPI2 Profundo (`src/utils/a11y.py`)**:
     - Conexión vía API GObject `Atspi` nativa en memoria (con fallback a D-Bus `/run/user/<uid>/at-spi/bus`).
     - **Máquina de estados de WhatsApp (`get_whatsapp_state`)**:
       * `READY`: Caja de búsqueda (`entry` `"Buscar un chat..."`), tabla de chats o campo de mensaje interactivos y sin banners de carga.
       * `LOADING`: Detección explícita de spinners/mensajes (*"Cargando tus chats"*, *"Descargando mensajes"*, *"Conectando"*).
       * `QR_REQUIRED`: Detección de pantalla de inicio de sesión desvinculada (*"Para usar WhatsApp en tu computadora"*).
       * `BLANK` / `NOT_FOUND`: Ventana ausente o webview aún no montada.
     - **Espera Reactiva (`wait_for_whatsapp_ready`)**: Polling en memoria (<1ms) hasta alcanzar estado interactivo real.
     - **Verificación de Conversación y Envío**: Espera reactiva de apertura de chat (`wait_for_chat_open`) y confirmación de vaciado de composer (`verify_message_sent`).
  2. **Servicio de Visión Híbrido y Optimizado (`src/vision/service.py`)**:
     - Captura en Wayland vía **XDG Desktop Portal D-Bus** en 0.01s.
     - Compresión automática bilineal a max 1280px y JPEG calidad 70 (reducción de ~2MB a ~60KB, >95% de ahorro en tokens/ancho de banda).

---

## 11. Envío de Correo Electrónico sin Servidores SMTP (Gmail PWA en Linux)
- **Desafío:** Comandos shell tradicionales de envío de correo (`mail -s`, `sendmail`, `ssmtp`) no están instalados por defecto en distribuciones de escritorio modernas y fallan con `mail: not found`. Configurar credenciales SMTP o contraseñas de aplicación de Google genera alta fricción para el usuario.
- **Solución:** Plugin `src/plugins/email/` con `EnviarCorreoTool`:
  1. Utiliza la sesión activa de la PWA de Gmail (`brave-mail.google.com__mail_-Default.desktop`).
  2. Genera URLs de redacción prellenadas (`https://mail.google.com/mail/?view=cm&fs=1&to=...&su=...&body=...`), soportando texto multilínea, acentos y emojis sin depender del estado de atajos de teclado de la web.
  3. Lanza/enfoca la ventana PWA con el perfil de usuario dedicado (`--user-data-dir=...`).
  4. Sincroniza mediante AT-SPI2 (`wait_for_gmail_ready`) y emite `Ctrl + Enter` vía `ydotool` para el envío instantáneo, confirmando el cierre del diálogo (`verify_email_sent`).

---

## 12. Activación Dinámica del Árbol DOM Accesible en Chromium/Brave (AT-SPI2 ScreenReaderEnabled)
- **Desafío:** Brave/Chromium registra la ventana top-level de las PWAs (ej: `web.whatsapp.com`) en el bus AT-SPI2 inmediatamente, pero **no expone el árbol DOM web** (campos de texto, listas, botones del SPA) a menos que la accesibilidad del renderer esté explícitamente habilitada. El flag `--force-renderer-accessibility` solo funciona si Brave **aún no está corriendo**; si ya hay una sesión activa, el flag se ignora porque el proceso simplemente delega al existente.
- **Causa Raíz:** Chromium comprueba la propiedad D-Bus `org.a11y.Status.ScreenReaderEnabled` al iniciar para decidir si activa la accesibilidad en los renderers. Si es `false` (default en distribuciones de escritorio sin lector de pantalla), los hijos accesibles de la ventana apuntan a `/org/a11y/atspi/null`.
- **Solución (`_ensure_a11y_enabled` en `src/plugins/whatsapp/tools.py`)**:
  1. Antes de lanzar/enfocar la PWA, se activa temporalmente `ScreenReaderEnabled = true` vía `gdbus call --session` en el bus de sesión D-Bus.
  2. Chromium detecta el cambio en runtime, activa la accesibilidad en todos los renderers y puebla el árbol AT-SPI2 con los elementos web reales.
  3. Tras 2 segundos, se desactiva `ScreenReaderEnabled = false` para evitar efectos secundarios (voz de Orca, etc.).
  4. Se mata el proceso `orca` si el sistema lo lanzó automáticamente al activar el lector de pantalla.
  5. **Importante:** Una vez que Chromium activa la accesibilidad en un renderer, la mantiene activa incluso tras desactivar `ScreenReaderEnabled`. Solo necesita activarse una vez por sesión de Brave.
- **Mejoras adicionales:**
  - `get_whatsapp_window()` ahora selecciona la ventana con el árbol AT-SPI2 más profundo (`_probe_tree_depth`) cuando hay múltiples ventanas de WhatsApp.
  - `wait_for_whatsapp_ready()` tiene fallback temporal: si la ventana existe pero el árbol permanece vacío tras el timeout, permite continuar en vez de bloquear.

---

## 13. Visualización en Tiempo Real de Respuestas en la Terminal (Gemini Live Audio Transcription)
- **Desafío:** Cuando Atlas respondía por voz mediante la Live API de Gemini, la consola de la terminal permanecía muda visualmente (no mostraba texto de las respuestas de Atlas), a pesar de que el audio se escuchaba por los parlantes.
- **Causa Raíz:** Con `response_modalities=[types.Modality.AUDIO]`, Gemini Live API entrega únicamente paquetes de bytes PCM en `model_turn.parts[].inline_data`, dejando `model_turn.parts[].text` vacío. La transcripción textual del audio sintetizado debe solicitarse explícitamente y se recibe en `server_content.output_transcription.text`.
- **Solución:**
  1. **Configuración de Transcripción Bidireccional (`src/providers/gemini.py`)**:
     - Se añadió `output_audio_transcription=types.AudioTranscriptionConfig()` y `input_audio_transcription=types.AudioTranscriptionConfig()` a `types.LiveConnectConfig`.
  2. **Normalización en Streaming (`src/providers/gemini_session.py`)**:
     - Extracción de `sc.output_transcription.text` y emisión progresiva de `TextChunk(text=...)`.
  3. **Eventos de Fin de Turno (`src/events/base.py` y `src/brain/assistant.py`)**:
     - Emisión de `TurnCompleted` al finalizar cada turno de respuesta.
  4. **Renderizador de Terminal Fluido (`src/ui/cli.py`)**:
     - Formato `│ 🤖 Atlas: <texto>` con streaming continuo sin saltos de línea prematuros y flush al terminar el turno (`TurnCompleted`).

---

## 14. Reconexión Silenciosa, Fluida y Transparente (Manejo de Timeout 1008 de WebSocket)
- **Desafío:** Los servidores de Google Gemini Live API cierran las conexiones WebSocket inactivas tras ~3 minutos con código `1008 None. The operation was aborted`. Atlas interpretaba esta reconexión como un nuevo inicio completo de sesión, repitiendo el banner gigante de bienvenida, el sonido de campana y la directiva de saludo por voz ("Sistema Atlas en línea y listo"), interrumpiendo al usuario y reiniciando el hilo de la terminal.
- **Causa Raíz:** No existía discriminación entre el arranque inicial en frío (`is_first_connection`) y las reconexiones automáticas subsiguientes dentro del bucle de reintento. Además, el gestor de interacción de terminal (`terminal_manager.listen()`) se instanciaba dentro del `TaskGroup` de la conexión efímera.
- **Solución:**
  1. **Diferenciación de Ciclo de Vida (`src/brain/assistant.py`)**:
     - `SessionStarted` + sonido `ready` + saludo inicial solo se ejecutan una vez en el primer arranque (`is_first_connection == True`).
     - Al reconectar tras un corte o inactividad, se emite `SessionReconnected` y se omite todo audio o mensaje introductorio.
  2. **Persistencia del Gestor de Terminal**:
     - `TerminalInteractionManager` se ejecuta en una tarea continua exterior a las reconexiones, preservando lo que el usuario esté escribiendo en el teclado sin reinicios.
  3. **Manejo Suave de Código 1008**:
     - Los cierres normales por inactividad se registran limpiamente como `[INFO]` y reconectan de inmediato (0.5s) sin generar trazas alarmantes de `[ERROR]`.
  4. **UI Compacta (`src/ui/cli.py`)**:
     - `SessionReconnected` muestra un aviso sutil de una sola línea:
       `│ 🔄 [Reconectado] Conexión con Gemini restablecida.`

---

## 15. Sincronización y Espera Reactiva en Telegram Desktop (Qt / AT-SPI2)
- **Desafío:** Al pedir el envío de un mensaje por Telegram con la aplicación cerrada o minimizada a la bandeja, Atlas reportaba la tarea como completada inmediatamente mientras la ventana de Telegram aún estaba en proceso de inicialización, perdiendo las pulsaciones de búsqueda (`Ctrl+K` / `Ctrl+F`) y el pegado del mensaje.
- **Causa Raíz:** `EnviarTelegramTool` dependía únicamente de pausas fijas breves (`sleep(0.4s)` / `sleep(0.8s)`) tras presionar `Super -> telegram -> Enter`, sin verificar si el binario de Telegram ya estaba activo o si su interfaz Qt había terminado de renderizar y obtener el foco de entrada del sistema.
- **Solución (`src/plugins/telegram/tools.py` y `src/utils/a11y.py`)**:
  1. **Detección Dinámica de Ejecutables y .desktop**:
     - Búsqueda de `org.telegram.desktop*.desktop` en `~/.local/share/applications` y `/usr/share/applications`, y fallback a binarios en `~/Applications/Telegram/Telegram`.
  2. **Lanzamiento y Enfoque Robusto (`_launch_or_focus_telegram`)**:
     - Uso de `gtk-launch` / ejecución directa del binario, combinado con elevación de ventana en GNOME Shell.
  3. **Sensor Reactivo de Estado (`wait_for_telegram_ready`)**:
     - Inspección en `AccessibilitySensor` (`get_telegram_window`) para confirmar que la app está activa y visible antes de enviar eventos de teclado.
  4. **Cadencia y Atajos Seguros**:
     - `Escape` previo para despejar modales o menús contextuales.
     - `Ctrl + K` (atajo global universal de búsqueda en Telegram Desktop).
     - Espera de 0.8s para el indexado de búsqueda local y de red antes de seleccionar el chat con `Down + Enter`.
     - Espera de 0.5s para el montaje del composer de texto antes del pegado con `Ctrl + V` y envío con `Enter`.

---

## 16. Eliminación de Duplicación de Respuestas y Limpieza Visual en Terminal
- **Desafío:** Al responder consultas comunes (ej. clima, información general), Atlas ejecutaba la herramienta `imprimir_en_consola` imprimiendo un cuadro redundante `[Texto en pantalla]` y luego repetía exactamente lo mismo en su respuesta hablada en `│ 🤖 Atlas: ...`. Adicionalmente, al silenciar el micrófono aparecían caracteres huérfanos residuales (ej. `...desactivado.sto.`).
- **Causa Raíz:** 
  1. En el system prompt (`SYS_PROMPT_BASE`), la regla 3 exigía llamar obligatoriamente a `imprimir_en_consola` porque anteriormente la terminal no transcribía la voz de Atlas. Con la integración de `output_audio_transcription`, esta regla provocaba duplicación 1:1.
  2. En `TerminalInteractionManager`, las impresiones con retorno de carro `\r` no incluían la secuencia ANSI `\033[K` (borrado de fin de línea), dejando caracteres residuales de cadenas más largas previas.
- **Solución:**
  1. **Actualización del System Prompt (`src/brain/prompts.py`)**:
     - Se ajustó la regla 3 para prohibir `imprimir_en_consola` en respuestas conversacionales normales, reservándola únicamente para bloques extensos de código o peticiones explícitas del usuario.
  2. **Refinamiento de `ImprimirConsolaTool` (`src/plugins/system/tools.py`)**:
     - Descripción actualizada para instruir al LLM que su voz ya se transcribe en pantalla.
  3. **Limpieza de Caracteres en Terminal (`src/ui/terminal_input.py`)**:
     - Incorporación de `\033[K` en `_toggle_mute` y `_adjust_sensitivity`.

## 17. Optimización y Resiliencia en Google Search Grounding
- **Desafío:** Google AI Studio aplica políticas de cuota y disponibilidad de modelos según el tier de la cuenta (`404 NOT_FOUND` en modelos descontinuados y `429 RESOURCE_EXHAUSTED` en Grounding gratuito sin tarjeta vinculada).
- **Causa Raíz:** En cuentas sin facturación habilitada, Google restringe el uso de la herramienta `google_search` en `gemini-3.6-flash`, mientras que modelos antiguos (`gemini-2.0-flash`, `gemini-1.5-flash`) fueron descontinuados.
- **Solución (`src/plugins/browser/engines/google_grounding.py`)**:
  1. **Modelo Oficial**: Configurado con `model="gemini-3.6-flash"` optimizado para síntesis rápida (<4s).
  2. **Silenciamiento de Advertencias Ruidosas**: Los errores de cuota o indisponibilidad de Google se registran a nivel `[DEBUG]`, evitando ensuciar la consola del usuario.
  3. **Delegación Fluida**: El gestor multi-motor salta limpiamente a **Tavily** (`[GOOGLE ❌ → TAVILY ✅]`) o **DuckDuckGo** de forma instantánea y transparente.

---

## 18. Estabilización de Máquina de Estados Wake Word, Anti-Alucinación Visual y Resiliencia 1011
- **Desafío:** En sesiones conversacionales con `wake_word: alexa`, el asistente sufría timeouts prematuros al estado `STANDBY` (sonido de "dormir" mientras procesaba o respondía), rompiendo la ventana de follow-up y obligando a decir "Alexa" repetidamente. Adicionalmente, el modelo ejecutaba capturas compulsivas de pantalla (`analizar_pantalla`) ante frases inocuas como "no te escucho" o saludos, leyendo números residuales de la terminal (ej: "29806595:46") y desatando un bucle de alucinación que culminaba en error `1011 Internal error encountered`.
- **Causas Raíz:**
  1. **Timeout Prematuro en AudioRecorder**: Al emitirse `END_OF_TURN`, `user_spoke` volvía a `False`, pero el temporizador `_active_started` seguía corriendo desde la detección inicial de la wake word. Si el modelo tardaba unos segundos en responder, el asistente se iba a `STANDBY` antes de recibir la respuesta. Al completarse el turno, `enter_follow_up()` ignoraba el estado si ya estaba en `STANDBY`.
  2. **Alucinación Compulsiva de Visión**: Ni el System Prompt ni la descripción de `analizar_pantalla` limitaban su uso a peticiones expresas del usuario, provocando que el modelo intentara "entender el contexto visual" ante cualquier confusión de audio.
  3. **Incompatibilidad de Protocolo en Inyección de Video**: `GeminiSession.send_video` usaba `video=` en lugar del parámetro estándar `media=` de `send_realtime_input`, lo que combinado con colisiones de turnos generaba desconexiones WebSocket 1011.
  4. **Sobrecarga de Trayectoria por Chunks de Texto**: `ResponseGenerated` se emitía en cada token individual, llenando los 100 pasos de trayectoria con fragmentos de una sola palabra y sobreescribiendo el historial real en disco.
- **Solución Implementada:**
  1. **Flag `waiting_for_model`**: El recorder permanece inmune al timeout de standby mientras espera la respuesta del modelo o mientras Atlas habla. `enter_follow_up()` reabre la ventana de 7 segundos sin importar el estado previo (mientras no esté silenciado).
  2. **Regla Anti-Alucinación 8 en System Prompt y Herramienta**: Prohibición explícita de usar `analizar_pantalla` por iniciativa propia en conversación normal o ante fallas de audio; restringido exclusivamente a peticiones explícitas del usuario ("mira mi pantalla", etc.) o verificación GUI.
  3. **Streaming Normalizado de STT (`UserTextChunk`)**: Extracción de `sc.input_transcription.text` para mostrar en la terminal y trayectoria exactamente qué reconoció Gemini por voz.
  4. **Separación de `AssistantTextChunk` y `ResponseGenerated`**: Streaming en tiempo real para la terminal y emisión consolidada de la respuesta completa solo al terminar el turno (`TurnComplete`).
  5. **Manejo Resiliente de 1011 y Limpieza de Colas**: Drenaje preventivo de colas PCM y reconexión suave ante errores transitorios 1011 del proveedor.
  6. **Ajuste de Umbral Wake Word**: Elevado a `0.55` para filtrar respiraciones y ruidos de fondo manteniendo 100% de efectividad ante el llamado intencional.

---

## 19. Priorización App-First (PWAs/Escritorio) y Deprecación de `media_chunks` en Gemini Live API
- **Desafíos:**
  1. **Apertura Ciega en Navegador Web**: Al pedir "abre whatsapp", "me refiero a la pwa", etc., el asistente abría `https://web.whatsapp.com` en una pestaña del navegador en lugar de la PWA instalada (`brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default.desktop`). Si el usuario especificaba "whatsapp web" o "whatsapp-pwa", fallaba con error de aplicación no permitida al depender de una lista estática de cadenas fijas.
  2. **Desconexión 1007 de WebSocket en Visión**: Al ejecutarse `analizar_pantalla`, el servidor de Google cerraba la conexión con `1007 None. realtime_input.media_chunks is deprecated. Use audio, video, or text instead.`
- **Causas Raíz:**
  1. `AbrirAplicacionTool` utilizaba un diccionario rígido que mapeaba servicios como `whatsapp` y `gmail` directamente a comandos `xdg-open 'https://...'`, sin consultar el registro del sistema de archivos `.desktop`.
  2. La SDK `google-genai` mapea el parámetro `media=` a `mediaChunks` en la capa de transporte mldev (`_LiveSendRealtimeInputParameters_to_mldev`), el cual fue deprecado en la API Live de Gemini en favor del campo nativo `video`.
- **Solución Implementada:**
  1. **Descubrimiento Dinámico de Aplicaciones (`scan_installed_desktop_apps` / `resolve_desktop_app`)**:
     - Escaneo en tiempo real de directorios Freedesktop (`~/.local/share/applications`, `/usr/share/applications`, flatpak, snap).
     - Soporte para alias bilingües, normalización de espacios (incluyendo NBSP de PWAs de Chromium), limpieza de sufijos (`pwa`, `web`, `app`) y scoring de coincidencia (con prioridad +5 para PWAs de usuario en `~/.local`).
     - Lanzamiento mediante `gtk-launch <desktop-id>` con fallback al comando `Exec=` limpio.
     - Regla de negocio estricta: Si una aplicación no está instalada localmente, **NUNCA** se abre en el navegador silenciosamente; se informa al usuario para que confirme o la instale como PWA.
  2. **Herramienta Dedicada `AbrirWhatsAppTool` (`abrir_whatsapp`)**:
     - Integrada en el plugin `src/plugins/whatsapp/` y enrutada también desde `abrir_aplicacion`, garantizando la apertura o enfoque de la PWA y la activación de accesibilidad AT-SPI2 sin tocar el navegador.
  3. **Actualización de Parámetro en `send_video` (`src/providers/gemini_session.py`)**:
     - Migrado de `media={"mime_type": mime_type, "data": data}` a `video={"mime_type": mime_type, "data": data}` para ajustarse a las especificaciones vigentes de Gemini Live API y evitar el error de desconexión 1007.
  4. **Cierre Nativo de Aplicaciones sin Confirmación Shell (`CerrarAplicacionTool` / `CerrarWhatsAppTool`)**:
     - Implementación de [`CerrarAplicacionTool`](file:///home/jonathan/proyectos/voice_agent/src/plugins/system/tools.py) y [`CerrarWhatsAppTool`](file:///home/jonathan/proyectos/voice_agent/src/plugins/whatsapp/tools.py).
     - Búsqueda segura con `psutil`, protección de procesos críticos (`PROTECTED_PROCESSES`) y exclusión estricta del árbol de procesos de Atlas.
     - Aislamiento de PWAs (WhatsApp, Gmail) por ID de aplicación (`cmdline`) para cerrar la ventana sin terminar la sesión del navegador.
     - Eliminación de la necesidad de recurrir a `proponer_comando('pkill ...')`, erradicando confirmaciones por voz innecesarias para acciones cotidianas de escritorio.

## 20. Robustez en Reconexión Gemini Live y Streaming de Voz Post-Inactividad (2026-09-03)
- **Problema Detectado:**
  - Tras periodos de inactividad de ~2.5 minutos en modo `STANDBY`, la API Gemini Live desconecta el WebSocket por inactividad (`1008 None. The operation was aborted`).
  - Al reconectar y activarse por voz (`WakeWordDetected`), el asistente quedaba atrapado en `Procesando respuesta...` sin responder ni emitir audio o texto.
  - Además, el bucle de reconexión agotaba el límite de 5 reintentos tras 12-15 minutos de silencio, cerrando el proceso de Atlas por completo.
- **Causa Raíz:**
  1. **Flag `generation_complete` en Gemini Live API**: La versión actual de Gemini Live API envía `generation_complete=True` en lugar de `turn_complete=True` al terminar de generar un turno. [`GeminiSession.receive()`](file:///home/jonathan/proyectos/voice_agent/src/providers/gemini_session.py) solo comprobaba `sc.turn_complete`, por lo que nunca emitía el evento `TurnComplete()`, manteniendo `self.recorder.waiting_for_model = True` permanentemente bloqueado para futuros turnos de audio.
  2. **Ciclo de vida del hardware de audio (PortAudio)**: `recorder.listen` y `player.play` se creaban dentro del `TaskGroup` de la sesión de red. Al desconectarse Gemini por inactividad, se cancelaba la tarea asyncio pero el hilo C subyacente de `asyncio.to_thread(in_stream.read)` seguía bloqueado; al iniciar una nueva conexión se creaba un segundo hilo leyendo del mismo stream de micrófono, fragmentando y corrompiendo los paquetes PCM de voz.
  3. **Preservación del presupuesto de reintentos en idle timeouts**: El contador `attempt` se incrementaba sin reiniciarse, considerando desconexiones normales por inactividad como fallos fatales de red.
- **Solución Implementada:**
  1. En [`src/providers/gemini_session.py`](file:///home/jonathan/proyectos/voice_agent/src/providers/gemini_session.py):
     - Detección de fin de turno con soporte para `sc.turn_complete` y `sc.generation_complete`.
  2. En [`src/brain/assistant.py`](file:///home/jonathan/proyectos/voice_agent/src/brain/assistant.py):
     - `self.recorder.listen` y `self.player.play` desacoplados de la red y promovidos al ámbito exterior continuo junto a `terminal_manager.listen()`, garantizando que el hardware de micrófono y altavoces nunca sufra colisiones ni cancelaciones por reconexión.
     - Reset explícito de `self.recorder.waiting_for_model = False` y limpieza de colas al iniciar una nueva sesión de proveedor.
     - Reinicio del contador de reintentos (`attempt = 1`) cuando la sesión previa estuvo viva y saludable (>15s), permitiendo a Atlas permanecer encendido indefinidamente en modo Standby sin límite de tiempo.



