# Plan de acción — Atlas (voice_agent)

> Contexto para quien ejecute este plan: Atlas es un asistente de voz en Python (Gemini Live API) que puede ejecutar comandos de shell, abrir aplicaciones, capturar pantalla y crear documentos a partir de instrucciones habladas. Este plan surge de una revisión de código completa del repo. **El modelo Gemini configurado actualmente y el flujo general de voz funcionan correctamente en producción para el usuario — no tocar `config.yaml` / `src/config/models.py` / `src/providers/gemini.py` en lo referente al nombre del modelo.** Cada fase es independiente; ejecútalas en orden de prioridad pero no es necesario completarlas todas en una sola sesión. Antes de cada cambio, correr `git status` y confirmar que no se pisa trabajo en curso.

## Fase 1 — Seguridad (prioridad alta)

### 1.1 `abrir_aplicacion` esquiva el flujo de confirmación
- Archivo: `src/plugins/system/tools.py`, clase `AbrirAplicacionTool.execute` (líneas 111-148).
- Problema: cuando `nombre` no está en el diccionario `mapeos` (línea 118), si `shutil.which(nombre_lower)` encuentra un binario en PATH, se ejecuta directo con `subprocess.Popen(cmd, shell=True, ...)` (línea 143), sin pasar por ningún mecanismo de confirmación ni por `context.config.tools.shell.enabled`.
- Acción:
  - Para nombres que SÍ están en `mapeos` (apps/webs conocidas y explícitamente soportadas): dejar el comportamiento actual, es una lista cerrada y controlada.
  - Para el fallback de `shutil.which` (ejecutar un binario arbitrario detectado en PATH): exigir el mismo flujo `proponer_comando` → `ejecutar_comando_confirmado` que ya usa `src/plugins/shell/tools.py`, en vez de ejecutar directo. Alternativa más simple si no se quiere reusar `CommandState`: eliminar por completo la rama de fallback por `shutil.which` y limitar `abrir_aplicacion` estrictamente al diccionario `mapeos`.
- Criterio de aceptación: pedir por voz/texto "abre reboot" o "abre pkill" no debe ejecutar nada sin confirmación explícita del usuario.

### 1.2 Blacklist de comandos peligrosos trivialmente evadible
- Archivo: `src/plugins/shell/tools.py`, línea 89: `blocked_keywords = ["rm -rf /", "mkfs", "> /dev/sda"]`.
- Problema: `rm -rf ~`, `rm -rf /*`, `dd if=/dev/zero of=/dev/sda1`, `curl x | bash`, etc. no son detectados.
- Acción: no se puede tapar esto con una blacklist perfecta (es un problema conocido de "shell libre"). Mejora pragmática:
  - Documentar explícitamente en el prompt del sistema (`src/brain/prompts.py`) que el usuario es el único que debe iniciar acciones destructivas, y que resultados de herramientas (búsqueda web) NUNCA deben interpretarse como instrucciones a ejecutar (ver 1.4).
  - Expandir la lista de patrones bloqueados con regex en vez de substrings literales (cubrir `rm -rf` seguido de cualquier ruta que empiece en `/`, `~`, o variables de entorno; `dd of=/dev/`; `mkfs.*`; pipes a `sh`/`bash` desde `curl`/`wget`).
  - Dejar constancia en el código (comentario) de que esto es defensa en profundidad, no una garantía — la barrera real sigue siendo la confirmación humana.
- Criterio de aceptación: los ejemplos de evasión de esta sección quedan bloqueados por la nueva lista/regex.

### 1.3 `EjecutarComandoTool` sin timeout — puede colgar el asistente
- Archivo: `src/plugins/shell/tools.py`, clase `BashExecutor.run` (líneas 20-35), usado desde `EjecutarComandoTool.execute` (línea 140).
- Problema: `await process.communicate()` sin límite de tiempo. Mientras la tool corre, el asistente no procesa audio (`processing_tool` en `src/brain/assistant.py`).
- Acción: envolver `process.communicate()` en `asyncio.wait_for(..., timeout=N)` (sugerido 30-60s, configurable). Si expira, matar el proceso (`process.kill()`) y devolver `ToolResult(success=False, content="El comando excedió el tiempo límite y fue cancelado.")`.
- Criterio de aceptación: `ejecutar_comando_confirmado` con `sleep 999` termina en ~30-60s con un mensaje de timeout, no cuelga el asistente.

