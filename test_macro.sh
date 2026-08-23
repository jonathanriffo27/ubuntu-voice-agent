#!/bin/bash
onlyoffice-desktopeditors &
sleep 5
wl-copy "Texto Original de Prueba"
ydotool key 29:1 47:1 47:0 29:0 # Ctrl+V
sleep 1

# Copiar al portapapeles lo que hay
ydotool key 29:1 30:1 30:0 29:0 # Ctrl+A
sleep 0.1
ydotool key 29:1 46:1 46:0 29:0 # Ctrl+C
sleep 0.4

# Deseleccionar
ydotool key 106:1 106:0
sleep 0.2

# Buscar
ydotool key 29:1 33:1 33:0 29:0
sleep 0.5
wl-copy "Original"
sleep 0.3
ydotool key 29:1 47:1 47:0 29:0
sleep 0.4
ydotool key 28:1 28:0 # Enter
sleep 0.2
ydotool key 1:1 1:0 # Esc
sleep 0.2

# Pegar modificado
wl-copy "MODIFICADO"
sleep 0.3
ydotool key 29:1 47:1 47:0 29:0
sleep 0.5

# Copiar resultado
ydotool key 29:1 30:1 30:0 29:0
sleep 0.1
ydotool key 29:1 46:1 46:0 29:0
sleep 0.4

wl-paste
