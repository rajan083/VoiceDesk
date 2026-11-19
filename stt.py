import os, time
from google import genai
from dotenv import load_dotenv
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                      http_options=types.HttpOptions(api_version="v1beta"))
STT_MODEL = "gemini-3.1-flash-lite"

def stt_llm(path, retries=3):
    with open(path, 'rb') as f:
        audio= f.read()

    for attempt in range(retries):
        try:
            response= client.models.generate_content(
                model=STT_MODEL, contents=["Transcibe the content only, don't add any commentary.",
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