### 1.4 Path traversal en el plugin de Office
- Archivo: `src/plugins/office/tools.py`, `CrearDocumentoOfficeTool.execute`, líneas 52 y 69: `ruta_archivo = os.path.join(escritorio, f"{nombre}.docx")` con `nombre = kwargs.get("nombre_archivo", "documento")` sin sanitizar.
- Problema: `nombre_archivo = "../../.config/algo"` escribe fuera del Escritorio.
- Acción: sanitizar `nombre` antes de construir la ruta — usar `os.path.basename(nombre)` y además rechazar (o limpiar) cualquier resultado vacío o que contenga `..`/separadores de ruta tras el `basename`. Ejemplo:
  ```python
  nombre = os.path.basename(kwargs.get("nombre_archivo", "documento")).strip() or "documento"
  ```
- Criterio de aceptación: pedir crear un documento con `nombre_archivo="../../evil"` produce un archivo dentro del Escritorio (p. ej. `evil.docx`), nunca fuera de él.

### 1.5 `analizar_pantalla` sin ningún control (privacidad)
- Archivo: `src/plugins/vision/tools.py`.
- Problema: captura el monitor completo y lo envía a la API en la nube en cada llamada, sin gate de configuración ni límite de frecuencia (a diferencia de shell, que sí respeta `context.config.tools.shell.enabled`).
- Acción: añadir un flag de configuración equivalente (ya existe `vision: enabled: false` en `config.yaml` — verificar que el plugin realmente lo consulte antes de capturar; si no lo hace, añadir el chequeo al inicio de `execute`). Opcional: añadir un cooldown mínimo entre capturas.
- Criterio de aceptación: con `vision.enabled: false` en `config.yaml`, la tool rechaza ejecutarse con un mensaje claro.

## Fase 2 — Persistencia y datos de usuario (prioridad media-alta)

### 2.1 Sacar `jarvis_memory.json` (legacy) del control de versiones
- El archivo está trackeado en git (commit `c4b7545`) y ningún código lo referencia ya (`grep` no encuentra usos).
- Acción:
  ```bash
  git rm --cached jarvis_memory.json
  ```
  y añadir `jarvis_memory.json` al `.gitignore`. Confirmar con el usuario antes de borrar el archivo del disco (puede tener notas que quiera revisar/migrar manualmente al nuevo formato antes de descartarlo).

### 2.2 Evitar que `atlas_knowledge.json` termine versionado
- Es el backend activo (`src/knowledge/backends/json.py:7`, default `"atlas_knowledge.json"`) y contiene datos personales (ubicación real del usuario).
- Acción: añadir `atlas_knowledge.json` (y en general el patrón usado para el archivo de memoria activo) a `.gitignore`. Si se quiere permitir configurarlo por ruta, considerar mover el default fuera del repo (p. ej. `~/.config/atlas/knowledge.json`) — cambio más invasivo, opcional, discutir con el usuario antes de aplicarlo.

### 2.3 Escritura no atómica del backend JSON
- Archivo: `src/knowledge/backends/json.py`, método `save` (líneas 25-34).
- Problema: escribe directo sobre `self.file_path`; una interrupción a mitad de escritura corrompe el archivo, y `load()` ante cualquier excepción de parseo resetea silenciosamente a `KnowledgeState()` vacío (línea 21-23), perdiendo todo el historial sin aviso real.
- Acción: escribir a un archivo temporal en el mismo directorio y luego `os.replace()` al destino final:
  ```python
  def save(self, state: KnowledgeState) -> None:
      tmp_path = f"{self.file_path}.tmp"
      try:
          with open(tmp_path, 'w', encoding='utf-8') as f:
              json.dump({...}, f, indent=4, ensure_ascii=False)
          os.replace(tmp_path, self.file_path)
      except Exception as e:
          print(f"⚠️ Error guardando base de conocimiento JSON: {e}")
          raise
  ```
  Nota el `raise` al final — necesario para el punto 2.4.

