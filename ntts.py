import asyncio
import edge_tts
import io
from pydub import AudioSegment

async def save_tts_to_wav(text: str, filename: str, voice: str = "en-US-AriaNeural"):
    communicate = edge_tts.Communicate(text, voice)
    mp3_buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_buffer.write(chunk["data"])
    mp3_buffer.seek(0)
    audio = AudioSegment.from_mp3(mp3_buffer)
    audio = audio.set_channels(1).set_frame_rate(8000)
    audio.export(filename, format="wav")
    print(f"Saved: {filename}")

asyncio.run(save_tts_to_wav("Hello, this is an automated call from the Google Trust and Security Team. We are reaching out regarding a recent number change request for your Google account. If you did not initiate this request and need to secure your account, please press 1.", "first_script.wav"))
asyncio.run(save_tts_to_wav("Thank you for pressing 1. A representative will contact you shortly. We appreciate you using Google.", "second_script.wav"))