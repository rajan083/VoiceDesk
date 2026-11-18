import os, wave
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
client= genai.Client(api_key= os.environ['GEMINI_API_KEY'])
model= "gemini-3.8-flash-lite-tts"

def tts(prompt, filename="speech.wav", voice="Kore"):
    response= client.models.generate_content(
        model=model, contents=prompt, 
        config= types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),                                        
        )
    )
    if response.usage_metadata:
        usage= response.usage_metadata
    pcm= response.candidates[0].content.parts[0].inline_data.data
    with wave.open(filename, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(pcm)
    
    return (filename,
            f"Prompt tokens: {usage.prompt_token_count}",
            f"Output tokens: {usage.candidates_token_count}",
            f"Total tokens: {usage.total_token_count}"
            )

prompt= input("You: ")
print(f'Prompt: {prompt}')
print({f'Response: {tts(prompt)}'})