# Funding Rate Arbitrage Rebalancing Strategies

## Position Opening Logic: Who Decides?

### Answer: Hybrid Approach (Recommended)

**Funding Rate Service provides:**
- Ranked opportunities with calculated profitability
- `/opportunities/best` - Top opportunity
- `/opportunities?symbol=BTC&min_profit=0.001` - Filtered opportunities

**Client (perp_dex_tools) decides:**
- WHEN to open positions (based on your risk management)
- HOW MUCH capital to deploy
- WHICH opportunity to take (may not always be "best")
- Position sizing based on available capital
- Risk limits (max positions, max per DEX, etc.)

### Example Flow

```python
# Client calls funding service
opportunities = await funding_api.get_opportunities(
    min_profit=0.001,
    max_oi_usd=500_000,  # For point farming
    prioritize_dexes="lighter,grvt"
)

# Client applies ITS OWN logic
for opp in opportunities:
    if client.should_open_position(opp):  # Client's risk rules
        size = client.calculate_position_size(opp)  # Client's capital allocation
        await client.open_position(opp, size)
```

### Why Hybrid?

- **Service calculates** = Reusable analysis (you could add more clients later)
- **Client decides** = Your personal risk management and capital allocation
- **Clear boundary** = Service finds opportunities, client takes them

---

## Client Service  Implementation

### Core Components

#### 1. Position Tracker
```python
class Position:
    id: UUID
    symbol: str
    long_dex: str
    short_dex: str
    size_usd: Decimal
    entry_long_rate: Decimal
    entry_short_rate: Decimal
    entry_divergence: Decimal
    opened_at: datetime
    last_check: datetime
    status: str  # 'open', 'pending_close', 'closed'
```

#### 2. Position Monitor
```python
class PositionMonitor:
    async def check_all_positions(self):
        """Run every minute, aligned with funding service data collection"""
        for position in self.get_open_positions():
            current_rates = await self.funding_api.compare_rates(
                symbol=position.symbol,
                dex1=position.long_dex,
                dex2=position.short_dex
            )
            
            if self.rebalancer.should_rebalance(position, current_rates):
                await self.rebalance(position, current_rates)
```

#### 3. Rebalancing Strategy
```python
class RebalanceStrategy:
    def should_rebalance(self, position, current_rates) -> tuple[bool, str]:
        """
        Returns: (should_rebalance, reason)
        """
        # Strategy rules below
```

---

## Rebalancing Strategies

### Strategy 1: Profit Erosion Threshold
**When**: Divergence drops below X% of entry divergence

```python
def profit_erosion_check(position, current_rates):
    erosion = current_rates['divergence'] / position.entry_divergence
    
    if erosion < 0.3:  # Lost 70% of edge
        return True, "PROFIT_EROSION"
    return False, None
```

**Use case**: Exit before all profit disappears  
**Risk**: May close too early if rates normalize

---

### Strategy 2: Divergence Flip
**When**: Funding rate divergence becomes negative (you're now losing money)

```python
def divergence_flip_check(position, current_rates):
    if current_rates['divergence'] < 0:
        return True, "DIVERGENCE_FLIPPED"
    return False, None
```

**Use case**: Stop losses when market flips  
**Risk**: None - this is mandatory exit

---

### Strategy 3: Better Opportunity Available
**When**: New opportunity is significantly better than current position

```python
def better_opportunity_check(position, current_rates):
    best_opp = await funding_api.get_best_opportunity(
        symbol=position.symbol
    )
    
    # Calculate cost to rebalance
    exit_cost = position.size * 0.0004  # 4 bps
    entry_cost = position.size * 0.0004  # 4 bps
    rebalance_cost = exit_cost + entry_cost
    
    # Calculate expected profit difference over next 24h (3 funding periods)
    current_expected = current_rates['divergence'] * 3 * position.size
    new_expected = best_opp['divergence'] * 3 * position.size
    
    if (new_expected - current_expected - rebalance_cost) > position.size * 0.001:
        return True, "BETTER_OPPORTUNITY"
    return False, None
```

**Use case**: Maximize profitability by switching to better pairs  
**Risk**: Over-trading fees if you rebalance too often

---

### Strategy 4: Time-Based Exit
**When**: Position held for X hours/days regardless of profitability

```python
def time_based_exit(position):
    hours_held = (datetime.now() - position.opened_at).total_seconds() / 3600
    
    if hours_held >= 72:  # 3 days
        return True, "TIME_LIMIT"
    return False, None
```

**Use case**: Reduce exposure to changing market conditions  
**Risk**: May exit profitable positions prematurely

---

### Strategy 5: Funding Schedule Timing
**When**: Close just before funding payment, reopen after

```python
def funding_schedule_exit(position):
    next_funding = get_next_funding_time(position.long_dex)
    time_until = (next_funding - datetime.now()).total_seconds()
    
    # Close 15 minutes before funding to ensure exit
    if 0 < time_until < 900:  # 15 minutes
        return True, "PRE_FUNDING_EXIT"
    return False, None
```

**Use case**: Collect funding payment then reassess  
**Risk**: Miss rebalancing if new rates aren't fetched quickly

---

### Strategy 6: Open Interest Imbalance
**When**: OI ratio between DEXs becomes too skewed

```python
def oi_imbalance_check(position, current_market_data):
    oi_ratio = current_market_data['long_oi'] / current_market_data['short_oi']
    
    # Exit if one side becomes illiquid or too crowded
    if oi_ratio > 3.0 or oi_ratio < 0.33:
        return True, "OI_IMBALANCE"
    return False, None
```

**Use case**: Avoid positions where one side has extreme OI  
**Risk**: May exit positions that remain profitable

---

### Strategy 7: Combination Strategy (Recommended)
```python
class CombinedRebalanceStrategy:
    def should_rebalance(self, position, current_rates):
        # Critical exits (immediate)
        if current_rates['divergence'] < 0:
            return True, "DIVERGENCE_FLIPPED"
        
        # High priority exits
        if current_rates['divergence'] < position.entry_divergence * 0.2:
            return True, "SEVERE_EROSION"
        
        # Opportunity-based (cost-aware)
        better, reason = self.better_opportunity_check(position)
        if better:
            return True, reason
        
        # Time-based fallback
        hours_held = (datetime.now() - position.opened_at).total_seconds() / 3600
        if hours_held >= 168:  # 1 week max
            return True, "WEEKLY_REBALANCE"
        
        return False, None
```

---

## Recommended Configuration

### Conservative (Point Farming Focus)
```python
REBALANCE_CONFIG = {
    "min_erosion_threshold": 0.5,  # Exit at 50% profit loss
    "rebalance_cost_bps": 8,  # Total round-trip cost
    "min_profit_improvement": 0.002,  # Need 0.2% better to justify rebalance
    "max_position_age_hours": 168,  # Weekly rebalance
    "enable_better_opportunity": True,
    "check_interval_seconds": 60  # Aligned with data collection
}
```

### Aggressive (Profit Maximization)
```python
REBALANCE_CONFIG = {
    "min_erosion_threshold": 0.7,  # Exit at 30% profit loss
    "rebalance_cost_bps": 8,
    "min_profit_improvement": 0.0005,  # More aggressive switching
    "max_position_age_hours": 48,  # Rebalance every 2 days
    "enable_better_opportunity": True,
    "check_interval_seconds": 60
}
```

---
