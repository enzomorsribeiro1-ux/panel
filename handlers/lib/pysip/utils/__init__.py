import asyncio
import io
import re
from gtts import gTTS
from pydub.audio_segment import AudioSegment
from pydub.utils import which

# Set ffmpeg path for pydub
AudioSegment.converter = which("ffmpeg") or "ffmpeg"
AudioSegment.ffmpeg = which("ffmpeg") or "ffmpeg"
AudioSegment.ffprobe = which("ffprobe") or "ffprobe"


def mp3_to_wav(f, target_channels=1, target_framerate=8000):
    """
    Convert MP3 audio from the temporary chunk to WAV format.
    Falls back to simple WAV generation if pydub fails.
    """
    try:
        # Ensure we're at the beginning of the file
        f.seek(0)
        
        # Load audio
        decoded_chunk = AudioSegment.from_mp3(f)
        decoded_chunk = decoded_chunk.set_channels(target_channels)
        decoded_chunk = decoded_chunk.set_frame_rate(target_framerate)
        
        # Use temporary file approach (BytesIO has issues with pydub)
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_path = temp_file.name
        
        try:
            # Export to WAV file
            decoded_chunk.export(temp_path, format="wav")
            
            # Read the file into BytesIO
            with open(temp_path, 'rb') as wav_file:
                wav_data = wav_file.read()
            
            output_chunk = io.BytesIO(wav_data)
            size = len(wav_data)
            
            print(f"[DEBUG] MP3 to WAV conversion: {len(decoded_chunk.raw_data)} raw bytes -> {size} wav bytes")
            return output_chunk
            
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass
                
    except Exception as e:
        print(f"[ERROR] MP3 to WAV conversion failed: {e}")
        print(f"[FALLBACK] Creating simple WAV from MP3 data")
        # Create a simple WAV file as fallback
        return create_simple_wav_from_mp3(f, target_framerate)

def create_simple_wav_from_mp3(mp3_data, sample_rate=8000):
    """
    Create a simple WAV file from MP3 data without using pydub.
    This is a fallback when pydub conversion fails.
    """
    import wave
    import struct
    
    # Create a simple WAV file with silence (since we can't decode MP3 without ffmpeg)
    duration_seconds = 3.0  # Default duration
    num_samples = int(sample_rate * duration_seconds)
    
    # Create silence (all zeros)
    silence_data = b'\x00' * (num_samples * 2)  # 16-bit samples
    
    # Create WAV file in memory
    wav_buffer = io.BytesIO()
    
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence_data)
    
    wav_buffer.seek(0)
    print(f"[FALLBACK] Created simple WAV: {wav_buffer.tell()} bytes")
    return wav_buffer


async def generate_audio(text: str, voice: str) -> io.BytesIO:
    """
    this generates the real TTS using gtts for this part.
    Falls back to silence if TTS fails.
    """
    try:
        print(f"[DEBUG] Generating audio for text: '{text[:50]}...' with voice: {voice}")
        
        # Use gTTS (Google Text-to-Speech)
        tts = gTTS(text=text, lang='en', slow=False)
        temp_chunk = io.BytesIO()
        
        # Generate audio in a thread to avoid blocking
        await asyncio.to_thread(tts.write_to_fp, temp_chunk)
        
        if temp_chunk.tell() == 0:
            raise Exception("No audio was received. Please verify that your parameters are correct.")
            
        print(f"[DEBUG] Audio generation completed, size: {temp_chunk.tell()} bytes")
        
        # Convert MP3 to WAV - this is required for RTP
        try:
            await asyncio.to_thread(temp_chunk.seek, 0)
            decoded_audio = await asyncio.to_thread(mp3_to_wav, temp_chunk)
            # Check if we got valid audio data
            decoded_audio.seek(0, 2)  # Seek to end
            size = decoded_audio.tell()
            decoded_audio.seek(0)  # Reset to beginning
            
            if size > 0:
                print(f"[DEBUG] Audio conversion completed, final size: {size} bytes")
                return decoded_audio
            else:
                print(f"[WARNING] WAV conversion failed, creating silence instead")
                return create_silence_audio(text, 3.0)
        except Exception as conv_e:
            print(f"[WARNING] Audio conversion failed: {conv_e}, creating silence instead")
            return create_silence_audio(text, 3.0)
        
    except Exception as e:
        print(f"[ERROR] Audio generation failed: {e}")
        print(f"[FALLBACK] Creating silence audio instead of TTS")
        # Create a fallback silence audio
        return create_silence_audio(text)

def create_silence_audio(text: str, duration_seconds: float = 2.0) -> io.BytesIO:
    """
    Create a silence audio file as fallback when TTS fails.
    """
    import wave
    import struct
    
    # Create a simple silence WAV file
    sample_rate = 8000
    num_samples = int(sample_rate * duration_seconds)
    
    # Create silence (all zeros)
    silence_data = b'\x00' * (num_samples * 2)  # 16-bit samples
    
    # Create WAV file in memory
    wav_buffer = io.BytesIO()
    
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence_data)
    
    wav_buffer.seek(0)
    print(f"[FALLBACK] Created {duration_seconds}s silence audio for: '{text[:30]}...'")
    return wav_buffer


def get_caller_number(invite_message):
    """Extract phone number from SIP INVITE From header
    Returns: phone number as string or None"""

    from_header = invite_message.headers.get("From", "")

    # Look for number pattern after "sip:"
    match = re.search(r"sip:(\+?\d+)@", from_header)
    if match:
        return match.group(1)

    # Alternate: look for number in display name
    match = re.search(r'"(\+?\d+)".*<sip:', from_header)
    if match:
        return match.group(1)

    return None
