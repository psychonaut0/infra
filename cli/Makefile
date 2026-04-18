VERSION := $(shell git -C .. describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  := $(shell git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)
LDFLAGS := -X main.Version=$(VERSION) -X main.Commit=$(COMMIT)
PREFIX  ?= $(HOME)/.local/bin
TARGETS := proxmoxmain proxmoxnode ct-backup

.DEFAULT_GOAL := install

.PHONY: build install deploy test fmt clean

build:
	mkdir -p bin
	go build -ldflags "$(LDFLAGS)" -o bin/infra ./cmd/infra

install: build
	install -m 755 bin/infra $(PREFIX)/infra
	@echo "Installed: $(PREFIX)/infra ($(VERSION))"

deploy: build
	@for host in $(TARGETS); do \
		echo "→ $$host"; scp -q bin/infra root@$$host:/usr/local/bin/infra; \
	done

test:
	go test ./...

fmt:
	gofmt -w .

clean:
	rm -rf bin
