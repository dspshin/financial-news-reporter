import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("GEMINI_API_KEY is not configured.")

client = genai.Client(api_key=api_key)

print("Listing available models...")
try:
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            print(f"- Name: {model.name}")
except Exception as e:
    raise SystemExit(f"Error listing models: {e}")
