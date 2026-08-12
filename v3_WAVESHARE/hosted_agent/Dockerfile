FROM python:3.12-slim

WORKDIR /app

COPY bridge/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/durable_memory.py ./durable_memory.py
COPY bridge/action_queue.py ./action_queue.py
COPY bridge/agent_config.py ./agent_config.py
COPY bridge/household_memory.py ./household_memory.py
COPY bridge/source_code.py ./source_code.py
COPY bridge/sphere_tools.py ./sphere_tools.py
COPY bridge/wearabllm_bridge.py ./wearabllm_bridge.py
COPY source_bundle.json ./source_bundle.json

ENV PYTHONUNBUFFERED=1 \
    WEARABLLM_HOSTED=1 \
    WEARABLLM_DURABLE_MEMORY=1 \
    WEARABLLM_MEMORY_BACKEND=supabase \
    WEARABLLM_EMBEDDING_MODEL=text-embedding-3-small \
    WEARABLLM_EMBEDDING_DIMENSIONS=512 \
    WEARABLLM_WEB_SEARCH=1 \
    WEARABLLM_MAX_TOOL_ROUNDS=8 \
    WEARABLLM_CONVERSATION_BACKEND=supabase \
    WEARABLLM_ACTION_BACKEND=supabase \
    WEARABLLM_SESSION_IDLE_SECONDS=3600 \
    WEARABLLM_PROVIDER=openai \
    WEARABLLM_STT=openai \
    WEARABLLM_LLM_MODEL=gpt-5.4-mini \
    WEARABLLM_STT_MODEL=gpt-4o-mini-transcribe \
    WEARABLLM_TTS_MODEL=gpt-4o-mini-tts \
    WEARABLLM_TTS_VOICE=marin \
    WEARABLLM_MAX_OUTPUT_TOKENS=1024

EXPOSE 7860

CMD ["sh", "-c", "python wearabllm_bridge.py --host 0.0.0.0 --port ${PORT:-7860}"]
