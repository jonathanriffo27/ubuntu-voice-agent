#!/usr/bin/env python3
"""Diagnóstico de búsqueda web para Jarvis — muestra errores detallados de cada método."""
import os
import sys
import json
import traceback

query = "tiempo en Puerto Natales"

# --- 1. Test API Keys con Gemini REST ---
print("=" * 60)
print("1. TESTING GEMINI REST API (con Google Search grounding)")
print("=" * 60)

api_keys = []
if os.environ.get("GEMINI_API_KEY"):
    api_keys.append(("ENV GEMINI_API_KEY", os.environ["GEMINI_API_KEY"]))

# Extraer de bashrc
try:
    import subprocess
    bashrc_key = subprocess.run(
        "grep 'export GEMINI_API_KEY' ~/.bashrc | tail -1 | awk -F '\"' '{print $2}'",
        shell=True, capture_output=True, text=True
    ).stdout.strip()
    if bashrc_key:
        api_keys.append(("BASHRC key", bashrc_key))
except Exception:
    pass

# Fallback keys del código
fallback_keys = [
    "os.getenv("GEMINI_API_KEY", "your-api-key")",
    "os.getenv("GEMINI_API_KEY", "your-api-key")",
    "os.getenv("GEMINI_API_KEY", "your-api-key")",
    "os.getenv("GEMINI_API_KEY", "your-api-key")"
]
for i, k in enumerate(fallback_keys):
    api_keys.append((f"FALLBACK_{i+1}", k))

print(f"\nTotal de API keys a probar: {len(api_keys)}\n")

import urllib.request
import urllib.error

for key_name, api_key in api_keys:
    print(f"\n--- Key: {key_name} ({api_key[:12]}...{api_key[-4:]}) ---")
    for model_name in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            data = {
                "contents": [{"parts": [{"text": query}]}],
                "tools": [{"googleSearch": {}}]
            }
            req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode('utf-8'))
                candidates = result.get('candidates', [])
                if candidates:
                    text = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    grounding = candidates[0].get('groundingMetadata', {})
                    chunks = grounding.get('groundingChunks', [])
                    print(f"  ✅ {model_name}: OK! ({len(text)} chars, {len(chunks)} fuentes)")
                    print(f"     Preview: {text[:150]}...")
                    break  # Success with this key, no need to try other models
                else:
                    print(f"  ⚠️ {model_name}: Respuesta sin candidates")
                    print(f"     Response: {json.dumps(result)[:200]}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            print(f"  ❌ {model_name}: HTTP {e.code}")
            print(f"     Error: {error_body[:200]}")
            if e.code == 404:
                continue  # try next model
            else:
                break  # 429 or other error, skip this key
        except Exception as e:
            print(f"  ❌ {model_name}: {type(e).__name__}: {e}")
            break

# --- 2. Test MCP ---
print("\n" + "=" * 60)
print("2. TESTING MCP GEMINI SEARCH")
print("=" * 60)

node_path = "/home/jonathan/.nvm/versions/node/v20.20.2/bin/node"
script_path = "/home/jonathan/.nvm/versions/node/v20.20.2/lib/node_modules/mcp-gemini-google-search/dist/index.js"

if not os.path.exists(node_path):
    print(f"  ❌ Node no encontrado en: {node_path}")
elif not os.path.exists(script_path):
    print(f"  ❌ MCP script no encontrado en: {script_path}")
else:
    print(f"  Node: {node_path}")
    print(f"  Script: {script_path}")
    
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        try:
            mcp_env = os.environ.copy()
            mcp_env["GEMINI_API_KEY"] = api_key
            mcp_env["GEMINI_MODEL"] = "gemini-2.0-flash"
            
            p = subprocess.Popen([node_path, script_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=mcp_env)
            
            # Initialize
            init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}}
            p.stdin.write(json.dumps(init_req) + "\n")
            p.stdin.flush()
            init_resp = p.stdout.readline()
            print(f"  Init response: {init_resp.strip()[:200]}")
            
            # Notification
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            p.stdin.flush()
            
            # Call tool
            call_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "google_search", "arguments": {"query": query}}}
            p.stdin.write(json.dumps(call_req) + "\n")
            p.stdin.flush()
            
            import select
            # Wait up to 15 seconds for response
            import time
            start = time.time()
            response_line = ""
            while time.time() - start < 15:
                if select.select([p.stdout], [], [], 0.5)[0]:
                    response_line = p.stdout.readline()
                    break
            
            if response_line:
                resp_data = json.loads(response_line)
                if "result" in resp_data:
                    content = resp_data["result"].get("content", [])
                    if content:
                        text = content[0].get("text", "")
                        print(f"  ✅ MCP search OK! ({len(text)} chars)")
                        print(f"     Preview: {text[:200]}...")
                    else:
                        print(f"  ⚠️ MCP: resultado vacío")
                elif "error" in resp_data:
                    print(f"  ❌ MCP error: {json.dumps(resp_data['error'])[:300]}")
                else:
                    print(f"  ⚠️ MCP respuesta inesperada: {response_line[:300]}")
            else:
                stderr = p.stderr.read() if select.select([p.stderr], [], [], 0.1)[0] else ""
                print(f"  ❌ MCP: No response in 15 seconds")
                if stderr:
                    print(f"     Stderr: {stderr[:300]}")
            
            p.terminate()
            p.wait()
        except Exception as e:
            print(f"  ❌ MCP exception: {e}")
            traceback.print_exc()

# --- 3. Test DDGS ---
print("\n" + "=" * 60)
print("3. TESTING DUCKDUCKGO SEARCH (ddgs)")
print("=" * 60)

try:
    from ddgs import DDGS
    print("  ✅ ddgs module importado correctamente")
    
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            if results:
                print(f"  ✅ DDGS search OK! ({len(results)} resultados)")
                for i, r in enumerate(results):
                    print(f"     {i+1}. {r.get('title', '?')[:60]}")
            else:
                print("  ⚠️ DDGS: 0 resultados")
    except Exception as e:
        print(f"  ❌ DDGS search error: {type(e).__name__}: {e}")
        traceback.print_exc()
except ImportError:
    print("  ❌ ddgs NO está instalado")
    print("     Para instalar: pip install ddgs")

print("\n" + "=" * 60)
print("DIAGNÓSTICO COMPLETO")
print("=" * 60)
