# Match the hosted runner's glibc when mounting its freshly built embedded YAS binary.
FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libstdc++6 procps \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
