with open("/home/jonathan/proyectos/voice_agent/toggle_jarvis.sh", "r") as f:
    content = f.read()

old_key = """# Usamos bash puro para evitar problemas de grep/cut
KEY=$(bash -c "source ~/.bashrc 2>/dev/null; source ~/.profile 2>/dev/null; echo \\$GEMINI_API_KEY")"""

new_key = """# Extraer la clave literalmente del archivo bashrc
KEY=$(grep "export GEMINI_API_KEY" ~/.bashrc | tail -1 | awk -F '"' '{print $2}')
"""

content = content.replace(old_key, new_key)

with open("/home/jonathan/proyectos/voice_agent/toggle_jarvis.sh", "w") as f:
    f.write(content)
