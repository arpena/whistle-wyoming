FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY whistle_wyoming.py .

# Engine + weights are downloaded here on first start; mount a volume to keep them.
VOLUME /root/.cache/cactus-needle
EXPOSE 10300

ENTRYPOINT ["python", "-u", "whistle_wyoming.py"]
