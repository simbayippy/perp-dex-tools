"""
Account manager module for Backpack client.

Handles account queries, balance, and leverage information.
"""

import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional

from exchange_clients.backpack.client.utils.helpers import to_decimal


class BackpackAccountManager:
    """
    Account manager for Backpack exchange.
    
    Handles:
    - Account balance queries
    - Leverage information
    """
    
    def __init__(
        self,
        account_client: Any,
        public_client: Any,
        config: Any,
        logger: Any,
        ensure_exchange_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize account manager.
        
        Args:
            account_client: Backpack Account client instance
            public_client: Backpack Public client instance
            config: Trading configuration object
            logger: Logger instance
            ensure_exchange_symbol_fn: Function to ensure exchange symbol format
        """
        self.account_client = account_client
        self.public_client = public_client
        self.config = config
        self.logger = logger
        self.ensure_exchange_symbol = ensure_exchange_symbol_fn or (lambda s: s)

    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Fetch available account balance.

        Returns the available USDC balance if present, otherwise None.
        """
        try:
            balances = await asyncio.to_thread(self.account_client.get_balances)
        except Exception as exc:
            self.logger.warning(f"[BACKPACK] Failed to fetch balances: {exc}")
            return None

        balance = self._extract_available_balance(balances)
        if balance is None:
            self.logger.warning("[BACKPACK] Unable to determine available USDC balance")
        return balance

    def _extract_available_balance(self, payload: Any) -> Optional[Decimal]:
        """
        Attempt to extract the available USDC balance from Backpack's capital response.
        
        Args:
            payload: Raw balance response from API
            
        Returns:
            Available USDC balance or None
        """
        if payload is None:
            return None

        entries: List[Dict[str, Any]] = []

        if isinstance(payload, dict):
            for key in ("balances", "capital", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    entries = value
                    break
            else:
                if all(isinstance(v, dict) for v in payload.values()):
                    entries = [dict(symbol=k, **v) for k, v in payload.items()]
        elif isinstance(payload, list):
            entries = payload

        if not entries:
            return None

        total_available = Decimal("0")
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            asset = (
                entry.get("symbol")
                or entry.get("asset")
                or entry.get("currency")
                or entry.get("token")
            )

            asset_code = str(asset).upper()
            if asset_code not in {"USDC", "USD", "USDT"}:
                continue

            available_value: Optional[Decimal] = None
            for key in ("available", "availableBalance", "free", "freeBalance", "balanceAvailable"):
                if key not in entry or entry[key] is None:
                    continue
                try:
                    available_value = Decimal(str(entry[key]))
                except (ValueError, TypeError):
                    available_value = None
                else:
                    break

            if available_value is None:
                fallback = entry.get("total") or entry.get("quantity")
                if fallback is not None:
                    try:
                        available_value = Decimal(str(fallback))
                    except (ValueError, TypeError):
                        available_value = None

            if available_value is not None:
                total_available += available_value

        return total_available if total_available > 0 else None

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch leverage limits for symbol.

        Backpack publishes initial margin settings in the market metadata. We derive
        leverage as floor(1 / initial_margin) and surface the account-level cap as well.
        
        Args:
            symbol: Symbol to get leverage info for
            
        Returns:
            Dictionary with leverage information
        """
        exchange_symbol = self.ensure_exchange_symbol(symbol) or symbol
        result: Dict[str, Any] = {
            "max_leverage": None,
            "max_notional": None,
            "margin_requirement": None,
            "brackets": None,
            "account_leverage": None,
            "error": None,
        }

        def _normalize_fraction(raw: Any) -> Optional[Decimal]:
            fraction = to_decimal(raw, None)
            if fraction is None:
                return None
            if fraction > 1:
                if fraction >= 1000:
                    fraction = fraction / Decimal("10000")
                else:
                    fraction = fraction / Decimal("100")
            return fraction

        market_payload: Optional[Dict[str, Any]] = None
        try:
            market_payload = await asyncio.to_thread(
                self.public_client.http_client.get,
                self.public_client.get_market_url(exchange_symbol),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"[BACKPACK] Direct market lookup failed for {exchange_symbol}: {exc}")

        if not isinstance(market_payload, dict):
            try:
                markets = await asyncio.to_thread(self.public_client.get_markets)
            except Exception as exc:
                message = f"Failed to fetch markets list: {exc}"
                if self.logger:
                    self.logger.warning(f"[BACKPACK] {message}")
                result["error"] = message
                return result

            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    if market.get("symbol") == exchange_symbol:
                        market_payload = market
                        break
                if market_payload is None:
                    base_symbol = symbol.upper()
                    market_payload = next(
                        (
                            market
                            for market in markets
                            if isinstance(market, dict)
                            and (market.get("baseSymbol") == base_symbol or market.get("baseAsset") == base_symbol)
                            and (market.get("marketType") or "").upper() in {"PERP", "PERPETUAL"}
                        ),
                        None,
                    )

        if not isinstance(market_payload, dict):
            result["error"] = f"No market data available for {exchange_symbol}"
            return result

        perp_info = market_payload.get("perpInfo") or market_payload.get("perp_info") or market_payload

        imf_candidate = (
            perp_info.get("imfFunction")
            or perp_info.get("imf_function")
            or perp_info.get("initialMarginFunction")
            or perp_info.get("initial_margin_function")
            if isinstance(perp_info, dict)
            else None
        )

        imf_base = to_decimal(imf_candidate.get("base"), None) if isinstance(imf_candidate, dict) else None

        initial_margin_fraction = _normalize_fraction(perp_info.get("initialMarginFraction")) if isinstance(perp_info, dict) else None
        if initial_margin_fraction is None and isinstance(perp_info, dict):
            initial_margin_fraction = _normalize_fraction(
                perp_info.get("initialMargin") or perp_info.get("initial_margin") or perp_info.get("imf") or imf_base
            )

        max_leverage = None
        if initial_margin_fraction and initial_margin_fraction > 0:
            max_leverage = Decimal("1") / initial_margin_fraction
        elif imf_base and imf_base > 0:
            max_leverage = Decimal("1") / imf_base

        if max_leverage is not None:
            max_leverage = max_leverage.to_integral_value(rounding=ROUND_DOWN)

        max_notional = None
        if isinstance(perp_info, dict):
            max_notional = to_decimal(
                perp_info.get("openInterestLimit")
                or perp_info.get("riskLimitNotional")
                or perp_info.get("open_interest_limit"),
                None,
            )

        maintenance_margin_fraction = _normalize_fraction(
            perp_info.get("maintenanceMarginFraction")
            or perp_info.get("maintenanceMargin")
            or perp_info.get("maintenance_margin")
            or perp_info.get("mmf")
        ) if isinstance(perp_info, dict) else None

        brackets = [
            {
                "notional_cap": max_notional,
                "initial_margin": initial_margin_fraction or imf_base,
                "maintenance_margin": maintenance_margin_fraction,
                "max_leverage": max_leverage,
            }
        ] if max_leverage is not None else []

        if result["margin_requirement"] is None:
            result["margin_requirement"] = initial_margin_fraction or imf_base

        result["max_leverage"] = max_leverage
        result["max_notional"] = max_notional
        result["brackets"] = brackets or None
        result["maintenance_margin"] = maintenance_margin_fraction

        # Account-level leverage cap (optional)
        try:
            account_info = await asyncio.to_thread(self.account_client.get_account)
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"[BACKPACK] Unable to fetch account leverage cap: {exc}")
            account_info = None

        if isinstance(account_info, dict):
            leverage_limit = account_info.get("leverageLimit") or account_info.get("leverage_limit")
            account_leverage = to_decimal(leverage_limit, None)
            if account_leverage and account_leverage > 0:
                result["account_leverage"] = account_leverage

        if result["max_leverage"] is None:
            result["error"] = f"Unable to determine leverage limits for {exchange_symbol}"

        # Log leverage info summary
        if self.logger and result["max_leverage"] is not None:
            margin_req = result["margin_requirement"]
            margin_pct = (margin_req * 100) if margin_req else None
            
            self.logger.info(
                f"📊 [BACKPACK] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {result['max_leverage']:.1f}x\n"
                f"  - Account leverage: {result.get('account_leverage', 'N/A')}x\n"
                f"  - Max notional: {result['max_notional'] or 'None'}\n"
                f"  - Margin requirement: {margin_req} ({margin_pct:.1f}%)" if margin_pct else f"  - Margin requirement: {margin_req}"
            )

        return result

