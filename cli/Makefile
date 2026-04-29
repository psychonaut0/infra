VERSION := $(shell git -C .. describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  := $(shell git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)
LDFLAGS := -X main.Version=$(VERSION) -X main.Commit=$(COMMIT)
PREFIX  ?= $(HOME)/.local/bin

.DEFAULT_GOAL := install

.PHONY: build install snapshot test fmt clean

snapshot:
	go run ./cmd/snapshot-services .. > internal/discover/services.json

build: snapshot
	mkdir -p bin
	go build -ldflags "$(LDFLAGS)" -o bin/infra ./cmd/infra

install: build
	install -m 755 bin/infra $(PREFIX)/infra
	@echo "Installed: $(PREFIX)/infra ($(VERSION))"

test:
	go test ./...

fmt:
	gofmt -w .

clean:
	rm -rf bin
