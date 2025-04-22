FROM python:3.10-slim-buster

WORKDIR /app

# Uygulamayı kopyala
COPY . /app/

# Gereksinimleri yükle
RUN pip install --no-cache-dir -r ../requirements.txt

# SIP ve RTP için portları tanımla
EXPOSE 5060/udp
EXPOSE 35000-40000/udp

# Çevre değişkenleri
ENV EVENT_PORT=50060
ENV CONFIG_FILE=/app/config.ini

# Çalıştırma komutu
CMD ["python3", "-u", "src/main.py"]