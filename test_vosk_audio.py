#!/usr/bin/env python
"""
Vosk sunucusuna doğrudan bir WebSocket bağlantısı ile ses dosyası gönderen test betiği
"""

import asyncio
import logging
import sys
import os
import wave
import json
import websockets
import audioop

# Loglamayı ayarla
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_vosk_with_wav(audio_file_path, vosk_url="ws://vosk-server.ai-gateway.svc.cluster.local:2700"):
    """Bir WAV dosyasını doğrudan WebSocket üzerinden Vosk sunucusuna gönderip test eder"""
    
    logger.info(f"Testing Vosk with audio file: {audio_file_path}")
    
    try:
        # WAV dosyasını aç ve doğrula
        # GELİŞTİRİLEBİLİR: Bir bağlam yöneticisi (context manager) sınıfı oluşturarak ses dosyası işlemlerini daha sağlam hale getirebiliriz
        with wave.open(audio_file_path, 'rb') as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            
            logger.info(f"Audio file: {audio_file_path}")
            logger.info(f"Channels: {channels}, Sample width: {sample_width} bytes")
            logger.info(f"Sample rate: {frame_rate} Hz, Duration: {frames/frame_rate:.2f} seconds")
            
            # Vosk sunucusu config.ini'den 16000 Hz bekliyor
            # GELİŞTİRİLEBİLİR: Bu değeri config.ini dosyasından okuyarak hardcoded (sabit kodlanmış) olmasını önleyebiliriz
            target_sample_rate = 16000
            logger.info(f"Vosk expects sample rate: {target_sample_rate} Hz")
            needs_resample = frame_rate != target_sample_rate
            if needs_resample:
                logger.info(f"Will resample from {frame_rate} Hz to {target_sample_rate} Hz")
            
            # Vosk WebSocket sunucusuna bağlan
            # GELİŞTİRİLEBİLİR: Bağlantı için daha sağlam bir hata yönetimi ve yeniden bağlanma mekanizması eklenebilir
            async with websockets.connect(vosk_url) as websocket:
                logger.info(f"Connected to Vosk server at {vosk_url}")
                
                # Hedef örnekleme oranı ile konfigürasyon mesajı gönder
                config = {
                    "config": {
                        "sample_rate": target_sample_rate
                    }
                }
                await websocket.send(json.dumps(config))
                logger.info(f"Sent configuration: {config}")
                
                # Gelen mesajları almak için bir görev oluştur
                # GELİŞTİRİLEBİLİR: Görev sonuçlarını toplama ve daha iyi analiz etme mekanizması eklenebilir
                receive_task = asyncio.create_task(receive_messages(websocket))
                
                # Sesi daha büyük parçalar halinde oku (20ms yerine 40ms)
                chunk_samples = int(frame_rate * 0.04)  # 40ms'lik örnekler
                chunk_size = chunk_samples * channels * sample_width
                chunk_count = 0
                
                # Ses göndermeye başlamadan önce biraz bekle
                await asyncio.sleep(0.5)
                
                while True:
                    audio_chunk = wav_file.readframes(chunk_samples)
                    if not audio_chunk:
                        break
                    
                    # Gerekiyorsa ses formatı dönüşümlerini yap
                    # GELİŞTİRİLEBİLİR: Ses dönüştürme işlemlerini ayrı bir yardımcı fonksiyon olarak ayırarak kodu daha temiz hale getirebiliriz
                    if needs_resample:
                        # Stereo ise mono'ya dönüştür
                        if channels == 2:
                            audio_chunk = audioop.tomono(audio_chunk, sample_width, 0.5, 0.5)
                        
                        # Hedef örnekleme oranına yeniden örnekle
                        audio_chunk = audioop.ratecv(
                            audio_chunk, 
                            sample_width, 
                            1, 
                            frame_rate, 
                            target_sample_rate, 
                            None
                        )[0]
                    
                    # Vosk için sadece mono kanal kullan
                    elif channels == 2:
                        audio_chunk = audioop.tomono(audio_chunk, sample_width, 0.5, 0.5)
                    
                    # İşlenmiş ses parçasını gönder
                    await websocket.send(audio_chunk)
                    chunk_count += 1
                    
                    if chunk_count % 25 == 0:
                        logger.info(f"Sent {chunk_count} chunks ({len(audio_chunk)} bytes in last chunk)")
                    
                    # Gerçek zamanlı ses akışını taklit et (40ms'lik parçalar için 40ms gecikme)
                    # GELİŞTİRİLEBİLİR: Akış kontrolü için token bucket algoritması gibi daha gelişmiş bir yöntem kullanılabilir
                    await asyncio.sleep(0.04)
                    
                    # Her 10 parçada bir sunucunun yetişmesi için ek duraklama ekle
                    if chunk_count % 10 == 0:
                        await asyncio.sleep(0.02)  # Ekstra 20ms duraklama
                
                logger.info(f"Finished sending {chunk_count} audio chunks")
                
                # EOF göndermeden önce biraz bekle
                await asyncio.sleep(0.5)
                
                # Bitirmek için EOF gönder
                # GELİŞTİRİLEBİLİR: EOF gönderme ve sonuçları bekleme sürecinde daha dikkatli hata yönetimi yapılabilir
                await websocket.send(json.dumps({"eof": 1}))
                logger.info("Sent EOF marker")
                
                # Son sonuçları bekle
                await asyncio.sleep(3)
                
                # receive_task görevini iptal et
                # GELİŞTİRİLEBİLİR: Görev sonuçlarını işleyebilir ve gerekli bilgileri toplayabiliriz
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
    """Vosk sunucusundan gelen mesajları al ve işle"""
    try:
        while True:
            message = await websocket.recv()
            try:
                result = json.loads(message)
                if "text" in result and result["text"]:
                    logger.info(f"💬 Transcription: {result['text']}")
                elif "partial" in result and result["partial"]:
                    if len(result["partial"]) > 5:  # Sadece anlamlı kısmi sonuçları logla
                        logger.info(f"🔄 Partial: {result['partial']}")
                else:
                    logger.info(f"Received: {message}")
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {message}")
    except asyncio.CancelledError:
        logger.info("Receive task cancelled")
        raise
    except Exception as e:
        # GELİŞTİRİLEBİLİR: Hata mesajları toplanabilir ve daha sonra analiz edilebilir
        logger.error(f"Error in receive task: {e}")

async def main():
    """Testi çalıştır"""
    # Komut satırından ses dosyası yolunu al veya varsayılanı kullan
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
    
    # Uygun çıkış kodu ile çık
    sys.exit(0 if result else 1)

if __name__ == "__main__":
    asyncio.run(main()) 
