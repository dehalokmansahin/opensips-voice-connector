#!/usr/bin/env python
"""
Vosk WebSocket bağlantısı için basit test betiği
"""

import asyncio
import logging
import websockets
import json
import sys
import os
import traceback

# Loglamayı ayarla
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_vosk_connection():
    """Vosk sunucusuna bağlanmayı ve basit bir konfigürasyon göndermeyi test eder"""
    vosk_url = os.environ.get("VOSK_URL", "ws://localhost:2700")
    logger.info(f"Connecting to Vosk server at {vosk_url}")
    
    try:
        # Zaman aşımı ile Vosk WebSocket sunucusuna bağlan
        # GELİŞTİRİLEBİLİR: Bağlantı hatalarını daha ayrıntılı ele alabilir ve daha güvenli bir bağlantı yönetimi yapılabilir
        websocket = await asyncio.wait_for(
            websockets.connect(vosk_url),
            timeout=5.0
        )
        logger.info("Connected to Vosk server!")
        
        # Konfigürasyon gönder
        # GELİŞTİRİLEBİLİR: Konfigürasyon değerlerini config.ini dosyasından okuyabiliriz
        config = {
            "config": {
                "sample_rate": 16000
            }
        }
        await websocket.send(json.dumps(config))
        logger.info(f"Sent configuration: {config}")
        
        # Küçük bir boş ses örneği gönder (sessizlik)
        # 16000Hz'de 0.1 saniyelik sessizlik üret (16-bit PCM)
        # GELİŞTİRİLEBİLİR: Farklı ses formatları ile de test yapabiliriz
        sample_count = int(16000 * 0.1)  # 0.1 saniyelik ses
        empty_audio = bytes(sample_count * 2)  # 16-bit PCM için örnek başına 2 bayt
        
        await websocket.send(empty_audio)
        logger.info(f"Sent {len(empty_audio)} bytes of empty audio")
        
        # Boş yanıt almak ve kapatmak için EOF gönder
        # GELİŞTİRİLEBİLİR: Sunucudan gelen yanıtları doğrulayabiliriz
        await websocket.send(json.dumps({"eof": 1}))
        logger.info("Sent EOF")
        
        # Zaman aşımı ile yanıt al
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=3.0)
            logger.info(f"Received: {response}")
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for response from Vosk server")
        
        # Bağlantıyı kapat
        # GELİŞTİRİLEBİLİR: WebSocket bağlantısını daha zarif bir şekilde kapatma mekanizması eklenebilir
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
        # GELİŞTİRİLEBİLİR: Hata türlerine göre daha spesifik işlemler yapılabilir
        logger.error(f"Error testing Vosk connection: {e}")
        logger.error(traceback.format_exc())
        return False

async def main():
    """Testi çalıştır"""
    logger.info("Starting Vosk connection test")
    result = await test_vosk_connection()
    
    if result:
        logger.info("✅ Vosk connection test successful!")
        logger.info("Your Vosk server is working and accessible.")
        logger.info("The VoskSTT module should be able to connect to it.")
    else:
        logger.error("❌ Vosk connection test failed!")
        logger.error("Please check if your Vosk server is running at the correct address.")
    
    # Uygun çıkış kodu ile çık
    sys.exit(0 if result else 1)

if __name__ == "__main__":
    # GELİŞTİRİLEBİLİR: Yeniden bağlanma testleri de ekleyebiliriz
    asyncio.run(main()) 