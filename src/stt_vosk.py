#!/usr/bin/env python
#
# Copyright (C) 2024 SIP Point Consulting SRL - adapted for Vosk
#
# This file is part of the OpenSIPS AI Voice Connector project
# (see https://github.com/OpenSIPS/opensips-ai-voice-connector-ce).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
Module that implements Vosk WebSocket communication for STT
"""

import logging
import asyncio
import json
import websockets  # Import the websockets library

from ai import AIEngine
from config import Config
from codec import get_codecs, CODECS, UnsupportedCodec

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VoskSTT(AIEngine):
    """ Implements Vosk WebSocket communication """

    def __init__(self, call, cfg):
        """ Initializes the Vosk STT engine """
        logger.info("Initializing Vosk STT Engine")
        self.cfg = Config.get("vosk", cfg)
        self.call = call  # Store call reference if needed later
        self.b2b_key = call.b2b_key # Store B2B key for identification

        # --- Configuration ---
        self.vosk_server_url = self.cfg.get("url", "VOSK_URL")
        # Default to 8000Hz if not specified, Vosk often uses 8k or 16k
        self.sample_rate = int(self.cfg.get("sample_rate", "VOSK_SAMPLE_RATE", 8000))
        # Add more Vosk specific config options here if needed (e.g., model)

        if not self.vosk_server_url:
            logger.error("Vosk server URL is not configured. Please set 'url' in the [vosk] section or VOSK_URL env var.")
            raise ValueError("Vosk server URL not configured")

        logger.info(f"Vosk Config: URL={self.vosk_server_url}, SampleRate={self.sample_rate}")

        # --- State ---
        self.codec = self.choose_codec(call.sdp) # Determine codec early
        logger.info(f"Chosen Codec: {self.codec.name}@{self.codec.sample_rate}Hz (Target Vosk Rate: {self.sample_rate}Hz)")

        self.websocket: websockets.WebSocketClientProtocol | None = None
        self.connection_task = None
        self.receive_task = None
        self.send_queue = asyncio.Queue() # Queue for audio data to send
        self.transcription_queue = asyncio.Queue() # Queue for received transcriptions
        self.is_active = False
        self.stop_event = asyncio.Event() # To signal stopping tasks


        # Placeholder for potential integration with a ChatGPT or similar component
        # self.chat_handler = ... # Initialize if needed based on project pattern

        logger.info(f"VoskSTT initialized for call {self.b2b_key}")


    def choose_codec(self, sdp):
        """ Chooses the preferred codec, prioritizing those easily convertible to PCM for Vosk """
        codecs = get_codecs(sdp)
        cmap = {c.name.lower(): c for c in codecs}

        # Vosk typically requires raw PCM (L16). PCMU/PCMA are easily convertible.
        preferred_codecs = ["pcmu", "pcma"] # G.711 mu-law and A-law
        
        for codec_name in preferred_codecs:
            if codec_name in cmap:
                selected_codec = CODECS[codec_name](cmap[codec_name])
                # Ensure the codec class has a decode method if needed
                if not hasattr(selected_codec, 'decode'):
                     logger.warning(f"Codec {codec_name} selected but has no decode method in codec.py!")
                     # Decide if this is a fatal error or if you can proceed assuming raw passthorugh
                     # For now, let's assume it's okay if sample rates match target
                     # if selected_codec.sample_rate != self.sample_rate:
                     #     continue # Or raise error if conversion impossible

                logger.info(f"Selected codec based on SDP: {codec_name}")
                return selected_codec
        
        # If no preferred codec is found, check if Opus is available and if we *could* decode it (requires external lib)
        # if "opus" in cmap:
        #     # Check if opus decoding is implemented/possible in codec.py or opus.py
        #     logger.warning("Opus found, but direct use/decoding for Vosk PCM needs verification/implementation.")
        #     # Potentially return Opus codec if handling is implemented, otherwise fallback or error

        # Fallback or error if no suitable codec
        logger.error(f"No suitable codec found in SDP for Vosk. Available: {list(cmap.keys())}. Need PCMU/PCMA or PCM compatible.")
        raise UnsupportedCodec(f"No suitable codec (PCMU/PCMA) found for Vosk in SDP: {list(cmap.keys())}")


    async def _connect_and_manage(self):
        """ Internal task to manage WebSocket connection, send/receive loops. """
        while self.is_active and not self.stop_event.is_set():
            try:
                logger.info(f"Connecting to Vosk server at {self.vosk_server_url}")
                self.websocket = await websockets.connect(self.vosk_server_url)
                logger.info(f"Connected to Vosk server for call {self.b2b_key}")
                
                # Send configuration message immediately after connecting
                config_message = json.dumps({
                    "config": {
                        "sample_rate": self.sample_rate
                    }
                })
                await self.websocket.send(config_message)
                logger.info(f"Sent configuration to Vosk: {config_message}")
                
                # Start send and receive tasks
                self.send_task = asyncio.create_task(self._send_loop())
                self.receive_task = asyncio.create_task(self._receive_loop())
                
                # Wait for either task to complete - if one fails, we'll reconnect
                done, pending = await asyncio.wait(
                    [self.send_task, self.receive_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel the pending task
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Error during task cleanup for call {self.b2b_key}: {e}")
                
                # Check why tasks completed
                for task in done:
                    try:
                        await task  # This will re-raise any exception that killed the task
                    except asyncio.CancelledError:
                        logger.info(f"Task was cancelled for call {self.b2b_key}")
                    except Exception as e:
                        logger.error(f"Task failed with error for call {self.b2b_key}: {e}")
                
                if self.stop_event.is_set():
                    logger.info(f"Stop event set, not reconnecting for call {self.b2b_key}")
                    break
                    
                logger.warning(f"One of the WebSocket tasks ended, will reconnect for call {self.b2b_key}")
                
            except websockets.exceptions.ConnectionClosed as e:
                if self.stop_event.is_set():
                    logger.info(f"Connection closed while stopping, exiting for call {self.b2b_key}")
                    break
                logger.warning(f"Vosk WebSocket connection closed for call {self.b2b_key}: {e}")
            except Exception as e:
                if self.stop_event.is_set():
                    logger.info(f"Exception while stopping, exiting for call {self.b2b_key}")
                    break
                logger.error(f"Error in Vosk WebSocket connection for call {self.b2b_key}: {e}")
            
            # Clean up the WebSocket
            if self.websocket:
                try:
                    await self.websocket.close()
                except Exception as e:
                    logger.warning(f"Error closing WebSocket for call {self.b2b_key}: {e}")
                finally:
                    self.websocket = None
            
            # If we're still active, implement reconnection backoff
            if self.is_active and not self.stop_event.is_set():
                retry_delay = 1.0  # Start with 1 second
                logger.info(f"Reconnecting to Vosk server in {retry_delay}s...")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=retry_delay)
                    if self.stop_event.is_set():
                        logger.info(f"Received stop event during reconnection delay for call {self.b2b_key}")
                        break
                except asyncio.TimeoutError:
                    # Timeout means we should retry the connection
                    pass
        
        logger.info(f"Vosk connection manager exiting for call {self.b2b_key}")

    async def _send_loop(self):
        """ Internal task to continuously send audio data from the queue """
        try:
            logger.info(f"Starting Vosk audio send loop for call {self.b2b_key}")
            
            while self.is_active and not self.stop_event.is_set() and self.websocket.open:
                try:
                    # Get the next audio chunk from the queue with a timeout
                    # This allows us to check periodically if we should exit
                    audio_data = await asyncio.wait_for(self.send_queue.get(), timeout=0.5)
                    
                    # Potentially decode the audio to match Vosk's expected format (PCM)
                    # Check if we need to decode based on the chosen codec
                    pcm_data = audio_data
                    if hasattr(self.codec, 'decode') and self.codec.sample_rate != self.sample_rate:
                        # We may need to decode and potentially also resample here
                        try:
                            pcm_data = self.codec.decode(audio_data)
                            # Resampling would happen here if needed
                        except Exception as e:
                            logger.error(f"Error decoding audio for Vosk for call {self.b2b_key}: {e}")
                            # Continue with the original data if decoding fails
                            pcm_data = audio_data
                    
                    # Send the audio data as binary to Vosk
                    await self.websocket.send(pcm_data)
                    self.send_queue.task_done()
                    
                except asyncio.TimeoutError:
                    # Just a timeout on queue.get(), continue the loop
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"WebSocket closed during send for call {self.b2b_key}: {e}")
                    # Let the _connect_and_manage method handle reconnection
                    break
                except Exception as e:
                    logger.error(f"Error in Vosk send loop for call {self.b2b_key}: {e}")
                    # Continue the loop and try again, unless it's a critical error
                    # If this error is happening repeatedly, _connect_and_manage will eventually reconnect
                    await asyncio.sleep(0.1)  # Small delay to prevent error spam
            
            # If we're shutting down, send the EOF marker to Vosk to finalize transcription
            if self.websocket and self.websocket.open and not self.stop_event.is_set():
                try:
                    logger.info(f"Sending EOF marker to Vosk for call {self.b2b_key}")
                    await self.websocket.send(json.dumps({"eof": 1}))
                except Exception as e:
                    logger.error(f"Error sending EOF to Vosk for call {self.b2b_key}: {e}")
            
            logger.info(f"Vosk audio send loop exiting for call {self.b2b_key}")
        except Exception as e:
            logger.error(f"Unexpected error in Vosk send loop for call {self.b2b_key}: {e}")
            raise  # Re-raise to let _connect_and_manage handle reconnection

    async def _receive_loop(self):
        """ Internal task to continuously receive messages from Vosk """
        try:
            logger.info(f"Starting Vosk transcription receive loop for call {self.b2b_key}")
            
            # For collecting sentence fragments over time
            # Similar to how Deepgram implementation buffers text
            sentence_buffer = []
            
            while self.is_active and not self.stop_event.is_set() and self.websocket.open:
                try:
                    # Receive the next message from Vosk
                    message = await self.websocket.recv()
                    
                    # Parse the JSON response
                    try:
                        result = json.loads(message)
                        # Debug output (can be removed or changed to debug level in production)
                        logger.info(f"Received from Vosk: {result}")
                        
                        # Process the result object: there are several possible response types
                        
                        # 1. Final text/result transcription
                        if "text" in result and result["text"]:
                            final_text = result["text"]
                            if final_text.strip():  # If not empty
                                sentence_buffer.append(final_text)
                                # If this looks like the end of a sentence
                                if final_text.endswith((".", "?", "!")):
                                    complete_sentence = " ".join(sentence_buffer)
                                    logger.info(f"Complete sentence from Vosk for call {self.b2b_key}: {complete_sentence}")
                                    
                                    # Similar to Deepgram implementation, we'll process complete sentences
                                    # We'll also trigger a handle_phrase or equivalent
                                    
                                    # Use create_task to not block the loop
                                    asyncio.create_task(self.handle_phrase(complete_sentence))
                                    
                                    # Clear the buffer after a complete sentence
                                    sentence_buffer.clear()
                        
                        # 2. Partial result (interim transcription still in progress)
                        elif "partial" in result and result["partial"]:
                            partial_text = result["partial"]
                            if logger.level <= logging.DEBUG:  # Only log partials at debug level
                                logger.debug(f"Partial from Vosk for call {self.b2b_key}: {partial_text}")
                            # We don't typically act on partial results other than possibly displaying
                            # them in a UI or monitoring. Store in the transcription queue if needed.
                            # await self.transcription_queue.put({"type": "partial", "text": partial_text})
                        
                        # Add other response types if Vosk provides them
                    
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode JSON from Vosk for call {self.b2b_key}: {e}. Message: {message}")
                    
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"WebSocket closed during receive for call {self.b2b_key}: {e}")
                    # Let the _connect_and_manage method handle reconnection
                    break
                except Exception as e:
                    logger.error(f"Error in Vosk receive loop for call {self.b2b_key}: {e}")
                    # Continue the loop and try again, unless it's a critical error
                    await asyncio.sleep(0.1)  # Small delay to prevent error spam
            
            logger.info(f"Vosk transcription receive loop exiting for call {self.b2b_key}")
        except Exception as e:
            logger.error(f"Unexpected error in Vosk receive loop for call {self.b2b_key}: {e}")
            raise  # Re-raise to let _connect_and_manage handle reconnection

    # Helper method to process transcribed text, similar to Deepgram's handle_phrase
    async def handle_phrase(self, phrase):
        """ Handles the complete transcribed phrase """
        logger.info(f"Handling phrase from Vosk for call {self.b2b_key}: {phrase}")
        
        # TODO: Integrate with local LLM service instead of ChatGPT
        # TODO: Connect to a TTS service (to be determined)
        # TODO: Flow will be similar to Deepgram implementation:
        #       1. Pass the phrase to the local LLM service
        #       2. Get a response from LLM
        #       3. Convert the response to speech using TTS service
        #       4. Send the speech back to the caller
        
        # For now, just log the transcribed phrase
        return

    async def start(self):
        """ Starts the Vosk STT processing by initiating the WebSocket connection """
        logger.info(f"Starting Vosk STT for call {self.b2b_key}")
        if not self.vosk_server_url:
             logger.error("Cannot start Vosk STT: URL not configured.")
             return # Or raise an error

        self.is_active = True
        self.stop_event.clear()
        # Start the connection and processing task
        self.connection_task = asyncio.create_task(self._connect_and_manage())
        logger.info(f"Vosk STT connection manager task created for call {self.b2b_key}")
        # We might need to wait for the connection to be established here
        # await self.websocket.wait_connected() # If using a library that supports this easily
        # or use an internal event signaled by _connect_and_manage


    async def send(self, audio):
        """ Puts audio data onto the send queue for the WebSocket connection """
        if not self.is_active or self.stop_event.is_set():
            # logger.debug(f"Vosk STT not active or stopping, ignoring audio data for call {self.b2b_key}")
            return # Don't queue if not running

        if self.websocket and self.websocket.open:
             # If direct sending is preferred and connection is stable:
             # try:
             #     # Decode if necessary
             #     pcm_data = self.codec.decode(audio) if self.codec.sample_rate != self.sample_rate else audio # Basic check
             #     await self.websocket.send(pcm_data)
             # except Exception as e:
             #     logger.error(f"Error sending audio directly: {e}")
             #     # Handle error, maybe close connection or queue data
             # else:
             # Put raw audio onto the queue, decoding will happen in the sending task
             await self.send_queue.put(audio)
        else:
             # logger.warning(f"WebSocket not ready, queueing audio data for call {self.b2b_key}")
             # Queue even if connection is down, _connect_and_manage might reconnect
             await self.send_queue.put(audio)


    async def close(self):
        """ Closes the Vosk STT session and cleans up resources """
        logger.info(f"Closing Vosk STT connection for call {self.b2b_key}")
        if not self.is_active:
            return

        self.is_active = False
        self.stop_event.set() # Signal all tasks to stop

        # Gracefully stop tasks
        if self.connection_task:
            try:
                 # Give connection task a chance to send EOF and close websocket
                 await asyncio.wait_for(self.connection_task, timeout=5.0)
            except asyncio.TimeoutError:
                 logger.warning(f"Vosk connection task did not finish gracefully within timeout for call {self.b2b_key}. Cancelling.")
                 self.connection_task.cancel()
            except Exception as e:
                 logger.error(f"Error during connection task shutdown for call {self.b2b_key}: {e}")
                 self.connection_task.cancel() # Ensure cancellation on other errors

        # Explicitly close websocket if connection_task didn't
        if self.websocket and self.websocket.open:
            try:
                await self.websocket.close(code=1000, reason='Client closing')
                logger.info(f"Vosk WebSocket closed explicitly for call {self.b2b_key}")
            except Exception as e:
                logger.error(f"Error closing Vosk WebSocket for call {self.b2b_key}: {e}")

        self.websocket = None
        self.connection_task = None
        self.receive_task = None
        # Clear queues? Decide based on desired behavior on close.
        # while not self.send_queue.empty(): self.send_queue.get_nowait()
        # while not self.transcription_queue.empty(): self.transcription_queue.get_nowait()

        logger.info(f"Vosk STT resources cleaned up for call {self.b2b_key}")

    # --- Additional methods needed ---
    async def get_transcription(self):
        """ Gets the next transcription result (placeholder) """
        # This might not be needed if results are pushed via callbacks like in Deepgram
        # Or it could pull from self.transcription_queue
        try:
             return await self.transcription_queue.get()
        except asyncio.QueueEmpty:
             return None

    # We might need a method similar to Deepgram's on_text callback registration
    # depending on how engine.py or call.py uses the AIEngine instance.


# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4 