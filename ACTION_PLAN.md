# Plan de Acción y Estado de Implementación — Atlas

Este documento registra el roadmap técnico, las fases ejecutadas y el estado de madurez de cada subsistema de Atlas.

---

## 📊 Resumen de Estado General

| Subsistema / Fase | Estado | Descripción |
| :--- | :---: | :--- |
| **Fase 1: Seguridad y Resiliencia** | ✅ Completado | Timeouts asíncronos en Shell, listas de apps controladas, sanitización de paths y redacción de secretos. |
| **Fase 2: Persistencia y Memoria** | ✅ Completado | Escritura atómica en JSON, límites validados, búsqueda semántica vectorial local (`LocalSemanticSearch`). |
| **Fase 3: Higiene y Modularización** | ✅ Completado | Subsistema `src/voice/` modularizado, 56 tests unitarios automatizados (`pytest`). |
| **Fase 4: Proveedores y Streaming** | ✅ Completado | Abstracción agnóstica `ProviderSession` y normalización de eventos de streaming. |
| **Fase 5: UI & Web HUD en Tiempo Real** | ✅ Completado | Dashboard reactivo en `:7890` (WebSocket, visualizador de audio, recordatorios, controles). |
| **Fase 6: Recordatorios & MCP** | ✅ Completado | Planificador cron/recordatorios con notificaciones nativas (`notify-send`) y soporte Model Context Protocol. |
| **Fase 7: Control Multimedia (Spotify)** | ✅ Completado | Control nativo MPRIS D-Bus (`reproducir_musica`, `controlar_musica`, `que_suena`). |
| **Fase 8: Multi-Agente Auto-Evolutivo** | ✅ Completado | Subagente Desarrollador (`gemini-3.7-flash-high` vía CLIProxyAPI Oracle), compuerta HITL y Hot-Reloading. |
| **Fase 9: Búsqueda Multi-Motor & Deep Research** | ✅ Completado | Jerarquía con fallback Google Grounding → Tavily → DuckDuckGo + Motor Deep Research de 4 fases. |
| **Fase 10: Interacción Terminal Unificada** | ✅ Completado | `TerminalInteractionManager` con prompts libres, edición inline (flechas), historial, atajos de 1 tecla (Tab/Shift+Espacio) y aprobación HITL. |
| **Computer-Use Fase 0: Fundaciones** | ✅ Completado | Health-check de input con auto-reparación de ydotoold (sonda /proc, no socket), política de riesgo por tiers (`config/security_policy.yaml`), mini-benchmark falsable `tests/computer_use/` (4/4 en vivo). |
| **Computer-Use Fase 1: Capa de Acción Unificada** | ✅ Completado | `InputRouter` (ydotool → portal RemoteDesktop), backend experimental Portal con token persistente, `ElementResolver` AT-SPI2→SoM→coords, herramienta `interactuar_gui` con verificación por frame-diff. |
| **Computer-Use Fase 2: Bucle OODA** | ✅ Completado | `ComputerUseOrchestrator` (Observe AT-SPI2 → Decide LLM JSON → Act vía interactuar_gui → Verify por firma de estado), con spotlighting anti prompt-injection, límites duros (12 pasos, 2 sin-progreso), cancelación cooperativa y tool de voz `operar_gui_tarea`. |
| **Computer-Use Fase 3: Git Worktrees** | ✅ Completado | `WorktreeManager`: cada tarea del DeveloperAgent corre en `.worktrees/agent-<id>`; escrituras auto-aprobadas en el sandbox; pytest en el worktree; UNA sola aprobación HITL con diff antes de mergear; fallback limpio a modo directo si git no está disponible. |
| **Computer-Use Fase 4: Navegador CDP** | ✅ Completado | Navegador real vía CDP sin drivers: instancia dedicada con perfil persistente (restricción Chromium 136 verificada), snapshot de elementos `[n]` determinista, `navegador_web` con tiers + spotlighting; headless desechable disponible. |
| **Computer-Use Fase 5: Sesión headless** | ⏸️ Aplazada | Trabajo paralelo desechable (scraping/builds): requiere sway/cage + virtual pointer/keyboard wlr + bus AT-SPI propio. Esfuerzo 1-2 semanas; se activa solo si hay necesidad real. |
| **Computer-Use Fase 6: Endurecimiento** | ✅ Completado | Sandbox `bwrap` para todo shell del subagente (red off por defecto, FS mínimo, HOME aislado, `--clearenv`), `CredentialBroker` (scrub_env + redacción de secretos en salidas al LLM), `ActionMonitor` de anomalías (ráfaga/repetición → pausa + HITL). |
| **Computer-Use Fase 7: Medición continua** | ✅ Completado | Benchmark ampliado a 7 sondas en 3 tiers (escritorio/local/navegador-headless); flujo CDP E2E medido activamente (abrir→elementos→click→escribir→leer, 1.35s); histórico `history.jsonl` con Δ contra la corrida anterior; baseline registrada: **100% (7/7)**. |

