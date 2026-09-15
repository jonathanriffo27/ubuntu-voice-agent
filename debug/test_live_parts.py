import asyncio
import os
from google import genai
from google.genai import types

async def test_live():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("NO GEMINI_API_KEY")
        return
        
    client = genai.Client(api_key=api_key)
    
    config1 = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
    )
    print("Connecting with response_modalities=[AUDIO]...")
    try:
        async with client.aio.live.connect(model="gemini-2.0-flash-exp", config=config1) as session:
            await session.send(input="Hola, responde 'Prueba de audio'", end_of_turn=True)
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc:
                    mt = getattr(sc, "model_turn", None)
                    if mt:
                        for p in mt.parts:
                            print("Part keys:", [k for k in dir(p) if not k.startswith("_")], "text:", getattr(p, "text", None), "has_inline_data:", getattr(p, "inline_data", None) is not None)
                    if getattr(sc, "turn_complete", False):
                        print("Turn complete received!")
                        break
    except Exception as e:
        print("Error with [AUDIO]:", e)

    print("\nTrying with response_modalities=[AUDIO, TEXT] or check if allowed...")
    try:
        config2 = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO, types.Modality.TEXT],
        )
        async with client.aio.live.connect(model="gemini-2.0-flash-exp", config=config2) as session:
            await session.send(input="Hola, responde 'Prueba dual'", end_of_turn=True)
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc:
                    mt = getattr(sc, "model_turn", None)
                    if mt:
                        for p in mt.parts:
                            print("Dual Part text:", getattr(p, "text", None), "has_inline_data:", getattr(p, "inline_data", None) is not None)
                    if getattr(sc, "turn_complete", False):
                        print("Dual Turn complete received!")
                        break
    except Exception as e:
        print("Error with [AUDIO, TEXT]:", e)

if __name__ == "__main__":
    asyncio.run(test_live())
