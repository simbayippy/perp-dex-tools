.PHONY: help install clean test install-supervisor

# Default Python version
PYTHON := python3
VENV := venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip
PYTEST ?= pytest
SUPERVISOR_CONFIG := /etc/supervisor/supervisord.conf
SUPERVISOR_RPC_PORT := 127.0.0.1:9001

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Display this help message
	@echo "$(GREEN)perp-dex-tools - Simple Installation$(NC)"
	@echo ""
	@echo "Usage:"
	@echo "  $(YELLOW)make install$(NC)           - Install all dependencies (Python + Supervisor)"
	@echo "  $(YELLOW)make install-supervisor$(NC) - Install and configure Supervisor only"
	@echo "  $(YELLOW)make test$(NC)              - Run pytest suite in ./tests"
	@echo "  $(YELLOW)make clean$(NC)             - Remove virtual environment"
	@echo "  $(YELLOW)make help$(NC)              - Show this help"

install: ## Install all dependencies (Python + Supervisor)
	@echo "$(YELLOW)1. Creating and activating virtual environment...$(NC)"
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip
	@echo "$(YELLOW)2. Installing funding service dependencies (includes pytz)...$(NC)"
	@$(PIP) install -r funding_rate_service/requirements.txt
	@echo "$(YELLOW)3. Installing exchange clients with all SDKs...$(NC)"
	@$(PIP) install -e './exchange_clients[all]'
	@echo "$(GREEN)✅ Python dependencies installed!$(NC)"
	@echo ""
	@echo "$(YELLOW)4. Installing and configuring Supervisor...$(NC)"
	@$(MAKE) install-supervisor
	@echo ""
	@echo "$(GREEN)✅ Complete installation finished!$(NC)"
	@echo ""
	@echo "$(YELLOW)To activate the virtual environment:$(NC)"
	@echo "  source $(VENV)/bin/activate"

clean: ## Remove virtual environment
	@echo "$(YELLOW)Removing virtual environment...$(NC)"
	@rm -rf $(VENV)
	@echo "$(GREEN)✅ Cleanup completed$(NC)"

test: ## Run all pytest suites under ./tests
	@echo "$(YELLOW)Running pytest ($(PYTEST))...$(NC)"
	@$(PYTEST) tests

install-supervisor: ## Install and configure Supervisor with XML-RPC
	@echo "$(YELLOW)Installing and configuring Supervisor...$(NC)"
	@echo ""
	@echo "$(YELLOW)1. Checking if Supervisor is installed...$(NC)"
	@if command -v supervisord >/dev/null 2>&1; then \
		echo "$(GREEN)✅ Supervisor is already installed$(NC)"; \
	else \
		echo "$(YELLOW)   Installing Supervisor...$(NC)"; \
		sudo apt-get update -qq && sudo apt-get install -y supervisor || (echo "$(RED)❌ Failed to install Supervisor. Please run: sudo apt-get update && sudo apt-get install -y supervisor$(NC)" && exit 1); \
		echo "$(GREEN)✅ Supervisor installed$(NC)"; \
	fi
	@echo ""
	@echo "$(YELLOW)2. Checking XML-RPC configuration...$(NC)"
	@if [ -f $(SUPERVISOR_CONFIG) ] && sudo grep -q "^\[inet_http_server\]" $(SUPERVISOR_CONFIG) 2>/dev/null && sudo grep -A 1 "^\[inet_http_server\]" $(SUPERVISOR_CONFIG) 2>/dev/null | grep -q "port=$(SUPERVISOR_RPC_PORT)"; then \
		echo "$(GREEN)✅ XML-RPC is already configured$(NC)"; \
	else \
		echo "$(YELLOW)   Adding XML-RPC configuration...$(NC)"; \
		if [ ! -f $(SUPERVISOR_CONFIG) ] || ! sudo grep -q "^\[inet_http_server\]" $(SUPERVISOR_CONFIG) 2>/dev/null; then \
			echo "" | sudo tee -a $(SUPERVISOR_CONFIG) > /dev/null 2>&1 || true; \
			echo "[inet_http_server]" | sudo tee -a $(SUPERVISOR_CONFIG) > /dev/null; \
			echo "port=$(SUPERVISOR_RPC_PORT)" | sudo tee -a $(SUPERVISOR_CONFIG) > /dev/null; \
			echo "$(GREEN)✅ XML-RPC configuration added$(NC)"; \
		else \
			echo "$(YELLOW)   Updating XML-RPC port configuration...$(NC)"; \
			sudo sed -i '/^\[inet_http_server\]/,/^\[/ { s/^port=.*/port=$(SUPERVISOR_RPC_PORT)/; }' $(SUPERVISOR_CONFIG) 2>/dev/null || \
			(echo "port=$(SUPERVISOR_RPC_PORT)" | sudo tee -a $(SUPERVISOR_CONFIG) > /dev/null); \
			echo "$(GREEN)✅ XML-RPC port updated$(NC)"; \
		fi; \
	fi
	@echo ""
	@echo "$(YELLOW)3. Starting and enabling Supervisor...$(NC)"
	@sudo systemctl enable supervisor >/dev/null 2>&1 || true
	@sudo systemctl restart supervisor || (echo "$(RED)❌ Failed to restart Supervisor$(NC)" && exit 1)
	@sleep 2
	@if sudo systemctl is-active --quiet supervisor; then \
		echo "$(GREEN)✅ Supervisor is running$(NC)"; \
	else \
		echo "$(RED)❌ Supervisor failed to start. Check logs: sudo journalctl -u supervisor -n 50$(NC)"; \
		exit 1; \
	fi
	@echo ""
	@echo "$(YELLOW)4. Verifying XML-RPC connection...$(NC)"
	@if $(PYTHON) -c "import xmlrpc.client; s = xmlrpc.client.ServerProxy('http://$(SUPERVISOR_RPC_PORT)/RPC2'); version = s.supervisor.getVersion(); print('Supervisor version:', version)" 2>/dev/null; then \
		echo "$(GREEN)✅ XML-RPC connection successful$(NC)"; \
	else \
		echo "$(RED)❌ XML-RPC connection failed. Checking port...$(NC)"; \
		if sudo netstat -tlnp 2>/dev/null | grep -q ":9001" || sudo ss -tlnp 2>/dev/null | grep -q ":9001"; then \
			echo "$(YELLOW)   Port 9001 is listening, but XML-RPC test failed. Check Supervisor logs:$(NC)"; \
			echo "$(YELLOW)   sudo tail -50 /var/log/supervisor/supervisord.log$(NC)"; \
		else \
			echo "$(RED)   Port 9001 is not listening. Supervisor XML-RPC may not be enabled.$(NC)"; \
		fi; \
		exit 1; \
	fi
	@echo ""
	@echo "$(GREEN)✅ Supervisor installation and configuration complete!$(NC)"
	@echo ""
	@echo "$(YELLOW)Supervisor is ready to manage strategy processes.$(NC)"
