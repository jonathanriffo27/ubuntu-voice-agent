#!/usr/bin/env python3
"""
Script de automatización de interfaz gráfica para enviar mensajes por WhatsApp en Linux.
Utiliza detección de estado reactiva por AT-SPI2 para esperar a que WhatsApp esté listo (evitando condiciones de carrera durante la carga).
"""

import os
import sys
import time
import shutil
import subprocess
import urllib.parse

# Agregar raíz del proyecto a sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.a11y import AccessibilitySensor


def set_clipboard(text: str):
    """Copia texto al portapapeles usando wl-copy o xclip."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
            return
        except Exception:
            pass
    if shutil.which("xclip"):
        try:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
            return
        except Exception:
            pass


def send_key_ydotool(key_code: int):
    """Envía pulsación de tecla usando ydotool si está disponible."""
    if shutil.which("ydotool"):
        try:
            env = os.environ.copy()
            env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"
            subprocess.run(["ydotool", "key", f"{key_code}:1", f"{key_code}:0"], env=env, check=False)
            return True
        except Exception:
            pass
    return False


def type_text_ydotool(text: str):
    """Escribe texto con ydotool."""
    if shutil.which("ydotool"):
        try:
            env = os.environ.copy()
            env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"
            subprocess.run(["ydotool", "type", "-d", "15", text], env=env, check=False)
            return True
        except Exception:
            pass
    return False


def launch_or_focus_whatsapp():
    """Lanza o enfoca la ventana PWA de WhatsApp Web."""
    desktop_id = "brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default"
    if shutil.which("gtk-launch"):
        try:
            subprocess.run(["gtk-launch", desktop_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"
    subprocess.run(["ydotool", "key", "125:1", "125:0"], env=env, check=False)
    time.sleep(0.3)
    subprocess.run(["ydotool", "type", "-d", "15", "whatsapp"], env=env, check=False)
    time.sleep(0.3)
    subprocess.run(["ydotool", "key", "28:1", "28:0"], env=env, check=False)
    time.sleep(0.5)


def automated_whatsapp_flow(phone_number: str, message: str) -> bool:
    """
    Flujo de interacción GUI con sensor AT-SPI2:
    1. Enfoca / abre WhatsApp.
    2. Espera a que la app esté realmente lista (no en estado de carga o QR).
    3. Busca el chat.
    4. Escribe y envía presionando Enter.
    """
    print(f"[*] Iniciando automatización GUI para WhatsApp")
    print(f"[*] Destinatario: {phone_number}")
    print(f"[*] Mensaje: {message}")

    sensor = AccessibilitySensor()

    # 1. Lanzar/enfocar
    launch_or_focus_whatsapp()

    # 2. Esperar reactivamente con AT-SPI2
    print("[*] Consultando árbol de accesibilidad AT-SPI2 para verificar estado de carga...")
    ready, detail = sensor.wait_for_whatsapp_ready(timeout=15.0)
    if not ready:
        print(f"[!] Error: WhatsApp Web no está listo ({detail}). Abortando.")
        return False

    print(f"[✔] WhatsApp Web verificado y listo: {detail}")

    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = f"/run/user/{os.getuid()}/.ydotool_socket"

    # 3. Enfocar barra de búsqueda con Ctrl+Alt+/ (Keycodes: 29, 56, 53)
    print("[*] Enfocando búsqueda (Ctrl+Alt+/)...")
    subprocess.run(["ydotool", "key", "29:1", "56:1", "53:1", "53:0", "56:0", "29:0"], env=env, check=False)
    time.sleep(0.3)

    # 4. Escribir destinatario
    clean_num = phone_number.replace(" ", "").replace("-", "") if any(c.isdigit() for c in phone_number) else phone_number
    type_text_ydotool(clean_num)
    time.sleep(0.6)

    # 5. Presionar Enter para abrir chat
    print("[*] Abriendo chat...")
    send_key_ydotool(28)
    sensor.wait_for_chat_open(phone_number, timeout=5.0)
    time.sleep(0.2)

    # 6. Copiar y pegar mensaje con Ctrl+V (Keycodes: 29, 47)
    set_clipboard(message)
    time.sleep(0.1)
    subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], env=env, check=False)
    time.sleep(0.2)

    # 7. Presionar Enter para enviar
    print("[*] Enviando mensaje...")
    send_key_ydotool(28)
    time.sleep(0.3)

    sensor.verify_message_sent(timeout=2.0)
    print("[✔] Automatización completada exitosamente.")
    return True


def main():
    target_phone = "+56 9 5790 9790"
    message = "Mensaje de prueba"

    if len(sys.argv) > 1:
        target_phone = sys.argv[1]
    if len(sys.argv) > 2:
        message = sys.argv[2]

    success = automated_whatsapp_flow(phone_number=target_phone, message=message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
