# Position Command Enhancements

## Description

Enhance the `/positions` command in the Telegram bot to display additional per-leg information that would be useful for position monitoring.

## Current State

The position command (`telegram_bot_service/utils/formatters.py`) currently displays:
- Per-leg: entry price, mark price, quantity, unrealized PnL, funding accrued, funding APY (in yield section)
- Summary: net PnL, total uPnL, total funding, position size

## Requested Enhancements

### 1. Add Funding APY Per Leg (in Exchange Section)

**Current**: Funding APY is shown in the "Yield (Annualized)" section at the top

**Requested**: Show funding APY for each leg in the per-exchange section (where entry/mark prices are shown)

**Location**: Should be displayed alongside other leg-specific metrics (entry, mark, qty, uPnL, funding)

### 2. Use Exchange Emojis

**Requested**: Add exchange-specific emojis/icons to make it easier to visually distinguish exchanges

**Examples**:
- Lighter: ⚡ 
- Aster: ⭐ 
- Backpack: 🎒 
- Paradex: 🎪 
- EdgeX: ⚡ 
- GRVT: 🟠

### 3. Add Liquidation Price Per Leg

**Requested**: Display the liquidation price for each leg in the per-exchange section

**Use Case**: Users need to know how close positions are to liquidation to manage risk

## Implementation Details

### Funding APY Per Leg

The funding APY is already calculated per leg (see `leg.get('funding_apy')` in formatter), but it's currently only shown in the yield section. Move or duplicate it to the per-leg section.

**Current code** (lines 172-175):
```python
if funding_apy is not None:
    apy_emoji = "📈" if funding_apy > 0 else "📉" if funding_apy < 0 else "➖"
    lines.append(f"  APY: {apy_emoji} <code>{funding_apy:.2f}%</code>")
```

**Location**: This is already in the per-leg section, but verify it's being populated correctly.

### Exchange Emojis

Create a mapping function:
```python
def get_exchange_emoji(dex_name: str) -> str:
    emoji_map = {
        'lighter': '⚡',
        'aster': '⭐',
        'backpack': '🎒',
        'paradex': '🎪',
        'edgex': '⚡',
        'grvt': '🟠',
    }
    return emoji_map.get(dex_name.lower(), '📊')
```

Use it when displaying exchange names:
```python
dex_emoji = get_exchange_emoji(dex)
lines.append(f"<b>{dex_emoji} {side_emoji} {dex}</b> ({side.upper()}{leverage_str})")
```

### Liquidation Price

**Challenge**: Need to fetch liquidation price from exchange snapshots or position data.

**Investigation Required**:
1. **Check if liquidation price is already fetched**:
   - Review `position_monitor.py` to see if `ExchangePositionSnapshot` includes `liquidation_price`
   - Check `exchange_clients/base_models.py` to see if `ExchangePositionSnapshot` has a `liquidation_price` field
   - Verify if exchange clients populate this field when fetching position snapshots
   - Look at `position_monitor.py` line 214 - there's already code checking for `snapshot.liquidation_price`

2. **Understand liquidation price behavior**:
   - **Is liquidation price fixed or dynamic?**
   - Liquidation price typically changes based on:
     - Margin usage (opening another position reduces available margin, changes liquidation price)
     - Leverage changes
     - Price movements (mark-to-market)
     - Funding payments affecting margin
   - **Conclusion**: Liquidation price is DYNAMIC and should be refreshed regularly via position monitoring

3. **Data Flow**:
   - Position monitor fetches snapshots → includes liquidation_price if available
   - Snapshot data stored in position metadata → `legs[dex]['liquidation_price']`
   - Formatter reads from metadata → displays liquidation price

**Display**: Add after mark price:
```python
liquidation_price = leg.get('liquidation_price')
if liquidation_price:
    mark_price = leg.get('mark_price')
    if mark_price:
        # Calculate distance to liquidation
        # For long: liquidation_price < mark_price (we're above liquidation)
        # For short: liquidation_price > mark_price (we're above liquidation)
        if side == 'long':
            distance_pct = ((mark_price - liquidation_price) / mark_price * 100) if mark_price > 0 else None
        else:  # short
            distance_pct = ((liquidation_price - mark_price) / mark_price * 100) if mark_price > 0 else None
        
        lines.append(f"  Liq: <code>${liquidation_price:.6f}</code>")
        if distance_pct is not None:
            lines.append(f"  Distance: <code>{distance_pct:.2f}%</code>")
    else:
        lines.append(f"  Liq: <code>${liquidation_price:.6f}</code>")
```

## Affected Files

- `telegram_bot_service/utils/formatters.py` - `_format_single_position()` method
- `strategies/implementations/funding_arbitrage/position_monitor.py` - Verify liquidation price is included in leg snapshots (line 214 already checks for it)
- `exchange_clients/base_models.py` - Verify `ExchangePositionSnapshot` includes `liquidation_price` field
- Exchange-specific clients - Verify they populate `liquidation_price` when fetching position snapshots

## Data Flow

1. **Position Monitor** (`position_monitor.py`):
   - Fetches position snapshots from exchanges
   - Calculates per-leg metrics including funding APY
   - Should include liquidation price in snapshots

2. **Control API** (`strategies/control/funding_arb_controller.py`):
   - Formats position data for API response
   - Includes leg data with all metrics

3. **Telegram Formatter** (`formatters.py`):
   - Formats API response for display
   - Adds emojis and formatting

## Testing

1. Verify funding APY appears in per-leg section
2. Verify exchange emojis display correctly
3. Verify liquidation price is fetched and displayed
4. Test with positions on different exchanges
5. Verify formatting looks good on mobile devices

## Notes

- Funding APY may already be in the per-leg section - verify current implementation
- Liquidation price may require additional API calls to exchanges
- Consider adding a "distance to liquidation" percentage for easier risk assessment

