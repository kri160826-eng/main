
FROM python:3.11-slim
WORKDIR /app

# Create a non-root user
RUN adduser --disabled-password --gecos "" myuser

# Switch to the non-root user
USER myuser

# Set up environment variables - Start
ENV PATH="/home/myuser/.local/bin:$PATH"

ENV GOOGLE_GENAI_USE_VERTEXAI=1
ENV GOOGLE_CLOUD_PROJECT=project-612d0540-c843-44b0-a04
ENV GOOGLE_CLOUD_LOCATION=us-central1

# Set up environment variables - End

# Install ADK - Start
RUN pip install google-adk==2.2.0
# Install ADK - End

# Copy agent - Start

# Set permission
COPY --chown=myuser:myuser "agentsV2/dm_agent/" "/app/agentsV2/dm_agent/"

# Copy agent - End

# Install Agent Deps - Start
RUN pip install -r "/app/agentsV2/dm_agent/requirements.txt"
# Install Agent Deps - End

EXPOSE 8000

CMD adk api_server --with_ui --port=8000 --host=0.0.0.0 --session_service_uri=memory:// --artifact_service_uri=memory://       "/app/agentsV2"
