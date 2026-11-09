IMAGE ?= divemate-cli
TAG   ?= latest
BINDIR ?= $(HOME)/.local/bin
CONFDIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/divemate-cli

.DEFAULT_GOAL := help

.PHONY: help build install config clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

build: ## Build the container image with podman
	podman build -t $(IMAGE):$(TAG) .

install: build ## Build, then install the dmcli wrapper on PATH
	install -D -m 0755 dmcli $(BINDIR)/dmcli
	@echo "installed $(BINDIR)/dmcli (ensure $(BINDIR) is on your PATH)"

config: ## Install the example config to ~/.config/divemate-cli (won't overwrite)
	@mkdir -p $(CONFDIR)
	@if [ -f $(CONFDIR)/config ]; then \
		echo "exists, not overwriting: $(CONFDIR)/config"; \
	else \
		install -m 0600 config.example $(CONFDIR)/config; \
		echo "wrote $(CONFDIR)/config — edit it with your paths"; \
	fi

clean: ## Remove the container image
	-podman rmi $(IMAGE):$(TAG)
