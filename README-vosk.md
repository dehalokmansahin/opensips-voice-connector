# Vosk Speech-to-Text Provider for OpenSIPS Voice Connector

This document provides instructions for setting up and using the Vosk Speech-to-Text (STT) provider with OpenSIPS Voice Connector.

## Overview

Vosk is an offline speech recognition toolkit that supports multiple languages. This integration allows OpenSIPS Voice Connector to use Vosk for streaming speech recognition through a WebSocket connection.

## Features

- Real-time streaming transcription via WebSocket
- Support for multiple languages (based on Vosk models)
- Automatic reconnection on connection loss
- Robust error handling
- Compatible with RTP codecs commonly used in VoIP (PCMU/PCMA)

## Requirements

1. A running Vosk server (locally or remotely accessible)
2. Python 3.7+
3. `websockets` Python package (version 10.0 or higher)

## Setup

### 1. Install Vosk Server

You can run Vosk server using Docker:

```bash
# For English model
docker run -d -p 2700:2700 alphacep/kaldi-en:latest

# For other languages, see Vosk documentation
# For Turkish model, you would need to use a Turkish-specific model
```

### 2. Add Vosk Configuration

Create or update your configuration file to include Vosk settings:

```ini
[vosk]
url = ws://localhost:2700
sample_rate = 8000

# Optional: Limit Vosk to specific callers
# match = ^.*@yourdomain\.com$
```

You can also set these configurations via environment variables:

```bash
export VOSK_URL="ws://localhost:2700"
export VOSK_SAMPLE_RATE="8000"
```

### 3. Install Dependencies

Add the `websockets` package to your environment:

```bash
pip install websockets>=10.0
```

## Usage

Once configured, the OpenSIPS Voice Connector will automatically use Vosk for speech recognition when:

1. The Vosk provider is selected through the configuration's matching rules, OR
2. The caller username matches "vosk", OR
3. You explicitly specify "vosk" as the AI flavor in your OpenSIPS dialplan.

### Example OpenSIPS Dialplan Configuration

```
if (is_method("INVITE") && has_body("application/sdp")) {
    # Use Vosk STT for calls to vosk@yourdomain.com
    if ($ru == "sip:vosk@yourdomain.com") {
        $var(params) = '{"flavor":"vosk"}';
        $avp(extra_params) = $var(params);
    }
    
    # Route the call to the AI Bot
    route(AI_BOT);
}
```

## Language Model Integration 

This Vosk integration includes placeholders for connecting to a Language Model (LLM) service and Text-to-Speech (TTS) service. These connections need to be implemented based on your specific LLM and TTS services.

See the TODOs in the `handle_phrase` method in `src/stt_vosk.py` for integration points.

## Troubleshooting

### Common Issues

1. **Connection Errors**: Ensure the Vosk server is running and accessible at the configured URL.
2. **Audio Format Issues**: If you see decoding errors, check that the audio codec is compatible (PCMU/PCMA are preferred).
3. **No Transcription**: Check the Vosk server logs to ensure it's receiving and processing audio correctly.

### Logs

Check the OpenSIPS Voice Connector logs for detailed information about Vosk connections, audio processing, and transcription results.

## License

This integration is distributed under the same license as the OpenSIPS Voice Connector project (GPL-3.0).

## Acknowledgments

- Vosk Speech Recognition Toolkit: https://github.com/alphacep/vosk-api
- OpenSIPS Team for the AI Voice Connector framework 