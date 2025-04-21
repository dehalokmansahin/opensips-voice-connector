#!/usr/bin/env python
"""
Simple test script for Vosk WebSocket connection
"""

import asyncio
import logging
import websockets
import json
import sys
import os
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_vosk_connection():
    """Test connecting to Vosk server and sending a simple configuration"""
    vosk_url = os.environ.get("VOSK_URL", "ws://localhost:2700")
    logger.info(f"Connecting to Vosk server at {vosk_url}")
    
    try:
        # Connect to Vosk WebSocket server with timeout
        websocket = await asyncio.wait_for(
            websockets.connect(vosk_url),
            timeout=5.0
        )
        logger.info("Connected to Vosk server!")
        
        # Send configuration
        config = {
            "config": {
                "sample_rate": 16000
            }
        }
        await websocket.send(json.dumps(config))
        logger.info(f"Sent configuration: {config}")
        
        # Send a small sample of empty audio (silence)
        # Generate 0.1 seconds of silence at 16000Hz (16-bit PCM)
        sample_count = int(16000 * 0.1)  # 0.1 seconds of audio
        empty_audio = bytes(sample_count * 2)  # 2 bytes per sample for 16-bit PCM
        
        await websocket.send(empty_audio)
        logger.info(f"Sent {len(empty_audio)} bytes of empty audio")
        
        # Send EOF to get empty response and close
        await websocket.send(json.dumps({"eof": 1}))
        logger.info("Sent EOF")
        
        # Get response with timeout
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
            logger.info(f"Received: {response}")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for response from Vosk server")
        
        # Close connection
        await websocket.close()
        logger.info("Connection closed")
        
        return True
        
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"WebSocket connection closed: {e.code} {e.reason}")
        return False
    except asyncio.TimeoutError:
        logger.error("Timeout connecting to Vosk server")
        return False
    except Exception as e:
        logger.error(f"Error testing Vosk connection: {e}")
        logger.error(traceback.format_exc())
        return False

async def main():
    """Run the test"""
    logger.info("Starting Vosk connection test")
    result = await test_vosk_connection()
    
    if result:
        logger.info("✅ Vosk connection test successful!")
        logger.info("Your Vosk server is working and accessible.")
        logger.info("The VoskSTT module should be able to connect to it.")
    else:
        logger.error("❌ Vosk connection test failed!")
        logger.error("Please check if your Vosk server is running at the correct address.")
    
    # Exit with appropriate code
    sys.exit(0 if result else 1)

if __name__ == "__main__":
    asyncio.run(main()) 