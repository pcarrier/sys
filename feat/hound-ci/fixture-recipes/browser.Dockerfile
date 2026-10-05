# The Rust sandbox-browser tests require this image to exist; they never build it themselves.
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install --no-install-recommends -y chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
