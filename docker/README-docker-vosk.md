# OpenSIPS Voice Connector Docker Kurulumu (Vosk Entegrasyonu)

Bu belge, OpenSIPS AI Voice Connector'ın Docker ortamında Vosk konuşma tanıma servisi ile birlikte nasıl çalıştırılacağını ve konfigürasyonunun dinamik olarak nasıl değiştirilebileceğini açıklar.

## Genel Bakış

OpenSIPS Voice Connector, SIP tabanlı iletişimi AI motorlarına bağlayan bir uygulamadır. Bu kurulumda, Vosk konuşma tanıma servisi (Speech-to-Text) ile entegre edilmiştir.

## Gereksinimler

- Docker ve Docker Compose kurulu olmalıdır
- Vosk sunucusu (ayrı bir Docker container olarak)
- Python 3.7+

## Kurulum Adımları

### 1. Proje Dosyalarını İndirin

```bash
git clone https://github.com/OpenSIPS/opensips-ai-voice-connector-ce.git
cd opensips-ai-voice-connector-ce
```

### 2. Konfigürasyon Dosyası Hazırlayın

Proje kök dizininde `config.ini` dosyasını oluşturun veya düzenleyin:

```ini
[engine]
event_ip = 127.0.0.1
event_port = 50060

[opensips]
ip = 127.0.0.1
port = 8080

[rtp]
min_port = 35000
max_port = 65000
bind_ip = 0.0.0.0
proxy_ip = 0.0.0.0
external_ip = 127.0.0.1

[vosk]
url = ws://localhost:2700  # Vosk sunucusunun URL'si (Docker network için güncelleyin)
sample_rate = 16000        # Vosk modelinize uygun örnek hızı

# Diğer sağlayıcıları devre dışı bırakın
[deepgram]
disabled = true

[openai]
disabled = true

# Genel ayarlar
[general]
logfile = vosk_connector.log
loglevel = INFO

# AI tipi seçimi
[ai]
type = vosk
```

### 3. Docker Compose Dosyasını Hazırlayın

Önceden hazırlanan Docker Compose dosyasında, config.ini dosyasını bir volume olarak bağlamak için aşağıdaki değişiklikleri yapın (docker/docker-compose.yml):

```yaml
services:
  engine:
    container_name: ai-voice-connector-engine
    build:
      context: ../
      dockerfile: ./docker/Dockerfile
    network_mode: host
    environment:
      CONFIG_FILE: /app/config.ini  # Container içindeki config dosyası konumu
    env_file:
      - .env
    volumes:
      - ../src:/app/src/
      - ../config.ini:/app/config.ini  # Host makinedeki config.ini'yi container'a bağla
  
  opensips:
    container_name: ai-voice-connector-opensips
    image: opensips/opensips:latest
    volumes:
      - ../cfg/:/etc/opensips/
    network_mode: host
```

### 4. Vosk Sunucusuyla Bağlantı

Vosk sunucusu zaten ayrı bir Docker container olarak çalışıyorsa, `config.ini` dosyasında `url` parametresini doğru container ismine veya IP adresine göre ayarlayın:

```ini
[vosk]
url = ws://localhost:2700  # Container adı veya IP adresi
```

Farklı bir network yapılandırması kullanıyorsanız, URL'yi buna göre güncelleyin.

### 5. Docker Ortamını Başlatın

```bash
cd docker
cp .env.example .env  # .env dosyasını oluşturun
docker-compose up -d  # Container'ları arka planda başlatın
```

## Dinamik Konfigürasyon Değişiklikleri

OpenSIPS Voice Connector'ın konfigürasyonunu dinamik olarak değiştirmek için:

1. Host makinenizdeki `config.ini` dosyasını herhangi bir metin editörü ile düzenleyin
2. Değişiklikleri kaydedin

Container, config.ini dosyasını host makineden bir volume olarak bağladığı için, değişiklikleriniz anında container içine yansıyacaktır. OpenSIPS Voice Connector, yeni ayarları okumak için yeniden başlatılmaya gerek duymaz.

## Konfigürasyon Değişikliği Örnekleri

### Loglama Seviyesini Değiştirme

```ini
[general]
loglevel = DEBUG  # INFO, WARNING, ERROR değerlerinden biri olabilir
```

### Vosk Sunucusu URL'sini Değiştirme

```ini
[vosk]
url = ws://yeni-vosk-sunucusu:2700
```

### Örnek Hızını Değiştirme

```ini
[vosk]
sample_rate = 8000  # Kullandığınız Vosk modeli ile uyumlu olmasına dikkat edin
```

## Log Dosyalarını İzleme

Docker container'ının loglarını izlemek için:

```bash
docker logs -f ai-voice-connector-engine
```

## Sorun Giderme

1. Eğer Vosk sunucusuna bağlantı sorunları yaşıyorsanız:
   - Vosk sunucusunun çalışır durumda olduğunu kontrol edin
   - URL'nin doğru olduğunu kontrol edin
   - Network yapılandırmasını kontrol edin (host network veya Docker network)

2. Ses sorunları için:
   - Örnek hızının (sample_rate) Vosk modelinizle uyumlu olduğunu kontrol edin
   - SIP ve RTP ayarlarını kontrol edin

3. Docker container'ını yeniden başlatmak için:
   ```bash
   docker restart ai-voice-connector-engine
   ```

## Notlar

- Vosk, konuşma tanıma için online servislere kıyasla daha düşük gecikme süresi sunar, ancak tanıma doğruluğu kullanılan modele bağlıdır.
- Türkçe dil desteği için uygun bir Türkçe Vosk modeli kullanmanız gerekir. 