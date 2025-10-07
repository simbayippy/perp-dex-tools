.PHONY: help install clean

# Default Python version
PYTHON := python3
VENV := venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

help: ## Display this help message
	@echo "$(GREEN)perp-dex-tools - Simple Installation$(NC)"
	@echo ""
	@echo "Usage:"
	@echo "  $(YELLOW)make install$(NC)  - Install all dependencies"
	@echo "  $(YELLOW)make clean$(NC)    - Remove virtual environment"
	@echo "  $(YELLOW)make help$(NC)     - Show this help"

install: ## Install all dependencies
	@echo "$(YELLOW)1. Creating and activating virtual environment...$(NC)"
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip
	@echo "$(YELLOW)2. Installing funding service dependencies (includes pytz)...$(NC)"
	@$(PIP) install -r funding_rate_service/requirements.txt
	@echo "$(YELLOW)3. Installing exchange clients with all SDKs...$(NC)"
	@$(PIP) install -e './exchange_clients[all]'
	@echo "$(GREEN)✅ Installation complete!$(NC)"
	@echo ""
	@echo "$(YELLOW)To activate the virtual environment:$(NC)"
	@echo "  source $(VENV)/bin/activate"

clean: ## Remove virtual environment
	@echo "$(YELLOW)Removing virtual environment...$(NC)"
	@rm -rf $(VENV)
	@echo "$(GREEN)✅ Cleanup completed$(NC)"
