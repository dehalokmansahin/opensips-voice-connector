#!/usr/bin/env python
"""
Test script for direct WebSocket connection to Vosk server with audio file
"""

import asyncio
import logging
import sys
import os
import wave
import json
import websockets
import audioop

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_vosk_with_wav(audio_file_path, vosk_url="ws://localhost:2700"):
    """Test sending a WAV file directly to Vosk server via WebSocket"""
    
    logger.info(f"Testing Vosk with audio file: {audio_file_path}")
    
    try:
        # Open and validate WAV file
        with wave.open(audio_file_path, 'rb') as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            
            logger.info(f"Audio file: {audio_file_path}")
            logger.info(f"Channels: {channels}, Sample width: {sample_width} bytes")
            logger.info(f"Sample rate: {frame_rate} Hz, Duration: {frames/frame_rate:.2f} seconds")
            
            # Vosk server expects 16000 Hz from config.ini
            target_sample_rate = 16000
            logger.info(f"Vosk expects sample rate: {target_sample_rate} Hz")
            needs_resample = frame_rate != target_sample_rate
            if needs_resample:
                logger.info(f"Will resample from {frame_rate} Hz to {target_sample_rate} Hz")
            
            # Connect to Vosk WebSocket server
            async with websockets.connect(vosk_url) as websocket:
                logger.info(f"Connected to Vosk server at {vosk_url}")
                
                # Send configuration message with the target sample rate
                config = {
                    "config": {
                        "sample_rate": target_sample_rate
                    }
                }
                await websocket.send(json.dumps(config))
                logger.info(f"Sent configuration: {config}")
                
                # Create a task to receive messages
                receive_task = asyncio.create_task(receive_messages(websocket))
                
                # Read audio in larger chunks (40ms instead of 20ms)
                chunk_samples = int(frame_rate * 0.04)  # 40ms of samples
                chunk_size = chunk_samples * channels * sample_width
                chunk_count = 0
                
                # Wait a bit before starting to send audio
                await asyncio.sleep(0.5)
                
                while True:
                    audio_chunk = wav_file.readframes(chunk_samples)
                    if not audio_chunk:
                        break
                    
                    # Handle audio format conversions if needed
                    if needs_resample:
                        # Convert to mono if stereo
                        if channels == 2:
                            audio_chunk = audioop.tomono(audio_chunk, sample_width, 0.5, 0.5)
                        
                        # Resample to target rate
                        audio_chunk = audioop.ratecv(
                            audio_chunk, 
                            sample_width, 
                            1, 
                            frame_rate, 
                            target_sample_rate, 
                            None
                        )[0]
                    
                    # Only use mono channel for Vosk
                    elif channels == 2:
                        audio_chunk = audioop.tomono(audio_chunk, sample_width, 0.5, 0.5)
                    
                    # Send the processed audio chunk
                    await websocket.send(audio_chunk)
                    chunk_count += 1
                    
                    if chunk_count % 25 == 0:
                        logger.info(f"Sent {chunk_count} chunks ({len(audio_chunk)} bytes in last chunk)")
                    
                    # Match real-time audio flow (40ms chunks need 40ms delay)
                    await asyncio.sleep(0.04)
                    
                    # Add additional pause every 10 chunks to let server catch up
                    if chunk_count % 10 == 0:
                        await asyncio.sleep(0.02)  # Extra 20ms pause
                
                logger.info(f"Finished sending {chunk_count} audio chunks")
                
                # Wait a moment before sending EOF
                await asyncio.sleep(0.5)
                
                # Send EOF to finalize
                await websocket.send(json.dumps({"eof": 1}))
                logger.info("Sent EOF marker")
                
                # Wait for final results
                await asyncio.sleep(3)
                
                # Cancel the receive task
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                
                logger.info("Test completed successfully")
                return True
                
    except Exception as e:
        logger.error(f"Error testing Vosk: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def receive_messages(websocket):
    """Receive and process messages from the Vosk server"""
    try:
        while True:
            message = await websocket.recv()
            try:
                result = json.loads(message)
                if "text" in result and result["text"]:
                    logger.info(f"💬 Transcription: {result['text']}")
                elif "partial" in result and result["partial"]:
                    if len(result["partial"]) > 5:  # Only log meaningful partials
                        logger.info(f"🔄 Partial: {result['partial']}")
                else:
                    logger.info(f"Received: {message}")
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {message}")
    except asyncio.CancelledError:
        logger.info("Receive task cancelled")
        raise
    except Exception as e:
        logger.error(f"Error in receive task: {e}")

async def main():
    """Run the test"""
    # Get audio file path from command line or use default
    audio_file = sys.argv[1] if len(sys.argv) > 1 else "test_l16_16k.wav"
    
    if not os.path.exists(audio_file):
        logger.error(f"Audio file not found: {audio_file}")
        sys.exit(1)
    
    logger.info("Starting Vosk WebSocket test")
    result = await test_vosk_with_wav(audio_file)
    
    if result:
        logger.info("✅ Vosk test successful!")
    else:
        logger.error("❌ Vosk test failed!")
    
    # Exit with appropriate code
    sys.exit(0 if result else 1)

if __name__ == "__main__":
    asyncio.run(main()) 