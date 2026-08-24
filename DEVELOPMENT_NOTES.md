# Notas de Desarrollo y Troubleshooting de Atlas

Este documento registra los problemas arquitectónicos, optimizaciones de bajo nivel y soluciones técnicas implementadas en Atlas.

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
