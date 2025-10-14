# Trading Data Model Redesign

This document captures the proposed schema changes that will let us:

- Track strategy positions in a strategy-agnostic way (not hard-coded to two legs).
- Record which trading account/wallet was used so capacity limits can be applied per account.
- Store DEX-specific credential material in an extensible form without hard-coding column names.
- Prepare the codebase to support position scaling (multiple fills updating the same logical position).

Because we can wipe the current local Postgres instance, the plan assumes a clean rebuild of the affected tables.

## Naming Direction

The existing `strategy_positions` table is heavily tailored to funding arbitrage. The redesign promotes a simpler, general-purpose top-level table named `positions`. Leg-specific rows move to a companion table. This keeps naming short and avoids leaking strategy-specific assumptions into the schema.

| Current Name        | Proposed Name | Rationale                                           |
|---------------------|---------------|-----------------------------------------------------|
| `strategy_positions`| `positions`   | Generic entry point for any strategy.               |
| (new)               | `position_legs` | Captures long/short (or more) legs per position.   |
| (new, optional now) | `position_fills` | Audit trail when we scale an existing position.   |

Strategy-specific metadata still lives in JSONB columns so we can extend without migrations for minor tweaks.

## Target Schema

### positions
Logical ownership of a trade opportunity, regardless of how many legs it contains.

| Column             | Type           | Notes |
|--------------------|----------------|-------|
| `id`               | UUID PK        | Generated in code. |
| `strategy_name`    | VARCHAR(50)    | e.g. `funding_arbitrage`, `grid_bot`. |
| `account_id`       | UUID FK → `trading_accounts.id` (nullable) | Enables per-account capacity checks. |
| `symbol_id`        | INT FK → `symbols(id)` (nullable) | Optional because some strategies hold multiple symbols; can be null with legs covering the details. |
| `status`           | VARCHAR(20)    | `open`, `pending_close`, `closed`, etc. |
| `opened_at`        | TIMESTAMP      | When we created the logical position. |
| `closed_at`        | TIMESTAMP      | When the position finished (nullable). |
| `exit_reason`      | VARCHAR(50)    | Mirrors existing usage. |
| `pnl_usd`          | DECIMAL        | Final PnL once closed. |
| `metadata`         | JSONB          | Strategy-specific data (e.g. config snapshot). |
| `created_at`       | TIMESTAMP      | Default `NOW()`. |
| `updated_at`       | TIMESTAMP      | Updated via trigger. |

**Indexes / Constraints**
- `idx_positions_strategy_status (strategy_name, status)`.
- Optional uniqueness: `(strategy_name, account_id, symbol_id, status)` filtered on `status = 'open'` once we know what “duplicate” should mean for each strategy.

### position_legs
Represents each execution leg (long, short, hedge, etc.). A funding-arb position will insert two rows, but market-making strategies could insert many.

| Column             | Type           | Notes |
|--------------------|----------------|-------|
| `id`               | UUID PK        | Useful if we ever reference legs directly. |
| `position_id`      | UUID FK → `positions.id` | Cascade delete on position removal. |
| `dex_id`           | INT FK → `dexes(id)` | Links to the existing DEX lookup. |
| `account_id`       | UUID FK → `trading_accounts.id` | Allows multi-account positions (e.g. hedging across wallets). |
| `side`             | VARCHAR(12)    | `long`, `short`, `hedge`, etc. |
| `size_usd`         | DECIMAL        | Current notional exposure. |
| `entry_rate`       | DECIMAL        | Optional; the funding rate or price used at open. |
| `entry_price`      | DECIMAL        | For price-based strategies. |
| `exposure_usd`     | DECIMAL        | Current exposure; updated by the monitor. |
| `leverage`         | DECIMAL        | Optional leverage snapshot. |
| `metadata`         | JSONB          | Structure that currently lives under `position.metadata["legs"][dex]`. |
| `created_at`       | TIMESTAMP      | Default `NOW()`. |
| `updated_at`       | TIMESTAMP      | Trigger-managed. |

**Indexes / Constraints**
- `UNIQUE(position_id, side)` covers today’s two-leg pattern; drop or extend (`..., leg_index`) if a strategy wants more than one `long` per position.
- `idx_position_legs_position` for quick joins.
- `idx_position_legs_account` so we can ask “how many active legs use this account?” quickly.

### position_fills (optional initially)
If we need to track scale-in/out operations, this table records each atomic execution. We can defer implementing this until we actually need per-fill auditing.

| Column             | Type           | Notes |
|--------------------|----------------|-------|
| `id`               | UUID PK        | |
| `position_id`      | UUID FK → `positions.id` | |
| `leg_id`           | UUID FK → `position_legs.id` | |
| `executed_at`      | TIMESTAMP      | |
| `fill_size_usd`    | DECIMAL        | Positive for adds, negative for reductions. |
| `fill_price`       | DECIMAL        | Optional price info. |
| `fees_paid_usd`    | DECIMAL        | Execution fees. |
| `slippage_usd`     | DECIMAL        | |
| `metadata`         | JSONB          | Raw exchange response, tx hashes, etc. |

