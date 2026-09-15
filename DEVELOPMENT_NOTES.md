# Notas de Desarrollo y Troubleshooting de Atlas

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



