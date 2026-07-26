# Ubuntu Voice Agent (Jarvis)

Un asistente de voz bidireccional y en tiempo real para Ubuntu, potenciado por la Live API de Google Gemini (2.5-flash-native-audio).
Este agente no solo conversa contigo por micrófono y altavoces, sino que tiene capacidades autónomas de ejecución de comandos bash (con una capa de confirmación vocal por seguridad).

## Requisitos Previos

- Python 3.10 o superior
- Bibliotecas del sistema para PyAudio (ALSA/PortAudio)
  ```bash
  sudo apt-get install portaudio19-dev python3-pyaudio
  ```

## Instalación

1. Clona el repositorio.
2. Crea un entorno virtual:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Instala las dependencias:
   ```bash
   pip install google-genai pyaudio
   ```

## Uso

1. Exporta tu clave de API de Google AI Studio:
   ```bash
   export GEMINI_API_KEY="tu_clave_aqui"
   ```
2. Ejecuta el agente:
   ```bash
   python3 jarvis.py
   ```

## Arquitectura

- `jarvis.py`: Maneja el bucle de eventos asíncrono, la captura de audio por RMS (VAD), la síntesis y el WebSocket de la Live API.
- `tools.py`: Define las herramientas y el `CommandManager`, el cual requiere confirmación explícita (del usuario por voz) antes de ejecutar cualquier subproceso en la terminal por razones de seguridad.
