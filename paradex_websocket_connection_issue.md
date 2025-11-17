[STDERR] 2025-11-17 13:48:11.034 - ERROR - ParadexWebsocketClient: Connection failed traceback:Traceback (most recent call last):
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/paradex_py/api/ws_client.py", line 337, in _read_messages
    response = await asyncio.wait_for(self.ws.recv(), timeout=self.ws_timeout)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/asyncio/tasks.py", line 520, in wait_for
    return await fut
           ^^^^^^^^^
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/websockets/asyncio/connection.py", line 308, in recv
    raise ConcurrencyError(
websockets.exceptions.ConcurrencyError: cannot call recv while another coroutine is already running recv or recv_streaming
Traceback (most recent call last):
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/paradex_py/api/ws_client.py", line 337, in _read_messages
    response = await asyncio.wait_for(self.ws.recv(), timeout=self.ws_timeout)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/asyncio/tasks.py", line 520, in wait_for
    return await fut
           ^^^^^^^^^
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/websockets/asyncio/connection.py", line 308, in recv
    raise ConcurrencyError(
websockets.exceptions.ConcurrencyError: cannot call recv while another coroutine is already running recv or recv_streaming

[STDERR] 2025-11-17 13:48:12.036 - ERROR - ParadexWebsocketClient: Connection failed traceback:Traceback (most recent call last):
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/paradex_py/api/ws_client.py", line 337, in _read_messages
    response = await asyncio.wait_for(self.ws.recv(), timeout=self.ws_timeout)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/asyncio/tasks.py", line 520, in wait_for
    return await fut
           ^^^^^^^^^
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/websockets/asyncio/connection.py", line 308, in recv
    raise ConcurrencyError(
websockets.exceptions.ConcurrencyError: cannot call recv while another coroutine is already running recv or recv_streaming
Traceback (most recent call last):
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/paradex_py/api/ws_client.py", line 337, in _read_messages
    response = await asyncio.wait_for(self.ws.recv(), timeout=self.ws_timeout)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/asyncio/tasks.py", line 520, in wait_for
    return await fut
           ^^^^^^^^^
  File "/root/perp-dex-tools/venv/lib/python3.12/site-packages/websockets/asyncio/connection.py", line 308, in recv
    raise ConcurrencyError(
websockets.exceptions.ConcurrencyError: cannot call recv while another coroutine is already running recv or recv_streaming