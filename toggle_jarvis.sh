#!/bin/bash

# Directorio del proyecto (determinado dinámicamente)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Buscar si hay una instancia de Atlas/Jarvis corriendo
PID=$(pgrep -f "python3 jarvis.py")

if [ -n "$PID" ]; then
    echo "Atlas detectado (PID: $PID). Apagando..."
    kill -INT $PID
    exit 0
fi

# 2. Atlas no está corriendo, lo iniciamos
echo "Iniciando Atlas..."

# Cargar variables de entorno: primero desde .env si existe, sino fallback a ~/.bashrc
if [ -f "$DIR/.env" ]; then
    CMD="cd \"$DIR\"; set -a; source .env; set +a; ./venv/bin/python3 jarvis.py; exec bash"
else
    KEY=$(grep "export GEMINI_API_KEY" ~/.bashrc 2>/dev/null | tail -1 | awk -F '"' '{print $2}')
    TAVILY_KEY=$(grep "export TAVILY_API_KEY" ~/.bashrc 2>/dev/null | tail -1 | awk -F '"' '{print $2}')
    CMD="export GEMINI_API_KEY=\"$KEY\"; export TAVILY_API_KEY=\"$TAVILY_KEY\"; cd \"$DIR\"; ./venv/bin/python3 jarvis.py; exec bash"
fi

# Detectar terminal disponible e iniciar
if command -v gnome-terminal &> /dev/null; then
    gnome-terminal -- bash -c "$CMD"
elif command -v kgx &> /dev/null; then
    kgx -e bash -c "$CMD"
elif command -v terminator &> /dev/null; then
    terminator -e "bash -c '$CMD'"
elif command -v alacritty &> /dev/null; then
    alacritty -e bash -c "$CMD"
elif command -v xterm &> /dev/null; then
    xterm -e bash -c "$CMD"
else
    x-terminal-emulator -e bash -c "$CMD"
fi
