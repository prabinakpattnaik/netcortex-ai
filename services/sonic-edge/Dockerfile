FROM golang:1.23-alpine AS builder

WORKDIR /app

# Build the insight-agent
COPY ../insight-agent/go.mod ../insight-agent/go.sum* ./
RUN go mod download 2>/dev/null || true

COPY ../insight-agent/ .
RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /insight-agent ./cmd/main.go

# SONiC-like runtime
FROM alpine:3.19

LABEL description="SONiC OS Edge Router with InsightAgent" \
      sonic.version="simulated" \
      maintainer="AI-Datacenter"

RUN apk --no-cache add ca-certificates bash

# Create SONiC-like directory structure
RUN mkdir -p /etc/sonic /var/log/sonic /var/run/sonic

# SONiC device metadata
COPY sonic-device-metadata.json /etc/sonic/device_metadata.json
COPY sonic-banner.sh /etc/profile.d/sonic-banner.sh
RUN chmod +x /etc/profile.d/sonic-banner.sh

# Insight Agent binary
COPY --from=builder /insight-agent /usr/local/bin/insight-agent

ENTRYPOINT ["insight-agent"]
