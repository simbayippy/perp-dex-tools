.PHONY: help install-all setup venv install-trading install-exchange-clients install-funding clean test verify

# Default Python version
PYTHON := python3
VENV := venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip
PYTHON_VENV := $(VENV_BIN)/python

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

##@ General

help: ## Display this help message
	@echo "$(GREEN)perp-dex-tools - Makefile Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make $(YELLOW)<target>$(NC)\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(GREEN)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Setup & Installation

install-all: venv install-trading install-exchange-clients install-funding verify ## 🚀 Install everything (recommended for first-time setup)
	@echo "$(GREEN)✅ All dependencies installed successfully!$(NC)"
	@echo ""
	@echo "$(YELLOW)To activate the virtual environment:$(NC)"
	@echo "  source $(VENV)/bin/activate"
	@echo ""
	@echo "$(YELLOW)To run the trading bot:$(NC)"
	@echo "  python runbot.py --env-file .env --exchange lighter --ticker BTC --quantity 0.00273 --take-profit 0 --max-orders 50 --wait-time 20 --grid-step 0.06"
	@echo ""
	@echo "$(YELLOW)To run the funding rate service:$(NC)"
	@echo "  cd funding_rate_service && uvicorn main:app --reload"

setup: install-all ## Alias for install-all

venv: ## Create Python virtual environment
	@echo "$(YELLOW)Creating virtual environment...$(NC)"
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@echo "$(GREEN)✅ Virtual environment created at ./$(VENV)$(NC)"

install-trading: venv ## Install trading client dependencies (root requirements.txt)
	@echo "$(YELLOW)Installing trading client dependencies...$(NC)"
	@$(PIP) install --upgrade pip setuptools wheel
	@$(PIP) install -r requirements.txt
	@echo "$(GREEN)✅ Trading client dependencies installed$(NC)"

install-exchange-clients: venv ## Install exchange clients library with all SDKs
	@echo "$(YELLOW)Installing exchange clients library with all exchanges...$(NC)"
	@$(PIP) install -e './exchange_clients[all]'
	@echo "$(GREEN)✅ Exchange clients library installed$(NC)"

install-funding: venv ## Install funding rate service dependencies
	@echo "$(YELLOW)Installing funding rate service dependencies...$(NC)"
	@$(PIP) install -r funding_rate_service/requirements.txt
	@echo "$(GREEN)✅ Funding rate service dependencies installed$(NC)"

##@ Selective Exchange Installation

install-lighter: venv ## Install only Lighter exchange
	@echo "$(YELLOW)Installing Lighter exchange...$(NC)"
	@$(PIP) install -e './exchange_clients[lighter]'
	@echo "$(GREEN)✅ Lighter exchange installed$(NC)"

install-grvt: venv ## Install only GRVT exchange
	@echo "$(YELLOW)Installing GRVT exchange...$(NC)"
	@$(PIP) install -e './exchange_clients[grvt]'
	@echo "$(GREEN)✅ GRVT exchange installed$(NC)"

install-edgex: venv ## Install only EdgeX exchange
	@echo "$(YELLOW)Installing EdgeX exchange...$(NC)"
	@$(PIP) install -e './exchange_clients[edgex]'
	@echo "$(GREEN)✅ EdgeX exchange installed$(NC)"

install-backpack: venv ## Install only Backpack exchange
	@echo "$(YELLOW)Installing Backpack exchange...$(NC)"
	@$(PIP) install -e './exchange_clients[backpack]'
	@echo "$(GREEN)✅ Backpack exchange installed$(NC)"

##@ Development

dev: venv install-trading install-exchange-clients ## Install dependencies for development (without funding service)
	@echo "$(YELLOW)Installing development tools...$(NC)"
	@$(PIP) install pytest pytest-asyncio black ruff mypy
	@echo "$(GREEN)✅ Development environment ready$(NC)"

test: ## Run tests
	@echo "$(YELLOW)Running tests...$(NC)"
	@$(PYTHON_VENV) -m pytest tests/ -v
	@echo "$(GREEN)✅ Tests completed$(NC)"

