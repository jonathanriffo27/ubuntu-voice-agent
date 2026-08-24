import re

with open("/home/jonathan/proyectos/voice_agent/toggle_jarvis.sh", "r") as f:
    content = f.read()

# Fix the grep regex so it correctly captures the API key whether it has export or not
old_grep = 'KEY=$(grep -h "GEMINI_API_KEY=" ~/.bashrc ~/.profile ~/.bash_profile 2>/dev/null | tail -1 | cut -d\'"\' -f2 | cut -d"\'" -f2 | cut -d\'=\' -f2-)'

new_grep = """# Usamos bash puro para evitar problemas de grep/cut
KEY=$(bash -c "source ~/.bashrc 2>/dev/null; source ~/.profile 2>/dev/null; echo \\$GEMINI_API_KEY")"""

content = content.replace(old_grep, new_grep)

with open("/home/jonathan/proyectos/voice_agent/toggle_jarvis.sh", "w") as f:
    f.write(content)
