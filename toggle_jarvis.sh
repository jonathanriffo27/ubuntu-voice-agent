#!/bin/bash

# Directorio donde está el proyecto
DIR="/home/jonathan/proyectos/voice_agent"

# 1. Buscar si hay una instancia de Jarvis corriendo
PID=$(pgrep -f "python3 jarvis.py")

if [ -n "$PID" ]; then
    # Jarvis está corriendo, lo apagamos
    echo "Jarvis detectado (PID: $PID). Apagando..."
    kill -INT $PID
    exit 0
fi

# 2. Jarvis no está corriendo, lo iniciamos
echo "Iniciando Jarvis..."

# Intentar extraer las KEYs del bashrc
KEY=$(grep "export GEMINI_API_KEY" ~/.bashrc | tail -1 | awk -F '"' '{print $2}')
TAVILY_KEY=$(grep "export TAVILY_API_KEY" ~/.bashrc | tail -1 | awk -F '"' '{print $2}')


# Comando a ejecutar
CMD="export GEMINI_API_KEY=\"$KEY\"; export TAVILY_API_KEY=\"$TAVILY_KEY\"; cd $DIR; ./venv/bin/python3 jarvis.py; exec bash"

# Detectar terminal disponible e iniciar
if command -v gnome-terminal &> /dev/null; then
    gnome-terminal -- bash -c "$CMD"
elif command -v kgx &> /dev/null; then
    # kgx es gnome-console (terminal por defecto en Ubuntu recientes)
    kgx -e bash -c "$CMD"
elif command -v terminator &> /dev/null; then
    terminator -e "bash -c '$CMD'"
elif command -v alacritty &> /dev/null; then
    alacritty -e bash -c "$CMD"
elif command -v xterm &> /dev/null; then
    xterm -e bash -c "$CMD"
else
    # Fallback genérico
    x-terminal-emulator -e bash -c "$CMD"
fi
