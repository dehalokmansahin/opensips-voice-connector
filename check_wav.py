#!/usr/bin/env python
import wave
import sys

filename = sys.argv[1] if len(sys.argv) > 1 else "test_l16_16k.wav"

with wave.open(filename, 'rb') as wav_file:
    channels = wav_file.getnchannels()
    sample_width = wav_file.getsampwidth()
    frame_rate = wav_file.getframerate()
    frames = wav_file.getnframes()
    duration = frames / frame_rate
    
    print(f"File: {filename}")
    print(f"Channels: {channels}")
    print(f"Sample width: {sample_width} bytes")
    print(f"Sample rate: {frame_rate} Hz")
    print(f"Frames: {frames}")
    print(f"Duration: {duration:.2f} seconds")
    print(f"Format: {sample_width*8} bit {'PCM' if sample_width <= 2 else 'Unknown'}") 