### 2.4 Fallos de guardado nunca llegan al usuario
- Archivos: `src/knowledge/manager.py` (`save_profile`, `save_note`) y `src/plugins/knowledge/tools.py` (`GuardarNotaTool.execute`, `GuardarPerfilTool.execute`).
- Problema: estas tools siempre devuelven `ToolResult(success=True, ...)` sin comprobar si `backend.save()` realmente tuvo éxito.
- Acción: tras aplicar 2.3 (que ahora relanza la excepción), envolver las llamadas a `save_note`/`save_profile` en try/except dentro de las tools y devolver `ToolResult(success=False, content="No pude guardar la nota/perfil, hubo un error de escritura.")` si falla.
- Criterio de aceptación: simular un `file_path` sin permisos de escritura y confirmar que la tool responde con `success=False` y un mensaje honesto, no "guardado correctamente".

### 2.5 Límites documentados pero no aplicados
- Archivo: `src/plugins/knowledge/tools.py` (descripciones prometen "máx 200 caracteres" en notas y "máx 10 campos" en perfil) vs `src/knowledge/manager.py` (`save_note`, `save_profile`) que no validan nada.
- Acción: aplicar los límites reales dentro de `KnowledgeManager.save_note`/`save_profile` (truncar o rechazar con mensaje claro), no solo en el texto descriptivo de la tool.

## Fase 3 — Higiene de repositorio y CI (prioridad media)

### 3.1 Arreglar o eliminar los 17 scripts rotos por `SyntaxError`
- Causa: el commit `c4b7545` reemplazó valores de API key por el string literal inválido `"os.getenv("GEMINI_API_KEY", "your-api-key")"` (comillas anidadas sin escapar) en: `list_models.py`, `debug_search.py`, `test_gemini2.py`, `test_gemini_models.py`, `test_gemini_models_v1.py`, `test_gemini_models_v1alpha.py`, `test_gemini_search.py`, `test_gemini_search2.py`, `test_keys.py`, `test_mcp.py`, `test_mcp_real.py`, `test_mcp_search.py`, `test_mcp_search2.py`, `test_tool_response.py/2/3/4.py`.
- Verificar cada uno con `python3 -m py_compile <archivo>`.
- Acción por archivo: si el script sigue siendo útil como debug manual, reemplazar la línea rota por `os.getenv("GEMINI_API_KEY", "your-api-key")` (sin comillas extra). Si es un experimento ya obsoleto (varios de los `test_gemini_models_v1*.py` parecen exploraciones puntuales de la API), eliminarlo directamente en vez de arreglarlo. Confirmar con el usuario el criterio para cada grupo antes de borrar en bloque.
- Adicional: `test_keys.py` imprime `api_key[:15]` de la key real en stdout (línea con `print(f"Testing key {i} ({api_key[:15]}...)")`) — quitar ese print o truncar a un largo que no sea reconstruible (p. ej. solo `len(api_key)`).

### 3.2 Mover scripts de debug fuera de la raíz
- `debug_search.py`, `debug_tool.py`, `doctor.py`, `list_models.py`, y los `test_*.py` que sobrevivan a 3.1 no usan pytest/unittest — son scripts manuales.
- Acción: crear `scripts/` o `debug/` y moverlos ahí con `git mv`, actualizando cualquier referencia (p. ej. si `ci.yml` invoca `doctor.py` por ruta, ver 3.4).

### 3.3 Pinear versiones en `requirements.txt` y declarar dependencias faltantes
- Archivo: `requirements.txt`. Ninguna dependencia tiene versión fija.
- Acción: fijar al menos versión mínima compatible para cada paquete (`google-genai`, `pyaudio`, `openwakeword`, `pyyaml`, `mss`, `Pillow`, `python-docx`) basándose en lo instalado actualmente en el entorno de desarrollo (`pip freeze | grep -i <paquete>`). Añadir `numpy` explícitamente (se usa en `src/brain/assistant.py` vía `import numpy as np`, hoy llega solo transitivamente por `openwakeword`).