---

## 🎯 Detalle de Fases Completadas

### ✅ Fase 1 — Seguridad y Robustez del Runtime
- [x] Timeouts asíncronos en comandos shell (`asyncio.wait_for`).
- [x] Eliminación de path traversal en documentos Office (`os.path.basename`).
- [x] Redacción automática de API keys y secretos en resultados de herramientas web.
- [x] Manejo de reconexión con backoff exponencial configurable y supresión de desconexiones transitorias 1008.

### ✅ Fase 2 — Base de Conocimiento y Memoria Semántica
- [x] Escritura atómica en `atlas_knowledge.json` (`.tmp` + `os.replace`).
- [x] Validación estricta de notas (largo máximo y rotación FIFO).
- [x] Búsqueda semántica vectorial local mediante n-gramas y similitud coseno (`src/knowledge/semantic.py`).

### ✅ Fase 3 — Subsistema de Audio y Limpieza Visual
- [x] Modularización en `src/voice/`: `recorder.py`, `player.py`, `constants.py`, `vad.py`.
- [x] Detector de voz inteligente (`VoiceActivityDetector`) multi-feature (Energía + ZCR + Crest Factor).
- [x] Supresión de errores de bajo nivel C de ALSA y PortAudio (`src/voice/alsa_mute.py`).
- [x] Terminal limpia y estilizada con streaming fluido en [`src/ui/cli.py`](file:///home/jonathan/proyectos/voice_agent/src/ui/cli.py).

### ✅ Fase 4 — Integración Multimedia (Spotify)
- [x] Plugin nativo `src/plugins/media/` utilizando protocolo MPRIS D-Bus.
- [x] Soporte para `reproducir_musica(busqueda="...")`, `controlar_musica(accion="play_pause")` y consulta de metadata `que_suena`.

### ✅ Fase 5 — Arquitectura Multi-Agente Auto-Evolutiva (Gemini 3.7 Flash + HITL)
- [x] Cliente asíncrono para **CLIProxyAPI** (`http://127.0.0.1:8317/v1`) consumiendo `gemini-3.7-flash-high` en el servidor Oracle.
- [x] Subagente autónomo en segundo plano (`DeveloperAgent`) con capacidades de lectura, escritura, ejecución de pruebas `pytest` y recarga en caliente.
- [x] Compuerta de Aprobación Humana (`ApprovalManager` / HITL) sincronizada en voz, Web HUD interactivo y terminal.
- [x] Recarga en caliente (*Hot-Reloading*) de plugins sin interrumpir la sesión de voz.

### ✅ Fase 6 — Búsqueda Inteligente Multi-Motor & Deep Research
- [x] Jerarquía de motores: Google Grounding (primario) → Tavily (fallback) → DuckDuckGo (fallback libre).
- [x] Motor de investigación profunda (`DeepResearchEngine`) con descomposición de consultas, scraping y síntesis con razonamiento.
- [x] Lector de páginas web (`WebPageReader`) para extracción de contenido textual limpio.

### ✅ Fase 7 — Gestor de Terminal & Prompts Avanzados
- [x] Escritura y edición de prompts con auto-wrap nativo sin duplicación.
- [x] Navegación de cursor con flechas (`←` / `→`), `Inicio`, `Fin`, `Supr`.
- [x] Historial de prompts (`↑` / `↓`).
- [x] Atajos rápidos de Mute (`Tab`, `Shift+Espacio`, `Ctrl+Espacio`, `F2`).
- [x] Aprobación de 1 tecla (`[Enter]` en línea vacía).

---

## 🧪 Pruebas Automatizadas
La suite cuenta con **388 pruebas unitarias** pasando al 100% (+1 sonda en vivo opcional):
```bash
./venv/bin/pytest tests/ -v
```
