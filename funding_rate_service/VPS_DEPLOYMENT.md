# VPS Deployment Guide

This guide explains how to deploy the Funding Rate Service on your VPS with separate processes for the API server and background tasks.

## Architecture Overview

The service now runs as **two separate processes**:

1. **API Server** (`main.py`) - Serves REST API endpoints
2. **Background Tasks** (`run_tasks.py`) - Collects data every 60 seconds

This separation provides:
- ✅ Better resource isolation
- ✅ Independent scaling and monitoring
- ✅ Easier debugging and maintenance
- ✅ Process-level fault tolerance

---

## Quick Start

### 1. Start the API Server

```bash
cd funding_rate_service
python main.py
```

The API will be available at `http://localhost:8000`

### 2. Start Background Tasks (in separate terminal)

```bash
cd funding_rate_service
python run_tasks.py
```

This will start collecting funding rates every 60 seconds.

---

## Production VPS Deployment

### Option 1: Using Screen/Tmux (Simple)

```bash
# Start API server in screen
screen -S funding-api
cd funding_rate_service
python main.py
# Detach with Ctrl+A, D

# Start background tasks in another screen
screen -S funding-tasks
cd funding_rate_service
python run_tasks.py
# Detach with Ctrl+A, D

# List screens
screen -ls

# Reattach to screens
screen -r funding-api
screen -r funding-tasks
```

### Option 2: Using nohup (Background processes)

```bash
cd funding_rate_service

# Start API server in background
nohup python main.py > api.log 2>&1 &

# Start background tasks in background
nohup python run_tasks.py > tasks.log 2>&1 &

# Check processes
ps aux | grep python

# View logs
tail -f api.log
tail -f tasks.log
```

### Option 3: Using systemd (Recommended for production)

Create service files:

**`/etc/systemd/system/funding-api.service`:**
```ini
[Unit]
Description=Funding Rate Service API
After=network.target postgresql.service

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/perp-dex-tools/funding_rate_service
ExecStart=/path/to/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONPATH=/path/to/perp-dex-tools

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/funding-tasks.service`:**
```ini
[Unit]
Description=Funding Rate Service Background Tasks
After=network.target postgresql.service

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/perp-dex-tools/funding_rate_service
ExecStart=/path/to/venv/bin/python run_tasks.py
Restart=always
RestartSec=10
Environment=PYTHONPATH=/path/to/perp-dex-tools

[Install]
WantedBy=multi-user.target
```

Enable and start services:
```bash
sudo systemctl daemon-reload
sudo systemctl enable funding-api funding-tasks
sudo systemctl start funding-api funding-tasks

# Check status
sudo systemctl status funding-api
sudo systemctl status funding-tasks

# View logs
sudo journalctl -u funding-api -f
sudo journalctl -u funding-tasks -f
```

---

## Task Runner Options

The `run_tasks.py` script supports several modes:

### All Tasks (Default)
```bash
python run_tasks.py
```
Runs all tasks:
- Funding rate collection (every 60s)
- Opportunity analysis (every 2 minutes)
- Database cleanup (daily at 2 AM UTC)

### Collection Only
```bash
python run_tasks.py --collection-only
```
Only runs funding rate collection (every 60s)

### No Cleanup
```bash
python run_tasks.py --no-cleanup
```
Runs collection and opportunity analysis, skips cleanup

### Test Run
```bash
python run_tasks.py --run-once
```
Runs all tasks once and exits (useful for testing)

### Test Collection Only
```bash
python run_tasks.py --run-once --collection-only
```
Tests only the collection task

---

## Monitoring

### API Endpoints for Monitoring

- **Task Status**: `GET /api/v1/tasks/status`
- **Task Health**: `GET /api/v1/tasks/health`
- **Task Metrics**: `GET /api/v1/tasks/metrics`
- **Task Info**: `GET /api/v1/tasks/info`

### Example Monitoring Commands

```bash
# Check if tasks are running
curl http://localhost:8000/api/v1/tasks/health

# Get detailed task status
curl http://localhost:8000/api/v1/tasks/status

# Get task metrics
curl http://localhost:8000/api/v1/tasks/metrics

# Check API health
curl http://localhost:8000/api/v1/health
```

