import os, time
from google import genai
from dotenv import load_dotenv
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                      http_options=types.HttpOptions(api_version="v1beta"))

STT_MODELS = [os.environ["GEMINI_API_KEY"]] + [m.strip() for m in os.environ.get("FALLBACK_MODELS", "").split(",") if m.strip()]

def stt_llm(path, retries=3):
    with open(path, 'rb') as f:
        audio= f.read()

    for attempt in range(retries):
        model = STT_MODELS[min(attempt, len(STT_MODELS) - 1)]
        try:
            response = client.models.generate_content(model=model, contents=["Transcibe the content only, don't add any commentary.",
                    types.Part.from_bytes(data=audio, mime_type="audio/wav")]
            )
            return response.text
        except Exception as e:
            print(f'Error : {e}')
            if attempt < retries-1:
                wait= 2*(attempt+1)
                time.sleep(wait)
    raise RuntimeError ("Transcription failed.")

if __name__ == "__main__":
    input_file= r'D:\Projects\VoiceDesk\speech.wav'
    print(stt_llm(input_file))