FROM python:3.12-slim

WORKDIR /app

COPY bridge/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/agent_config.py ./agent_config.py
COPY bridge/durable_memory.py ./durable_memory.py
COPY bridge/wearabllm_bridge.py ./wearabllm_bridge.py

ENV PYTHONUNBUFFERED=1 \
    WEARABLLM_HOSTED=1 \
    WEARABLLM_DURABLE_MEMORY=1 \
    WEARABLLM_MEMORY_BACKEND=supabase \
    WEARABLLM_CONVERSATION_BACKEND=supabase \
    WEARABLLM_SESSION_IDLE_SECONDS=3600 \
    WEARABLLM_PROVIDER=openrouter \
    WEARABLLM_STT=openrouter \
    WEARABLLM_LLM_MODEL=openai/gpt-5.2 \
    WEARABLLM_STT_MODEL=openai/whisper-large-v3 \
    WEARABLLM_TTS_MODEL=x-ai/grok-voice-tts-1.0 \
    WEARABLLM_TTS_VOICE=ara

EXPOSE 7860

CMD ["sh", "-c", "python wearabllm_bridge.py --host 0.0.0.0 --port ${PORT:-7860}"]
