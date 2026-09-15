#!/usr/bin/env python3
"""
Script para enviar un mensaje a 'Mensajes guardados' (Saved Messages / 'me')
en Telegram usando la librería Telethon.

Uso:
    python scripts/telegram_saved_messages.py [mensaje]

Variables de entorno esperadas (o se solicitarán interactivamente):
    TELEGRAM_API_ID : int / str
    TELEGRAM_API_HASH : str
    TELEGRAM_PHONE : str (opcional, si se requiere para inicio de sesión)
"""

import os
import sys
import asyncio
from pathlib import Path

try:
    from telethon import TelegramClient
except ImportError:
    print("❌ La librería 'telethon' no está instalada.")
    print("👉 Puedes instalarla ejecutando: pip install telethon")
    sys.exit(1)


async def send_saved_message(
    message: str = "Mensaje de prueba desde Atlas",
    session_name: str = "atlas_telegram_session"
) -> bool:
    api_id_raw = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")

    if not api_id_raw or not api_hash:
        print("ℹ️ No se detectaron las variables de entorno TELEGRAM_API_ID y/math/o TELEGRAM_API_HASH.")
        print("Por favor ingrésalas a continuación:")
        if not api_id_raw:
            api_id_raw = input("Introduce tu TELEGRAM_API_ID: ").strip()
        if not api_hash:
            api_hash = input("Introduce tu TELEGRAM_API_HASH: ").strip()

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print("❌ Error: TELEGRAM_API_ID debe ser un número entero.")
        return False

    phone = os.environ.get("TELEGRAM_PHONE")

    print(f"🔄 Conectando a Telegram con la sesión '{session_name}'...")
    client = TelegramClient(session_name, api_id, api_hash)

    try:
        await client.start(phone=phone if phone else None)
        print("✅ Autenticación exitosa.")

        # Enviar mensaje a 'me' (Saved Messages / Mensajes guardados)
        sent_msg = await client.send_message("me", message)
        print(f"🚀 Mensaje enviado con éxito a 'Mensajes guardados' (ID: {sent_msg.id}):")
        print(f"   \"{message}\"")
        return True
    except Exception as e:
        print(f"❌ Error al enviar mensaje por Telegram: {e}")
        return False
    finally:
        await client.disconnect()


def main():
    mensaje = "Mensaje de prueba desde Atlas"
    if len(sys.argv) > 1:
        mensaje = " ".join(sys.argv[1:])

    asyncio.run(send_saved_message(message=mensaje))


if __name__ == "__main__":
    main()
