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
STT için Vosk WebSocket iletişimini uygulayan modül
"""

import logging
import asyncio
import json
import websockets  # WebSockets kütüphanesini içe aktar
import time

from ai import AIEngine
from config import Config
from codec import get_codecs, CODECS, UnsupportedCodec

# Loglamayı yapılandır
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FlowControl:
    """Akış kontrolü için token bucket algoritmasını uygular"""
    
    def __init__(self, rate=50, capacity=100):
        """Saniyede rate token ile akış kontrolünü başlat"""
        self.rate = rate  # saniyede token sayısı
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self.connection_health = 1.0  # 0.0-1.0 arasında bağlantı sağlığını belirtir
        self.lock = asyncio.Lock()
    
    async def consume(self, tokens=1):
        """Tokenleri tüket, gerekirse bekle. Tokenler tüketildiğinde True döner."""
        # GELİŞTİRİLEBİLİR: Tüketim hızına göre dinamik olarak token kapasitesini ayarlayabilir
        async with self.lock:
            now = time.time()
            # Geçen süreye göre token yenile
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate * self.connection_health)
            self.last_update = now
            
            if tokens <= self.tokens:
                # Yeterli token var
                self.tokens -= tokens
                return 0  # Bekleme gerekmez
            else:
                # Yeterli token yok, bekleme süresi hesapla
                wait_time = (tokens - self.tokens) / (self.rate * self.connection_health)
                self.tokens = 0
                return wait_time
    
    def connection_error(self):
        """Bağlantı sorunu olduğunu bildir, hızı düşür"""
        self.connection_health = max(0.2, self.connection_health * 0.7)
        logger.info(f"Flow control: reducing to {self.connection_health*100:.1f}% of normal rate")
    
    def connection_success(self):
        """Başarılı işlemi bildir, hızı kademeli olarak arttır"""
        self.connection_health = min(1.0, self.connection_health * 1.05)

class VoskSTT(AIEngine):
    """ Vosk WebSocket iletişimini uygular """

    def __init__(self, call, cfg):
        """ Vosk STT motorunu başlatır """
        logger.info("Initializing Vosk STT Engine")
        self.cfg = Config.get("vosk", cfg)
        self.call = call  # İleride kullanılabilecek çağrı referansını sakla
        self.b2b_key = call.b2b_key # Tanımlama için B2B anahtarını sakla

        # --- Konfigürasyon ---
        self.vosk_server_url = self.cfg.get("url", "VOSK_URL")
        # Belirtilmemişse 8000Hz varsayılan olarak kullan, Vosk genellikle 8k veya 16k kullanır
        # GELİŞTİRİLEBİLİR: Farklı örnek hızlarını destekleyecek şekilde yeniden örnekleme eklenebilir
        self.sample_rate = int(self.cfg.get("sample_rate", "VOSK_SAMPLE_RATE", 8000))
        # İhtiyaç duyulursa buraya daha fazla Vosk'a özel yapılandırma seçeneği eklenebilir (ör. model)

        if not self.vosk_server_url:
            logger.error("Vosk server URL is not configured. Please set 'url' in the [vosk] section or VOSK_URL env var.")
            raise ValueError("Vosk server URL not configured")

        logger.info(f"Vosk Config: URL={self.vosk_server_url}, SampleRate={self.sample_rate}")

        # --- Durum ---
        self.codec = self.choose_codec(call.sdp) # Erken aşamada codec'i belirle
        logger.info(f"Chosen Codec: {self.codec.name}@{self.codec.sample_rate}Hz (Target Vosk Rate: {self.sample_rate}Hz)")

        self.websocket = None
        self.connection_task = None
        self.receive_task = None
        self.send_task = None
        self.send_queue = asyncio.Queue() # Gönderilecek ses verileri için kuyruk
        self.transcription_queue = asyncio.Queue() # Alınan transkripsiyon sonuçları için kuyruk
        self.is_active = False
        self.stop_event = asyncio.Event() # Görevleri durdurmak için sinyal
        
        # Örnek hızına göre hedef paket hızıyla akış kontrolünü başlat
        # 20ms paket varsayarak: 1000ms/20ms = saniyede 50 paket
        # GELİŞTİRİLEBİLİR: Bu değer dinamik olarak ayarlanabilir
        packet_rate = 1000 / 20  # 20ms'lik parçalar için saniyede 50 paket
        self.flow_control = FlowControl(rate=packet_rate, capacity=packet_rate)
        
        # Uyarlanabilir geri çekilme için hata izleme
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.reconnection_attempts = 0

        # ChatGPT veya benzer bir bileşenle potansiyel entegrasyon için yer tutucu
        # self.chat_handler = ... # Proje modeline bağlı olarak gerekirse başlat

        logger.info(f"VoskSTT initialized for call {self.b2b_key}")


    def choose_codec(self, sdp):
        """ Tercih edilen codec'i seçer, Vosk için PCM'e kolayca dönüştürülebilenleri önceliklendirir """
        # GELİŞTİRİLEBİLİR: Daha geniş codec desteği eklenebilir (örn. Opus)
        codecs = get_codecs(sdp)
        cmap = {c.name.lower(): c for c in codecs}

        # Vosk genellikle ham PCM (L16) gerektirir. PCMU/PCMA kolayca dönüştürülebilir.
        preferred_codecs = ["pcmu", "pcma"] # G.711 mu-law ve A-law
        
        for codec_name in preferred_codecs:
            if codec_name in cmap:
                selected_codec = CODECS[codec_name](cmap[codec_name])
                # Codec sınıfının gerekirse decode metodu olduğundan emin ol
                if not hasattr(selected_codec, 'decode'):
                     logger.warning(f"Codec {codec_name} selected but has no decode method in codec.py!")
                     # Bu ölümcül bir hata mı yoksa ham geçişi varsayarak devam edebilir miyiz karar ver
                     # Şimdilik hedef örnekleme hızı eşleşiyorsa sorun olmadığını varsayalım
                     # if selected_codec.sample_rate != self.sample_rate:
                     #     continue # Veya dönüşüm imkansızsa hata yükselt

                logger.info(f"Selected codec based on SDP: {codec_name}")
                return selected_codec
        
        # Tercih edilen codec bulunamazsa, Opus var mı ve *dönüştürebilir miyiz* kontrol et (harici kütüphane gerekir)
        # if "opus" in cmap:
        #     # codec.py veya opus.py'da opus çözümü uygulanmış/mümkün mü kontrol et
        #     logger.warning("Opus found, but direct use/decoding for Vosk PCM needs verification/implementation.")
        #     # İşleme uygulanmışsa Opus codec'i döndür, aksi takdirde yedekleme veya hata ver

        # Uygun codec yoksa yedekleme veya hata ver
        logger.error(f"No suitable codec found in SDP for Vosk. Available: {list(cmap.keys())}. Need PCMU/PCMA or PCM compatible.")
        raise UnsupportedCodec(f"No suitable codec (PCMU/PCMA) found for Vosk in SDP: {list(cmap.keys())}")


    async def _connect_and_manage(self):
        """ WebSocket bağlantısını yöneten dahili görev, gönderme/alma döngüleri. """
        # GELİŞTİRİLEBİLİR: Devre kesici (circuit breaker) deseni eklenebilir
        while self.is_active and not self.stop_event.is_set():
            try:
                logger.info(f"Connecting to Vosk server at {self.vosk_server_url}")
                self.websocket = await websockets.connect(self.vosk_server_url)
                logger.info(f"Connected to Vosk server for call {self.b2b_key}")
                
                # Başarılı bağlantıda hata sayaçlarını sıfırla
                self.consecutive_errors = 0
                self.reconnection_attempts = 0
                self.flow_control.connection_success()
                
                # Bağlandıktan hemen sonra yapılandırma mesajı gönder
                config_message = json.dumps({
                    "config": {
                        "sample_rate": self.sample_rate
                    }
                })
                await self.websocket.send(config_message)
                logger.info(f"Sent configuration to Vosk: {config_message}")
                
                # Gönderme ve alma görevlerini başlat
                self.send_task = asyncio.create_task(self._send_loop())
                self.receive_task = asyncio.create_task(self._receive_loop())
                
                # Herhangi bir görev tamamlanırsa - biri başarısız olursa, yeniden bağlanacağız
                done, pending = await asyncio.wait(
                    [self.send_task, self.receive_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Bekleyen görevi iptal et
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Error during task cleanup for call {self.b2b_key}: {e}")
                
                # Görevlerin neden tamamlandığını kontrol et
                for task in done:
                    try:
                        await task  # Bu, görevi öldüren herhangi bir istisnaı yeniden yükseltecektir
                    except asyncio.CancelledError:
                        logger.info(f"Task was cancelled for call {self.b2b_key}")
                    except Exception as e:
                        logger.error(f"Task failed with error for call {self.b2b_key}: {e}")
                        self.consecutive_errors += 1
                        self.flow_control.connection_error()
                
                if self.stop_event.is_set():
                    logger.info(f"Stop event set, not reconnecting for call {self.b2b_key}")
                    break
                    
                logger.warning(f"One of the WebSocket tasks ended, will reconnect for call {self.b2b_key}")
                
            except websockets.exceptions.ConnectionClosed as e:
                if self.stop_event.is_set():
                    logger.info(f"Connection closed while stopping, exiting for call {self.b2b_key}")
                    break
                logger.warning(f"Vosk WebSocket connection closed for call {self.b2b_key}: {e}")
                self.consecutive_errors += 1
                self.flow_control.connection_error()
            except Exception as e:
                if self.stop_event.is_set():
                    logger.info(f"Exception while stopping, exiting for call {self.b2b_key}")
                    break
                logger.error(f"Error in Vosk WebSocket connection for call {self.b2b_key}: {e}")
                self.consecutive_errors += 1
                self.flow_control.connection_error()
            
            # WebSocket'i temizle
            if self.websocket:
                try:
                    await self.websocket.close()
                except Exception as e:
                    logger.warning(f"Error closing WebSocket for call {self.b2b_key}: {e}")
                finally:
                    self.websocket = None
            
            # Hala aktifse, yeniden bağlantı geri çekilmesini uygula
            if self.is_active and not self.stop_event.is_set():
                self.reconnection_attempts += 1
                # Titreşimli üstel geri çekilme
                # GELİŞTİRİLEBİLİR: Daha gelişmiş geri çekilme stratejileri ve daha iyi yeniden bağlanma mantığı
                base_delay = min(1.0 * (1.5 ** min(self.reconnection_attempts, 10)), 30)
                jitter = 0.1 * base_delay * (asyncio.get_event_loop().time() % 1.0)
                retry_delay = base_delay + jitter
                
                logger.info(f"Reconnecting to Vosk server in {retry_delay:.2f}s... (attempt {self.reconnection_attempts})")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=retry_delay)
                    if self.stop_event.is_set():
                        logger.info(f"Received stop event during reconnection delay for call {self.b2b_key}")
                        break
                except asyncio.TimeoutError:
                    # Zaman aşımı, bağlantıyı yeniden denememiz gerektiği anlamına gelir
                    pass
        
        logger.info(f"Vosk connection manager exiting for call {self.b2b_key}")

    async def _send_loop(self):
        """ Sürekli olarak kuyruktan ses verilerini gönderen dahili görev """
        try:
            logger.info(f"Starting Vosk audio send loop for call {self.b2b_key}")
            error_count = 0
            success_count = 0
            
            while self.is_active and not self.stop_event.is_set() and self.websocket and not self.websocket.closed:
                try:
                    # Bir sonraki ses parçasını zaman aşımı ile kuyruktan al
                    # Bu, periyodik olarak çıkış yapıp yapmamamız gerektiğini kontrol etmemizi sağlar
                    audio_data = await asyncio.wait_for(self.send_queue.get(), timeout=0.5)
                    
                    # Akış kontrolünü uygula
                    wait_time = await self.flow_control.consume()
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    
                    # Vosk'un beklediği formata (PCM) sesi eşleştir
                    # Seçilen codec'e göre çözümlememiz gerekip gerekmediğini kontrol et
                    # GELİŞTİRİLEBİLİR: Ses dönüştürme ve yeniden örnekleme için ayrı bir yardımcı yöntem eklenebilir
                    pcm_data = audio_data
                    if hasattr(self.codec, 'decode') and self.codec.sample_rate != self.sample_rate:
                        try:
                            pcm_data = self.codec.decode(audio_data)
                            # Gerekirse yeniden örnekleme burada yapılır
                        except Exception as e:
                            logger.error(f"Error decoding audio for Vosk for call {self.b2b_key}: {e}")
                            # Çözme başarısız olursa orijinal veriyle devam et
                            pcm_data = audio_data
                    
                    # Ses verisini Vosk'a ikili (binary) olarak gönder
                    await self.websocket.send(pcm_data)
                    self.send_queue.task_done()
                    
                    # Başarıyı izle
                    success_count += 1
                    error_count = 0  # Başarıda hata sayısını sıfırla
                    
                    # Periyodik olarak bağlantı sağlığını bildir
                    if success_count % 100 == 0:
                        self.flow_control.connection_success()
                    
                except asyncio.TimeoutError:
                    # queue.get() üzerinde sadece bir zaman aşımı, döngüye devam et
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"WebSocket closed during send for call {self.b2b_key}: {e}")
                    # _connect_and_manage yönteminin yeniden bağlanmayı ele almasına izin ver
                    raise
                except Exception as e:
                    logger.error(f"Error in Vosk send loop for call {self.b2b_key}: {e}")
                    # Uyarlanabilir geri çekilme için ardışık hataları izle
                    error_count += 1
                    if error_count >= 3:
                        self.flow_control.connection_error()
                        
                    # Ardışık hatalara dayalı artan gecikme ekle
                    backoff_delay = min(0.1 * (2 ** min(error_count, 5)), 2.0)
                    await asyncio.sleep(backoff_delay)
            
            # Kapatıyorsak, transkripsiyonu sonlandırmak için Vosk'a EOF işaretçisi gönder
            if self.websocket and not self.websocket.closed and not self.stop_event.is_set():
                try:
                    logger.info(f"Sending EOF marker to Vosk for call {self.b2b_key}")
                    await self.websocket.send(json.dumps({"eof": 1}))
                except Exception as e:
                    logger.error(f"Error sending EOF to Vosk for call {self.b2b_key}: {e}")
            
            logger.info(f"Vosk audio send loop exiting for call {self.b2b_key}")
        except Exception as e:
            logger.error(f"Unexpected error in Vosk send loop for call {self.b2b_key}: {e}")
            raise  # Yeniden bağlanmayı ele alması için _connect_and_manage'e yeniden yükselt

    async def _receive_loop(self):
        """ Vosk'tan sürekli olarak mesaj alan dahili görev """
        try:
            logger.info(f"Starting Vosk transcription receive loop for call {self.b2b_key}")
            
            # Zaman içinde cümle parçalarını toplamak için
            # Deepgram uygulamasının metni nasıl tamponladığına benzer
            # GELİŞTİRİLEBİLİR: Tampon boyutu sınırlandırılabilir ve tamponlama stratejisi iyileştirilebilir
            sentence_buffer = []
            
            while self.is_active and not self.stop_event.is_set() and self.websocket and not self.websocket.closed:
                try:
                    # Vosk'tan bir sonraki mesajı al
                    message = await self.websocket.recv()
                    
                    # Akış kontrolüne başarılı iletişimi bildir
                    self.flow_control.connection_success()
                    
                    # JSON yanıtını ayrıştır
                    try:
                        result = json.loads(message)
                        # Hata ayıklama çıktısı (üretimde kaldırılabilir veya debug seviyesine değiştirilebilir)
                        logger.info(f"Received from Vosk: {result}")
                        
                        # Sonuç nesnesini işle: birkaç olası yanıt türü var
                        
                        # 1. Final metin/sonuç transkripsiyon
                        if "text" in result and result["text"]:
                            final_text = result["text"]
                            if final_text.strip():  # Boş değilse
                                sentence_buffer.append(final_text)
                                # Bu bir cümlenin sonu gibi görünüyorsa
                                if final_text.endswith((".", "?", "!")):
                                    complete_sentence = " ".join(sentence_buffer)
                                    logger.info(f"Complete sentence from Vosk for call {self.b2b_key}: {complete_sentence}")
                                    
                                    # Deepgram uygulamasına benzer şekilde, tam cümleleri işleyeceğiz
                                    # Ayrıca bir handle_phrase veya eşdeğerini tetikleyeceğiz
                                    
                                    # Döngüyü engellememe için create_task kullan
                                    # GELİŞTİRİLEBİLİR: Bu görevleri izlemek ve düzgün temizlenmesini sağlamak için bir mekanizma eklenebilir
                                    asyncio.create_task(self.handle_phrase(complete_sentence))
                                    
                                    # Tam bir cümleden sonra tamponu temizle
                                    sentence_buffer.clear()
                        
                        # 2. Kısmi sonuç (hala devam eden geçici transkripsiyon)
                        elif "partial" in result and result["partial"]:
                            partial_text = result["partial"]
                            if logger.level <= logging.DEBUG:  # Kısmi sonuçları sadece debug seviyesinde logla
                                logger.debug(f"Partial from Vosk for call {self.b2b_key}: {partial_text}")
                            # Genellikle kısmi sonuçlar üzerinde UI'da gösterme veya izleme dışında bir işlem yapmayız.
                            # Gerekirse transkripsiyon kuyruğunda saklayabiliriz.
                            # await self.transcription_queue.put({"type": "partial", "text": partial_text})
                        
                        # Vosk'un sağladığı diğer yanıt türlerini ekle
                    
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode JSON from Vosk for call {self.b2b_key}: {e}. Message: {message}")
                    
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(f"WebSocket closed during receive for call {self.b2b_key}: {e}")
                    # _connect_and_manage yönteminin yeniden bağlanmayı ele almasına izin ver
                    raise
                except Exception as e:
                    logger.error(f"Error in Vosk receive loop for call {self.b2b_key}: {e}")
                    # Akış kontrolünde bağlantı sorununu işaretle
                    self.flow_control.connection_error()
                    
                    # Hatalar için uyarlanabilir geri çekilme kullan
                    backoff_delay = min(0.1 * (2 ** min(self.consecutive_errors, 5)), 2.0)
                    await asyncio.sleep(backoff_delay)
            
            logger.info(f"Vosk transcription receive loop exiting for call {self.b2b_key}")
        except Exception as e:
            logger.error(f"Unexpected error in Vosk receive loop for call {self.b2b_key}: {e}")
            raise  # Yeniden bağlanmayı ele alması için _connect_and_manage'e yeniden yükselt

    # Deepgram'ın handle_phrase'ine benzer şekilde, transkribe edilmiş metni işleme yardımcı yöntemi
    async def handle_phrase(self, phrase):
        """ Tam transkribe edilmiş cümleyi işler """
        logger.info(f"Handling phrase from Vosk for call {self.b2b_key}: {phrase}")
        
        # TODO: ChatGPT yerine yerel LLM servisi ile entegre et
        # TODO: Bir TTS servisine bağlan (belirlenecek)
        # TODO: Akış Deepgram uygulamasına benzer olacak:
        #       1. Cümleyi yerel LLM servisine aktar
        #       2. LLM'den yanıt al
        #       3. Yanıtı TTS servisi kullanarak konuşmaya dönüştür
        #       4. Konuşmayı arayana geri gönder
        
        # Şimdilik, sadece transkribe edilen cümleyi logla
        return

    async def start(self):
        """ WebSocket bağlantısını başlatarak Vosk STT işlemeyi başlatır """
        logger.info(f"Starting Vosk STT for call {self.b2b_key}")
        if not self.vosk_server_url:
             logger.error("Cannot start Vosk STT: URL not configured.")
             return # Veya bir hata yükselt

        self.is_active = True
        self.stop_event.clear()
        # Bağlantı ve işleme görevini başlat
        self.connection_task = asyncio.create_task(self._connect_and_manage())
        logger.info(f"Vosk STT connection manager task created for call {self.b2b_key}")
        # Bağlantının kurulmasını beklemek isteyebiliriz
        # await self.websocket.wait_connected() # Kolayca destekleyen bir kütüphane kullanıyorsak
        # veya _connect_and_manage tarafından sinyallenen dahili bir olay kullan


    async def send(self, audio):
        """ Ses verilerini WebSocket bağlantısı için gönderme kuyruğuna koyar """
        if not self.is_active or self.stop_event.is_set():
            # logger.debug(f"Vosk STT not active or stopping, ignoring audio data for call {self.b2b_key}")
            return # Çalışmıyorsa kuyruğa koyma

        if self.websocket and not self.websocket.closed:
             # Doğrudan gönderme tercih edilirse ve bağlantı stabilse:
             # try:
             #     # Gerekirse çözümle
             #     pcm_data = self.codec.decode(audio) if self.codec.sample_rate != self.sample_rate else audio # Temel kontrol
             #     await self.websocket.send(pcm_data)
             # except Exception as e:
             #     logger.error(f"Error sending audio directly: {e}")
             #     # Hatayı ele al, belki bağlantıyı kapat veya veriyi kuyruğa al
             # else:
             # Ham sesi kuyruğa koy, çözümleme gönderme görevinde gerçekleşecek
             await self.send_queue.put(audio)
        else:
             # logger.warning(f"WebSocket not ready, queueing audio data for call {self.b2b_key}")
             # Bağlantı kapalı olsa bile kuyruğa al, _connect_and_manage yeniden bağlanabilir
             await self.send_queue.put(audio)


    async def close(self):
        """ Vosk STT oturumunu kapatır ve kaynakları temizler """
        logger.info(f"Closing Vosk STT connection for call {self.b2b_key}")
        if not self.is_active:
            return

        self.is_active = False
        self.stop_event.set() # Tüm görevlere durma sinyali ver

        # Görevleri nazikçe durdur
        # GELİŞTİRİLEBİLİR: Bekleyen tüm görevleri (handle_phrase görevleri gibi) izleyip temizleyebiliriz
        if self.connection_task:
            try:
                 # Bağlantı görevine EOF gönderme ve WebSocket'i kapatma şansı ver
                 await asyncio.wait_for(self.connection_task, timeout=5.0)
            except asyncio.TimeoutError:
                 logger.warning(f"Vosk connection task did not finish gracefully within timeout for call {self.b2b_key}. Cancelling.")
                 self.connection_task.cancel()
            except Exception as e:
                 logger.error(f"Error during connection task shutdown for call {self.b2b_key}: {e}")
                 self.connection_task.cancel() # Diğer hatalarda iptali sağla

        # connection_task yapmadıysa WebSocket'i açıkça kapat
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.close(code=1000, reason='Client closing')
                logger.info(f"Vosk WebSocket closed explicitly for call {self.b2b_key}")
            except Exception as e:
                logger.error(f"Error closing Vosk WebSocket for call {self.b2b_key}: {e}")

        self.websocket = None
        self.connection_task = None
        self.receive_task = None
        # Kuyrukları temizle? Kapatma sırasındaki istenen davranışa göre karar ver.
        # while not self.send_queue.empty(): self.send_queue.get_nowait()
        # while not self.transcription_queue.empty(): self.transcription_queue.get_nowait()

        logger.info(f"Vosk STT resources cleaned up for call {self.b2b_key}")

    # --- İhtiyaç duyulan ek yöntemler ---
    async def get_transcription(self):
        """ Bir sonraki transkripsiyon sonucunu alır (yer tutucu) """
        # Deepgram'daki gibi geri aramalar aracılığıyla sonuçlar itilirse bu gerekli olmayabilir
        # Veya self.transcription_queue'dan çekebilir
        try:
             return await self.transcription_queue.get()
        except asyncio.QueueEmpty:
             return None

    # engine.py veya call.py'nin AIEngine örneğini nasıl kullandığına bağlı olarak
    # Deepgram'ın on_text geri arama kaydına benzer bir yönteme ihtiyacımız olabilir.


# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4 