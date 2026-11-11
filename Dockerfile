FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY whistle_wyoming.py .

# Engine + weights are downloaded on first start into ~/.cache/cactus, with Hugging Face's own
# cache in ~/.cache/huggingface. Both are needed to start offline, so mount a volume over ~/.cache.
VOLUME /root/.cache
EXPOSE 10300

ENTRYPOINT ["python", "-u", "whistle_wyoming.py"]
