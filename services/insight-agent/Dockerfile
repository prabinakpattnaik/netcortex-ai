FROM golang:1.23-alpine AS builder

WORKDIR /app

COPY go.mod go.sum* ./
RUN go mod download 2>/dev/null || true

COPY . .

RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /insight-agent ./cmd/main.go

# SONiC-style runtime environment
FROM alpine:3.19

LABEL description="SONiC OS Network Device with InsightAgent" \
      platform="SONiC" \
      component="edge"

RUN apk --no-cache add ca-certificates bash && \
    mkdir -p /etc/sonic /var/log/sonic

COPY --from=builder /insight-agent /usr/local/bin/insight-agent

ENTRYPOINT ["insight-agent"]
