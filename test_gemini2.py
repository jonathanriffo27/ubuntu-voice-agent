import os
import urllib.request
import json

api_key = "os.getenv("GEMINI_API_KEY", "your-api-key")"

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
data = {
    "contents": [{"parts": [{"text": "tiempo actual en Puerto Natales"}]}],
    "tools": [{"googleSearch": {}}]
}

req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        
        candidates = result.get('candidates', [])
        if candidates:
            content = candidates[0].get('content', {}).get('parts', [])[0].get('text', '')
            print("Result:", content)
        else:
            print("No candidates:", result)
except Exception as e:
    print("API Error:", e)
    if hasattr(e, 'read'):
        print(e.read().decode('utf-8'))