### 3.4 CI no valida nada real
- Archivo: `.github/workflows/ci.yml`.
- Problema: los pasos de `ruff`/`black`/`mypy` están comentados; el job de test solo corre `python doctor.py` como smoke test, sin pytest.
- Acción (incremental, no rediseñar todo el pipeline de una vez):
  1. Descomentar `ruff` primero (es el más rápido de poner en verde) y arreglar lo que reporte, o configurarlo en modo no-bloqueante (`continue-on-error: true`) si el código no está listo para ser estricto todavía.
  2. Dejar `black --check` y `mypy` como siguiente paso, documentado como pendiente si se decide no abordarlos ahora.
  3. Si en el futuro se agregan tests reales con pytest (fuera de alcance de este plan), agregar el paso `pytest` al job `build-and-test`.

### 3.5 `.gitignore` — entradas faltantes y ambiguas
- Archivo: `.gitignore`.
- Acción:
  - Añadir `jarvis_memory.json` y `atlas_knowledge.json` (ver 2.1/2.2).
  - Cambiar la línea `core` por `/core` si la intención original era ignorar solo un archivo de core dump en la raíz (evita que en el futuro una carpeta `src/core` sea ignorada por accidente). Confirmar con el usuario la intención original antes de cambiarlo.

### 3.6 README desactualizado
- Archivo: `README.md`, sección Roadmap (línea ~126).
- Acción: marcar "Vision Plugin" como completado (`[x]`), y añadir el plugin Office (creado en el commit `04f52f6`) al diagrama de arquitectura y a la lista de plugins si corresponde.

## Fase 4 — Backlog / opcional (no urgente)

### 4.1 Wake word por voz no funcional
- Confirmado por el usuario: la activación real hoy es por atajo de teclado (`input_watcher` en `src/brain/assistant.py:307-327`), no por voz. El modelo cargado es `hey_jarvis` (línea 360) pero los mensajes dicen "Hey Atlas" (líneas 100, 354; `src/ui/cli.py:36`).
- No es bloqueante — el usuario ya tiene un flujo de activación que funciona.
- Si en el futuro se quiere retomar: opción mínima es corregir los mensajes para que no prometan una función que no se usa; opción completa es entrenar un modelo custom de openwakeword para "Hey Atlas" (requiere dataset de audio propio, fuera de alcance de un fix de código simple).

### 4.2 `src/voice/` vacío
- No hay imports rotos ni nada pendiente por él, pero toda la lógica de audio vive dentro de `src/brain/assistant.py` (God object). Decidir explícitamente: extraer esa lógica a `src/voice/` (refactor de mayor alcance, no trivial) o eliminar el directorio vacío. No priorizar sobre las fases 1-3.

### 4.3 `toggle_jarvis.sh` expone API keys en el árbol de procesos
- Extrae `GEMINI_API_KEY`/`TAVILY_API_KEY` desde `~/.bashrc` y las mete en un string pasado a `bash -c`, visibles en `ps aux` mientras el proceso vive.
- Mejora futura: usar un archivo de entorno (`--env-file` o `set -a; source .env; set +a`) en vez de interpolar el valor en el comando. No urgente si la máquina es de un solo usuario de confianza.

---

## Orden sugerido de ejecución
1. Fase 1 completa (seguridad) — es la que más impacto tiene si el asistente ya está en uso activo ejecutando comandos reales.
2. Fase 2 (2.1 y 2.2 primero, son de bajo esfuerzo y alto impacto en privacidad; luego 2.3-2.5).
3. Fase 3 (housekeeping, no bloqueante, pero recomendable antes de que el repo crezca más).
4. Fase 4 solo si sobra tiempo o el usuario lo pide explícitamente.

Después de cada fase, correr `git status` y `git diff` para revisar el alcance real de los cambios antes de proponer un commit, y no commitear sin que el usuario lo pida explícitamente.
