import urllib.request
import json
import os

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    api_key = os.getenv("GEMINI_API_KEY", "your-api-key")

url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
req = urllib.request.Request(url)

try:
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        models = result.get('models', [])
        for m in models:
            name = m.get('name')
            methods = m.get('supportedGenerationMethods', [])
            if "generateContent" in methods:
                 print(f"{name}: {methods}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode('utf-8')[:100]}...")
