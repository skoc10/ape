# Works with BuildKit (multi-arch through TARGETARCH) and with the classic builder.
FROM golang:1.27 AS build
ARG TARGETARCH
ARG VERSION=dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH:-$(go env GOARCH)} \
    go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/ape-agent ./cmd/ape-agent

# Distroless static: no shell, no package manager, runs as uid 65532.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ape-agent /ape-agent
USER 65532:65532
ENTRYPOINT ["/ape-agent"]