This gives the “expand existing position” story a clean home.

### trading_accounts
Master table for wallets or sub-accounts.

| Column             | Type           | Notes |
|--------------------|----------------|-------|
| `id`               | UUID PK        | |
| `label`            | VARCHAR(64)    | Friendly name (`core-edgeX`, `backpack-main`). |
| `wallet_address`   | VARCHAR(128)   | Optional depending on DEX. |
| `owner`            | VARCHAR(64)    | Team/user owning the account (optional). |
| `chain`            | VARCHAR(32)    | Canonical chain/network identifier. |
| `metadata`         | JSONB          | Extra info (e.g. funding limits, tags). |
| `created_at`       | TIMESTAMP      | |
| `updated_at`       | TIMESTAMP      | |

### account_credentials
Holds DEX-specific secrets or references per account.

| Column             | Type           | Notes |
|--------------------|----------------|-------|
| `id`               | UUID PK        | |
| `account_id`       | UUID FK → `trading_accounts.id` | |
| `dex_id`           | INT FK → `dexes(id)` | |
| `credential_type`  | VARCHAR(32)    | e.g. `api_key`, `api_secret`, `private_key`, `stark_key`. |
| `identifier`       | VARCHAR(128)   | Public identifier when relevant (e.g. API key id). |
| `secret_ref`       | VARCHAR(256)   | Reference to the stored secret (see below). |
| `metadata`         | JSONB          | Extra fields (`environment`, rate limits, notes). |
| `created_at`       | TIMESTAMP      | |
| `updated_at`       | TIMESTAMP      | |

**Secret References**

`secret_ref` is a pointer to where the actual sensitive material lives. Options:

1. **Secrets manager**: store a URI like `aws-sm://prod/trading/backpack-secret` or `vault://kv/accounts/backpack`. The app resolves it at runtime via the relevant SDK.
2. **Encrypted blob**: store a KMS-encrypted base64 payload (`kms:gcp:...`) if we need local operation without an external secrets manager.
3. **Fallback (today)**: while bootstrapping, we can store plain secrets encrypted with a symmetric key and track the key rotation plan in `metadata`. This still improves structure and lets us migrate to a dedicated manager later without schema changes.

By splitting identifier vs. secret reference, we can add new credential types per DEX by inserting more rows—no schema change needed.

## Extensibility Notes

- **New DEX credentials**: add rows to `account_credentials` with new `credential_type` values; strategy code only needs to know the label it cares about.
- **Multiple accounts per strategy**: link each leg to its account via `position_legs.account_id`. Capacity logic can aggregate open legs per account.
- **Supporting non-funding strategies**: add custom data either in `position_legs.metadata` or new tables keyed by `position_id`. The base tables do not assume funding-rate-specific columns.
- **Future multi-leg strategies**: extend `position_legs` with a `leg_index` or `leg_group` if we ever need more than two legs per side.

## Migration Plan

1. **Schema creation**  
   - Write a migration that creates `positions`, `position_legs`, `trading_accounts`, and `account_credentials`.  
   - Create triggers for `updated_at` where needed.  
   - Leave the old tables untouched until code is ready.

2. **Code updates**  
   - Update `FundingArbPositionManager` to read/write the new tables.  
   - Adjust `PositionOpener` to look for existing open positions (matching `strategy_name`, `account_id`, `symbol_id`, `legs`) and update instead of creating duplicates.  
   - Update monitors/closers to join through `position_legs`.

3. **Data migration (optional)**  
   - Because we have no critical live data, we can drop the old tables.  
   - If future migrations need data preservation, write a one-off script to project legacy rows into the new structure.

4. **Clean-up**  
   - Drop the obsolete `strategy_positions`, `funding_payments`, and `fund_transfers` tables once dependent code is removed or rewritten against the new structure (or rename them to `_legacy` during the transition).

## Operational Considerations

- **Bootstrap Credentials**: until a secrets manager is integrated, we can populate `account_credentials.secret_ref` with encrypted payloads derived from existing `env-accountX` files. Loading logic can decrypt them or fallback to environment variables.
- **Adding new DEXs**: simply add a row in the `dexes` lookup table and populate the necessary credential rows. No migrations or code branches needed.
- **Auditing / Observability**: `position_fills` (when implemented) plus `metadata` fields lets us record tx hashes, API responses, or anything exchange-specific.

## Next Steps

1. Implement migrations for the new tables (keeping the old tables until code is ported).  
2. Refactor the funding-arb managers/opener/monitor to the new schema.  
3. Introduce an account loader that can ingest existing env files into `trading_accounts` and `account_credentials`.  
4. Wire capacity checks to consider `account_id` so per-wallet throttles work.  
5. Evaluate secret storage: decide between a secrets manager integration (preferred) or local encryption, then update the loader accordingly.

This structure keeps the schema flexible for future strategies, unlocks wallet-scoped tracking, and decouples credential management from the trading logic.

