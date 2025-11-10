# Resume Strategy Functionality - Investigation Summary

## Current Flow Analysis

### 1. **stop_strategy Flow** (`process_manager.py:459`)
```
1. Get supervisor_program_name and status from DB
2. Call supervisor.supervisor.stopProcess(supervisor_program_name)
3. Update DB: status = 'stopped', stopped_at = NOW()
4. Release port via port_manager.release_port(port) [just logging]
```

**Key Points:**
- Supervisor program config file persists (not deleted)
- Port is marked as released in DB (status change makes it available)
- Process state in Supervisor becomes STOPPED
- Database record remains with status='stopped'

### 2. **spawn_strategy Flow** (`process_manager.py:107`)
```
1. Generate new run_id (UUID)
2. Allocate new port via port_manager.allocate_port()
3. Create temp config file (YAML)
4. Create Supervisor config file in /etc/supervisor/conf.d/
5. Call supervisor.supervisor.reloadConfig()
6. Call supervisor.supervisor.addProcessGroup()
7. Call supervisor.supervisor.startProcess()
8. Insert NEW DB record with status='starting'
```

**Key Points:**
- Creates new run_id (new strategy instance)
- Allocates fresh port
- Creates new Supervisor config file
- Creates new DB record

### 3. **Port Management** (`port_manager.py`)
- Ports tracked by DB query: `WHERE status IN ('starting', 'running', 'paused')`
- When status changes to 'stopped', port becomes available
- Port range: 8766-8799 (33 ports)
- `allocate_port()` finds first available port
- `is_port_available(port)` checks if specific port is free

## Resume Strategy Requirements

### What We Need to Do:
1. **Reuse existing strategy run** (same run_id, same Supervisor program)
2. **Check Supervisor state** - verify process is STOPPED
3. **Port handling:**
   - Try to reuse old port if available
   - If old port taken, allocate new port
   - Update Supervisor config if port changed
4. **Restart Supervisor process** using `startProcess()`
5. **Update database:**
   - status: 'stopped' → 'starting'
   - Clear stopped_at (set to NULL)
   - Update control_api_port if changed

### Implementation Plan:

#### Method: `resume_strategy(run_id: str) -> bool`

**Steps:**
1. **Get strategy info from DB:**
   ```sql
   SELECT supervisor_program_name, status, control_api_port, 
          user_id, account_id, config_id
   FROM strategy_runs
   WHERE id = :run_id
   ```

2. **Validate:**
   - Strategy exists
   - Status is 'stopped' (can't resume running/starting strategies)
   - User has permission (ownership check)

3. **Check Supervisor state:**
   ```python
   info = supervisor.supervisor.getProcessInfo(supervisor_program_name)
   state = info.get('statename')
   # Should be 'STOPPED' or 'EXITED'
   ```

4. **Port handling:**
   ```python
   old_port = row['control_api_port']
   if await port_manager.is_port_available(old_port):
       port = old_port  # Reuse old port
   else:
       port = await port_manager.allocate_port()  # Get new port
       # Update Supervisor config file with new port
   ```

5. **Update Supervisor config (if port changed):**
   - Read existing config file: `/etc/supervisor/conf.d/{supervisor_program_name}.conf`
   - Update `--control-api-port {port}` in command
   - Write back to file
   - Call `supervisor.supervisor.reloadConfig()`

6. **Start process:**
   ```python
   supervisor.supervisor.startProcess(supervisor_program_name)
   ```

7. **Update database:**
   ```sql
   UPDATE strategy_runs
   SET status = 'starting',
       stopped_at = NULL,
       control_api_port = :port
   WHERE id = :run_id
   ```

### Edge Cases to Handle:

1. **Supervisor program doesn't exist:**
   - Config file was deleted
   - Need to recreate Supervisor config (similar to spawn_strategy)
   - This would be a "restart" rather than "resume"

2. **Old port unavailable:**
   - Another strategy using it
   - Need to update Supervisor config with new port
   - Need to reload Supervisor config

3. **Strategy already running:**
   - Check Supervisor state first
   - If RUNNING/STARTING, return success (already running)

4. **Config file missing:**
   - Would need to recreate from DB data
   - Requires config_data from strategy_configs table
   - This is more complex - might want to prevent resume in this case

### Differences from spawn_strategy:

| Aspect | spawn_strategy | resume_strategy |
|--------|---------------|----------------|
| run_id | New UUID | Reuse existing |
| DB record | INSERT new | UPDATE existing |
| Supervisor config | Create new | Reuse existing (may update port) |
| Port | Always allocate new | Try to reuse old, allocate if needed |
| stopped_at | N/A | Clear (set to NULL) |

### Supervisor API Methods Available:

- `getProcessInfo(name)` - Get process state
- `startProcess(name)` - Start stopped process
- `stopProcess(name)` - Stop running process
- `reloadConfig()` - Reload config files
- `addProcessGroup(name)` - Register new program group

**Note:** Supervisor doesn't have a separate "resume" method - `startProcess()` works for both new starts and resuming stopped processes.

## Recommended Implementation

### Option 1: Simple Resume (Recommended)
- Only resume if Supervisor config file still exists
- Reuse old port if available, otherwise allocate new
- Update Supervisor config if port changed
- Use `startProcess()` to restart

### Option 2: Full Restart
- Recreate Supervisor config from DB data
- Always allocate new port
- More complex but handles edge cases

**Recommendation:** Start with Option 1, add Option 2 later if needed.

