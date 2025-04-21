# Vosk ASR Provider Configuration

This document describes the configuration parameters for the Vosk ASR (Automatic Speech Recognition) provider in the OpenSIPS AI Voice Connector.

## Overview

The Vosk provider enables speech-to-text capabilities by connecting to a Vosk server (via WebSocket) running locally or remotely. Vosk is an offline speech recognition toolkit that supports multiple languages, including Turkish.

## Configuration Parameters

The Vosk provider is configured under the `[vosk]` section in the configuration file:

| Parameter | Environment Variable | Description | Default |
|-----------|----------------------|-------------|---------|
| `url` | `VOSK_URL` | **Required**. The WebSocket URL of the Vosk server (e.g., `ws://localhost:2700`) | None |
| `sample_rate` | `VOSK_SAMPLE_RATE` | The audio sample rate to use for Vosk server | `8000` (Hz) |

## Example Configuration

```ini
[vosk]
url = ws://localhost:2700
sample_rate = 8000
```

## Setup Requirements

1. You need to have a running Vosk server. You can use the [vosk-server](https://github.com/alphacep/vosk-server) Docker container:

   ```bash
   docker run -d -p 2700:2700 alphacep/kaldi-en:latest
   ```

   For Turkish language support, use a Turkish Vosk model.

2. Add the `websockets` Python package to your environment:

   ```bash
   pip install websockets>=10.0
   ```

## Implementation Notes

- The Vosk provider automatically selects audio codecs compatible with Vosk (PCMU/PCMA are preferred).
- The WebSocket connection to the Vosk server is automatically managed with reconnection capability if the connection is lost.
- Audio is streamed in real-time through the WebSocket connection.
- Transcriptions are received as they become available.

## Integration with Language Model

To process the transcriptions with a language model (like GPT), you'll need to implement the `handle_phrase` method with your specific LLM integration. This is marked with TODOs in the code.

## Troubleshooting

If you encounter issues with the Vosk ASR provider, check the following:

1. Ensure your Vosk server is running and accessible at the specified URL.
2. Verify that the sample rate matches the configuration of your Vosk model.
3. Check the logs for any connection or transcription errors. 