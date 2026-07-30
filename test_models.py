import os
import google.genai as genai_module
client = genai_module.Client()

print("Modelos que soportan búsqueda:")
for model in client.models.list():
    if "flash" in model.name or "pro" in model.name:
        print(model.name)
