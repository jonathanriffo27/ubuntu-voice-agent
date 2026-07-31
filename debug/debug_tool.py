import sys
import os
sys.path.append(os.getcwd())
from tools import buscar_en_internet

print("Llamando a buscar_en_internet...")
result = buscar_en_internet("tiempo actual en Puerto Natales")
print(f"Resultado final: {result}")