verify: ## Verify installation by checking exchange factory
	@echo "$(YELLOW)Verifying installation...$(NC)"
	@$(PYTHON_VENV) -c "from exchange_clients.factory import ExchangeFactory; print('Available exchanges:', ExchangeFactory.get_supported_exchanges()); print('✅ Installation verified!')"

format: ## Format code with black
	@echo "$(YELLOW)Formatting code...$(NC)"
	@$(VENV_BIN)/black . --exclude $(VENV)
	@echo "$(GREEN)✅ Code formatted$(NC)"

lint: ## Lint code with ruff
	@echo "$(YELLOW)Linting code...$(NC)"
	@$(VENV_BIN)/ruff check .
	@echo "$(GREEN)✅ Linting completed$(NC)"

##@ Database (Funding Rate Service)

db-init: ## Initialize database schema
	@echo "$(YELLOW)Initializing database...$(NC)"
	@$(PYTHON_VENV) funding_rate_service/scripts/init_db.py
	@echo "$(GREEN)✅ Database initialized$(NC)"

db-seed: ## Seed database with DEX data
	@echo "$(YELLOW)Seeding database...$(NC)"
	@$(PYTHON_VENV) funding_rate_service/scripts/seed_dexes.py
	@echo "$(GREEN)✅ Database seeded$(NC)"

db-setup: db-init db-seed ## Initialize and seed database

##@ Cleanup

clean: ## Remove virtual environment and cache files
	@echo "$(YELLOW)Cleaning up...$(NC)"
	@rm -rf $(VENV)
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Cleanup completed$(NC)"

clean-cache: ## Remove Python cache files only (keep venv)
	@echo "$(YELLOW)Cleaning cache files...$(NC)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)✅ Cache cleaned$(NC)"

reinstall: clean install-all ## Clean and reinstall everything

##@ Running Services

run-trading: ## Run trading bot (requires --exchange, --ticker, etc. arguments)
	@echo "$(YELLOW)Starting trading bot...$(NC)"
	@echo "$(RED)Note: You need to provide arguments. Example:$(NC)"
	@echo "  python runbot.py --exchange lighter --ticker BTC --quantity 0.01 --direction buy"

run-funding: ## Run funding rate service API
	@echo "$(YELLOW)Starting funding rate service...$(NC)"
	@cd funding_rate_service && $(PYTHON_VENV) -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

run-funding-bg: ## Run funding rate service in background
	@echo "$(YELLOW)Starting funding rate service in background...$(NC)"
	@cd funding_rate_service && $(PYTHON_VENV) -m uvicorn main:app --host 0.0.0.0 --port 8000 &
	@echo "$(GREEN)✅ Service started on http://localhost:8000$(NC)"

##@ Docker

docker-up: ## Start PostgreSQL with Docker Compose
	@echo "$(YELLOW)Starting PostgreSQL...$(NC)"
	@cd funding_rate_service && docker-compose up -d
	@echo "$(GREEN)✅ PostgreSQL started$(NC)"

docker-down: ## Stop PostgreSQL
	@echo "$(YELLOW)Stopping PostgreSQL...$(NC)"
	@cd funding_rate_service && docker-compose down
	@echo "$(GREEN)✅ PostgreSQL stopped$(NC)"

docker-logs: ## View PostgreSQL logs
	@cd funding_rate_service && docker-compose logs -f

##@ Information

check-python: ## Check Python version
	@echo "$(YELLOW)Python version:$(NC)"
	@$(PYTHON) --version
	@echo ""
	@echo "$(YELLOW)pip version:$(NC)"
	@$(PYTHON) -m pip --version

list-exchanges: verify ## List all available exchanges
	@echo ""

deps: ## Show installed dependencies
	@echo "$(YELLOW)Installed packages:$(NC)"
	@$(PIP) list

show-venv: ## Show virtual environment info
	@echo "$(YELLOW)Virtual environment:$(NC) $(VENV)"
	@echo "$(YELLOW)Python binary:$(NC) $(PYTHON_VENV)"
	@echo "$(YELLOW)pip binary:$(NC) $(PIP)"
	@test -d $(VENV) && echo "$(GREEN)✅ Virtual environment exists$(NC)" || echo "$(RED)❌ Virtual environment not found$(NC)"

