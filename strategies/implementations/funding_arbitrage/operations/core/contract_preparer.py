"""Contract preparation utilities for exchange clients."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from decimal import Decimal

from funding_rate_service.core.opportunity_finder import OpportunityFinder

if TYPE_CHECKING:
    from exchange_clients.base_client import BaseExchangeClient


class ContractPreparer:
    """Handles contract initialization and context preparation for exchange clients."""
    
    @staticmethod
    async def ensure_contract_attributes(
        exchange_client: "BaseExchangeClient",
        symbol: str,
        logger: Any,
    ) -> bool:
        """
        Ensure the given exchange client is prepared to trade the symbol.
        
        Args:
            exchange_client: Exchange client to prepare
            symbol: Symbol to prepare for
            logger: Logger instance for logging
            
        Returns:
            True if contract attributes are ready, False otherwise
        """
        try:
            exchange_name = exchange_client.get_exchange_name()
            
            # Normalize symbol for cache lookup
            if hasattr(exchange_client, "normalize_symbol"):
                try:
                    normalized_symbol = exchange_client.normalize_symbol(symbol).upper()
                except Exception:
                    normalized_symbol = symbol.upper()
            else:
                normalized_symbol = symbol.upper()
            
            # Check if this symbol is already known to be untradeable on this exchange
            if not OpportunityFinder.is_symbol_tradeable(exchange_name, symbol):
                logger.debug(
                    f"⏭️  [{exchange_name.upper()}] Skipping {symbol} - known to be untradeable "
                    "(cached from previous attempt)"
                )
                return False
            
            config_ticker = getattr(exchange_client.config, "ticker", "")
            contract_cache = getattr(exchange_client, "_contract_id_cache", {})
            
            # ContractIdCache supports dict-like access
            if hasattr(contract_cache, 'get'):
                has_cached_contract = normalized_symbol in contract_cache
                cached_contract = contract_cache.get(normalized_symbol)
            elif isinstance(contract_cache, dict):
                has_cached_contract = normalized_symbol in contract_cache
                cached_contract = contract_cache.get(normalized_symbol)
            else:
                has_cached_contract = False
                cached_contract = None
            
            base_missing = getattr(exchange_client, "base_amount_multiplier", None) is None
            price_missing = getattr(exchange_client, "price_multiplier", None) is None
            current_contract = getattr(exchange_client.config, "contract_id", None)
            contract_mismatch = (
                has_cached_contract
                and cached_contract is not None
                and current_contract is not None
                and str(current_contract) != str(cached_contract)
            )

            config_missing = not hasattr(exchange_client.config, "contract_id")
            ticker_generic = str(config_ticker).upper() in {"", "ALL", "MULTI", "MULTI_SYMBOL"}

            needs_refresh = (
                config_missing
                or ticker_generic
                or not has_cached_contract
                or base_missing
                or price_missing
                or contract_mismatch
            )

            should_apply_metadata = False
            if exchange_name == "lighter":
                metadata_cache = getattr(exchange_client, "_market_metadata", {})
                if normalized_symbol in metadata_cache:
                    should_apply_metadata = True
                else:
                    needs_refresh = True

            if needs_refresh or should_apply_metadata:
                if needs_refresh:
                    logger.info(
                        f"🔧 [{exchange_name.upper()}] Initializing contract attributes for {symbol}"
                    )

                original_ticker = exchange_client.config.ticker
                exchange_client.config.ticker = symbol

                try:
                    contract_id, tick_size = await exchange_client.get_contract_attributes()
                    if not contract_id:
                        logger.warning(
                            f"❌ [{exchange_name.upper()}] Symbol {symbol} initialization returned empty contract_id"
                        )
                        OpportunityFinder.mark_symbol_untradeable(exchange_name, symbol)
                        return False

                    if needs_refresh:
                        logger.info(
                            f"✅ [{exchange_name.upper()}] {symbol} initialized → contract_id={contract_id}, tick_size={tick_size}"
                        )

                except ValueError as exc:
                    error_msg = str(exc).lower()
                    if "not found" in error_msg or "not supported" in error_msg or "not tradeable" in error_msg:
                        logger.warning(
                            f"⚠️  [{exchange_name.upper()}] Symbol {symbol} is NOT TRADEABLE on {exchange_name}"
                        )
                        OpportunityFinder.mark_symbol_untradeable(exchange_name, symbol)
                        return False
                    raise
                finally:
                    exchange_client.config.ticker = original_ticker

            return True

        except Exception as exc:
            logger.error(
                f"❌ [{exchange_client.get_exchange_name().upper()}] Failed to ensure contract attributes for {symbol}: {exc}"
            )
            return False
    
    @staticmethod
    async def prepare_contract_context(
        client: "BaseExchangeClient",
        symbol: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        contract_hint: Optional[Any] = None,
        logger: Any,
    ) -> Optional[Any]:
        """
        Ensure the exchange client is configured with the correct contract metadata.

        Closing legs often happens long after a position was opened. Some exchange
        clients reset their cached contract identifiers (contract_id, ticker, base
        multipliers) between runs, so we re-hydrate them on demand using the live
        snapshot metadata and connector helpers.
        
        Args:
            client: Exchange client
            symbol: Symbol to prepare
            metadata: Optional metadata dictionary
            contract_hint: Optional contract ID hint
            logger: Logger instance
            
        Returns:
            Resolved contract_id, or None if unable to resolve
        """
        metadata = metadata or {}
        config = getattr(client, "config", None)

        def _is_valid_contract(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return False
                if stripped.upper() in {"ALL", "MULTI", "MULTI_SYMBOL"}:
                    return False
                return True
            if isinstance(value, (int, Decimal)):
                return value != 0
            return True

        candidate_ids: List[Any] = [
            contract_hint,
            metadata.get("contract_id"),
            metadata.get("market_id"),
            metadata.get("backpack_symbol"),
            metadata.get("exchange_symbol"),
        ]
        if config is not None:
            candidate_ids.append(getattr(config, "contract_id", None))

        resolved_contract: Optional[Any] = next(
            (cid for cid in candidate_ids if _is_valid_contract(cid)), None
        )

        if not _is_valid_contract(resolved_contract) and hasattr(client, "normalize_symbol"):
            try:
                normalized = client.normalize_symbol(symbol)
            except Exception:
                normalized = None
            if _is_valid_contract(normalized):
                resolved_contract = normalized

        # Try to restore multipliers from metadata cache (saves 300 weight REST call!)
        if metadata:
            if hasattr(client, "base_amount_multiplier") and getattr(client, "base_amount_multiplier", None) is None:
                cached_base = metadata.get("base_amount_multiplier")
                if cached_base is not None:
                    try:
                        setattr(client, "base_amount_multiplier", cached_base)
                    except Exception:
                        pass
            
            if hasattr(client, "price_multiplier") and getattr(client, "price_multiplier", None) is None:
                cached_price = metadata.get("price_multiplier")
                if cached_price is not None:
                    try:
                        setattr(client, "price_multiplier", cached_price)
                    except Exception:
                        pass
        
        base_multiplier_missing = hasattr(client, "base_amount_multiplier") and getattr(
            client, "base_amount_multiplier", None
        ) is None
        price_multiplier_missing = hasattr(client, "price_multiplier") and getattr(
            client, "price_multiplier", None
        ) is None
        needs_refresh = not _is_valid_contract(resolved_contract)

        if (needs_refresh or base_multiplier_missing or price_multiplier_missing) and hasattr(
            client, "get_contract_attributes"
        ):
            ticker_restore = None
            candidate_ticker = (
                metadata.get("symbol")
                or metadata.get("backpack_symbol")
                or metadata.get("exchange_symbol")
                or symbol
            )
            if config is not None and candidate_ticker:
                ticker_restore = getattr(config, "ticker", None)
                try:
                    setattr(config, "ticker", candidate_ticker)
                except Exception:
                    ticker_restore = None

            try:
                attr = await client.get_contract_attributes()
                refreshed_id: Optional[Any]
                if isinstance(attr, tuple):
                    refreshed_id = attr[0]
                else:
                    refreshed_id = attr
                if _is_valid_contract(refreshed_id):
                    resolved_contract = refreshed_id
            except Exception as exc:
                logger.warning(
                    f"⚠️ [{client.get_exchange_name().upper()}] Failed to refresh contract attributes "
                    f"for {symbol}: {exc}"
                )
            finally:
                if ticker_restore is not None and config is not None:
                    try:
                        setattr(config, "ticker", ticker_restore)
                    except Exception:
                        pass

        if config is not None:
            try:
                if _is_valid_contract(resolved_contract):
                    setattr(config, "contract_id", resolved_contract)
                ticker_value = getattr(config, "ticker", None)
                if not ticker_value or str(ticker_value).upper() in {"ALL", "MULTI", "MULTI_SYMBOL"}:
                    setattr(config, "ticker", symbol)
            except Exception:
                pass

        # Surface the resolved contract_id to callers and leg metadata
        if _is_valid_contract(resolved_contract):
            metadata.setdefault("contract_id", resolved_contract)
            
            # Cache multipliers to metadata to avoid expensive get_contract_attributes() on next session
            if hasattr(client, "base_amount_multiplier"):
                base_mult = getattr(client, "base_amount_multiplier", None)
                if base_mult is not None:
                    metadata.setdefault("base_amount_multiplier", base_mult)
            
            if hasattr(client, "price_multiplier"):
                price_mult = getattr(client, "price_multiplier", None)
                if price_mult is not None:
                    metadata.setdefault("price_multiplier", price_mult)
            
            return resolved_contract
        return None

