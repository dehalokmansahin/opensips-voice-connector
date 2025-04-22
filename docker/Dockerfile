FROM python:3.10-slim-buster

# Çalışma dizini
WORKDIR /app

# Tüm proje dosyalarını (src, requirements.txt, config.ini vs.) kopyala
COPY . /app/

# Gereksinimleri yükle
RUN pip install --no-cache-dir -r /app/requirements.txt

# Gerekli portlar
EXPOSE 5060/udp
EXPOSE 35000-40000/udp

# Ortam değişkenleri
ENV EVENT_PORT=50060
ENV CONFIG_FILE=/app/config.ini

# Uygulama çalıştır
CMD ["python3", "-u", "src/main.py"]