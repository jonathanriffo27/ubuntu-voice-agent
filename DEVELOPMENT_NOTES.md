# Notas de Desarrollo y Troubleshooting

Este documento registra los problemas arquitectónicos y bugs críticos que se encontraron durante el desarrollo inicial del agente, sus causas raíz y las soluciones implementadas. Servirá como referencia futura en caso de actualizaciones del SDK o cambios en la API.

## 1. Segmentation Fault (Violación de Segmento) al cerrar
- **Síntoma:** Al interrumpir el script con `Ctrl+C` o al ocurrir una falla de red, Python colapsaba lanzando `Violación de segmento (core generado)`.
- **Causa Raíz:** El micrófono se lee usando `asyncio.to_thread(input_stream.read)`. Al cancelar el Event Loop de Python, el hilo subyacente de C (PyAudio/PortAudio) seguía bloqueado esperando audio. Cuando el bloque `finally` ejecutaba `p.terminate()` para liberar la memoria, el hilo de lectura intentaba acceder a memoria ya destruida.
- **Solución:** 
  1. Se implementó una verificación `if not input_stream.is_active(): break` antes de leer.
  2. En fallas críticas de red manejadas por el `TaskGroup`, se reemplazó el cierre suave por un `os._exit(1)`, delegando al Sistema Operativo la destrucción segura e inmediata de todos los hilos nativos.

## 2. Congelamiento tras el primer turno (Keepalive Ping Timeout 1011)
- **Síntoma:** El agente respondía perfectamente a la primera interacción, pero luego ignoraba el micrófono. Eventualmente colapsaba con `sent 1011 (internal error) keepalive ping timeout`.
- **Causa Raíz:** En la API Live, el iterador `session.receive()` se agota (termina su ciclo) cada vez que el servidor envía la señal de fin de turno. Al no estar envuelto en un bucle infinito, la corrutina de recepción moría silenciosamente tras la primera respuesta. Al no haber nadie leyendo los mensajes del WebSocket, los pings de mantenimiento se acumulaban hasta dar timeout.
- **Solución:** Envolver `async for msg in session.receive():` dentro de un bloque `while True:`.

## 3. Disparos infinitos de turnos vacíos (VAD Compitiendo)
- **Síntoma:** Se enviaban señales de `turn_complete` múltiples veces por segundo sin que el usuario hablara.
- **Causa Raíz:** El VAD (Voice Activity Detection) casero por RMS calculaba silencios constantemente basándose en el ruido de fondo, forzando cortes.
- **Solución:** Se añadió un estado `speech_detected`. El contador de silencio (y el disparo de fin de turno local) ahora solo se activa *después* de que se haya detectado un pico de volumen real.

## 4. El modelo 3.1-Flash devuelve respuestas vacías
- **Síntoma:** Usando `gemini-3.1-flash-live-preview`, el sistema detectaba voz y el servidor aceptaba los turnos, pero nunca llegaba el payload de `inline_data` (audio) ni la transcripción.
- **Causa Raíz:** Bug o restricción de la versión preview 3.1 en el manejo de modalidades de audio puro bidireccional. 
- **Solución:** Cambio a `gemini-2.5-flash-native-audio-preview-12-2025`, el cual procesa la modalidad de voz nativa de forma estable.

## 5. Cambios en el SDK (google-genai v2.14.0)
- **Síntoma:** Error `TypeError: AsyncSession.send_client_content() got an unexpected keyword argument 'contents'` al intentar mandar un texto inicial.
- **Causa Raíz:** La firma del método cambió recientemente.
- **Solución:** Se utilizó temporalmente el método `session.send(input="texto", end_of_turn=True)`. Como este arroja un `DeprecationWarning`, se envolvió en un supresor de advertencias de Python para mantener la terminal limpia.
