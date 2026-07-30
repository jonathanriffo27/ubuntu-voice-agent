import sys
import os
sys.path.append(os.getcwd())
from tools import buscar_en_internet

# Remove GEMINI_API_KEY to test the fallback extraction logic
if "GEMINI_API_KEY" in os.environ:
    del os.environ["GEMINI_API_KEY"]

print(buscar_en_internet("tiempo actual en Puerto Natales"))
