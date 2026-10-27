# Relay server only. The overlay app runs on the players' Windows PCs.
FROM python:3.13-slim
RUN pip install --no-cache-dir "websockets>=13"
WORKDIR /app
COPY src/telecaster/__init__.py src/telecaster/protocol.py src/telecaster/relay.py telecaster/
EXPOSE 8765
USER nobody
CMD ["python", "-m", "telecaster.relay", "--host", "0.0.0.0", "--port", "8765"]
