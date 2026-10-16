FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# PayPal's Agent Toolkit: only its framework-free core is used, so its optional
# framework extras (CrewAI, LangChain, OpenAI Agents) are not installed.
RUN pip install --no-cache-dir --no-deps paypal-agent-toolkit==1.11.0
COPY app app
COPY samples/northwind-harbour-services-agreement.pdf samples/
ENV PORT=8080
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