### Log Monitoring

```bash
# API logs (if using nohup)
tail -f api.log

# Task logs (if using nohup)
tail -f tasks.log

# System logs (if using systemd)
sudo journalctl -u funding-api -f
sudo journalctl -u funding-tasks -f
```

---

## Troubleshooting

### Tasks Not Running

1. **Check if run_tasks.py is running:**
   ```bash
   ps aux | grep run_tasks
   ```

2. **Check task health via API:**
   ```bash
   curl http://localhost:8000/api/v1/tasks/health
   ```

3. **Start tasks manually:**
   ```bash
   python run_tasks.py --run-once  # Test run
   python run_tasks.py             # Continuous run
   ```

### API Not Responding

1. **Check if main.py is running:**
   ```bash
   ps aux | grep main.py
   ```

2. **Check API health:**
   ```bash
   curl http://localhost:8000/ping
   ```

3. **Start API manually:**
   ```bash
   python main.py
   ```

### Database Issues

1. **Check database connection:**
   ```bash
   curl http://localhost:8000/api/v1/health/database
   ```

2. **Verify database is running:**
   ```bash
   sudo systemctl status postgresql
   ```

3. **Check database logs:**
   ```bash
   sudo journalctl -u postgresql -f
   ```

### No Data Being Collected

1. **Check recent collections:**
   ```bash
   curl http://localhost:8000/api/v1/tasks/status
   ```

2. **Force a collection:**
   ```bash
   python run_tasks.py --run-once --collection-only
   ```

3. **Check exchange client installation:**
   ```bash
   pip list | grep -E "(lighter|grvt|edgex)"
   ```

---

## Performance Tuning

### For High-Traffic VPS

1. **Increase API workers:**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
   ```

2. **Optimize database connections:**
   - Increase `database_pool_max_size` in config
   - Monitor connection usage

3. **Adjust task frequencies:**
   - Modify intervals in `tasks/scheduler.py`
   - Consider running tasks less frequently if needed

### For Low-Resource VPS

1. **Run collection only:**
   ```bash
   python run_tasks.py --collection-only
   ```

2. **Reduce database retention:**
   - Modify retention days in `tasks/cleanup_task.py`
   - Run cleanup more frequently

3. **Monitor resource usage:**
   ```bash
   htop
   df -h
   ```

---

## Security Considerations

1. **Firewall Configuration:**
   ```bash
   # Only allow API port
   sudo ufw allow 8000/tcp
   sudo ufw enable
   ```

2. **Environment Variables:**
   - Store sensitive config in `.env` file
   - Never commit `.env` to version control

3. **User Permissions:**
   - Run services as non-root user
   - Restrict file permissions

4. **Database Security:**
   - Use strong passwords
   - Restrict database access to localhost
   - Regular backups

---

## Backup Strategy

### Database Backup
```bash
# Daily backup script
pg_dump funding_rates > backup_$(date +%Y%m%d).sql

# Automated backup (add to crontab)
0 3 * * * pg_dump funding_rates > /backups/funding_$(date +\%Y\%m\%d).sql
```

### Configuration Backup
```bash
# Backup important files
tar -czf config_backup.tar.gz .env funding_rate_service/config.py
```

---

## Scaling Considerations

### Horizontal Scaling

1. **Multiple API Instances:**
   - Run multiple API servers behind load balancer
   - Only run tasks on one instance

2. **Database Scaling:**
   - Use read replicas for API queries
   - Keep writes on primary for tasks

### Vertical Scaling

1. **Increase VPS Resources:**
   - More CPU for faster opportunity analysis
   - More RAM for larger datasets
   - SSD storage for better database performance

---

## Support

If you encounter issues:

1. Check the logs first
2. Verify all dependencies are installed
3. Test with `--run-once` mode
4. Check API endpoints for status
5. Monitor resource usage

The separate process architecture makes debugging much easier - you can restart either the API or tasks independently without affecting the other.
