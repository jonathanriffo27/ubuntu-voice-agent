import subprocess
import json
import os
import sys

query = "tiempo actual en Puerto Natales"
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    api_key = "os.getenv("GEMINI_API_KEY", "your-api-key")"

node_path = "/home/jonathan/.nvm/versions/node/v20.20.2/bin/node"
script_path = "/home/jonathan/.nvm/versions/node/v20.20.2/lib/node_modules/mcp-gemini-google-search/dist/index.js"

mcp_env = os.environ.copy()
mcp_env["GEMINI_API_KEY"] = api_key
mcp_env["GEMINI_MODEL"] = "gemini-2.0-flash" 

print(f"Iniciando MCP con modelo {mcp_env['GEMINI_MODEL']}")
p = subprocess.Popen([node_path, script_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=mcp_env)

try:
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "jarvis-mcp", "version": "1.0"}}}
    p.stdin.write(json.dumps(init_req) + "\n")
    p.stdin.flush()
    print("Init response:", p.stdout.readline().strip())
    
    init_notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    p.stdin.write(json.dumps(init_notif) + "\n")
    p.stdin.flush()
    
    call_req = {
        "jsonrpc": "2.0", 
        "id": 2, 
        "method": "tools/call", 
        "params": {
            "name": "google_search",
            "arguments": {"query": query}
        }
    }
    p.stdin.write(json.dumps(call_req) + "\n")
    p.stdin.flush()
    
    resp = p.stdout.readline().strip()
    print("Tool response:", resp)
    
finally:
    p.terminate()
    p.wait()
