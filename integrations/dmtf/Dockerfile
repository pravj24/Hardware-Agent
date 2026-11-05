FROM python:3.13-slim
ARG DMTF_COMMIT=23d21f9a3610f6b6fbb71ffc941296b159a524d2
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/DMTF/Redfish-Mockup-Server.git /opt/dmtf && \
    cd /opt/dmtf && git checkout --detach "$DMTF_COMMIT"
COPY requirements.lock /opt/requirements.lock
RUN pip install --no-cache-dir -r /opt/requirements.lock && useradd --uid 10001 --create-home mockserver
USER mockserver
WORKDIR /opt/dmtf
EXPOSE 8000
ENTRYPOINT ["python", "redfishMockupServer.py", "-H", "0.0.0.0", "-p", "8000", "-D", "public-rackmount1", "-S"]
