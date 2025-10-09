# CLI Display System - Analysis & Notes

## Overview
Hummingbot's **Terminal UI** system built on `prompt_toolkit`. Provides a full-featured, interactive command-line interface with:
- Multi-pane layout (output, input, logs, live data)
- Auto-completion and command parsing
- Colorized output with themes
- Live updating panels (CPU, memory, trades, strategies)
- Keyboard shortcuts and navigation

---

## 🏗️ Architecture

### Core Components

```
HummingbotCLI (ui/hummingbot_cli.py)
    ├── Layout (ui/layout.py)
    │   ├── Input Field (bottom pane)
    │   ├── Output Field (main pane)
    │   ├── Log Field (right pane)
    │   ├── Header (version, strategy, gateway status)
    │   └── Footer (timer, CPU/mem, trades)
    ├── Completer (ui/completer.py)
    ├── Parser (ui/parser.py)
    ├── Keybindings (ui/keybindings.py)
    └── Style (ui/style.py)
```

---

## 📦 Component Breakdown

### 1. **HummingbotCLI** (`ui/hummingbot_cli.py`)

**Purpose:** Main application controller managing UI state and user interaction.

```python
class HummingbotCLI(PubSub):
    def __init__(
        self,
        client_config_map: ClientConfigAdapter,
        input_handler: Callable,  # Function to process commands
        bindings: KeyBindings,
        completer: Completer,
        command_tabs: Dict[str, CommandTab]
    ):
        self.input_field = create_input_field(completer)
        self.output_field = create_output_field(client_config_map)
        self.log_field = create_log_field()
        self.timer = create_timer()
        self.process_usage = create_process_monitor()
        self.trade_monitor = create_trade_monitor()
        
        # Generate layout from components
        self.layout = generate_layout(
            self.input_field,
            self.output_field,
            self.log_field,
            ...
        )
        
        # Create prompt_toolkit Application
        self.app = Application(
            layout=self.layout,
            key_bindings=self.bindings,
            style=load_style(client_config_map),
            mouse_support=True
        )
```

**Key Methods:**

```python
async def run(self):
    """Start the CLI application."""
    await self.app.run_async(pre_run=self.did_start_ui)

def accept(self, buff):
    """Called when user presses Enter."""
    self.pending_input = self.input_field.text.strip()
    self.log(f"\n>>>  {self.input_field.text}")
    self.input_handler(self.input_field.text)  # Process command

def log(self, text: str, save_log: bool = True):
    """Output to main pane."""
    self.output_field.log(text)

async def prompt(self, prompt: str, is_password: bool = False) -> str:
    """Prompt user for input."""
    self.change_prompt(prompt, is_password)
    await self.input_event.wait()
    return self.pending_input

def toggle_right_pane(self):
    """Show/hide log pane (Ctrl+T)."""
    ...
```

**Usage Pattern:**
```python
# In main application
cli = HummingbotCLI(
    client_config_map=config,
    input_handler=self.process_command,
    bindings=load_key_bindings(self),
    completer=load_completer(self),
    command_tabs={}
)

await cli.run()  # Blocks until exit
```

---

### 2. **Layout System** (`ui/layout.py`)

**Purpose:** Defines the visual structure using `prompt_toolkit` containers.

#### Layout Structure:

```
┌─────────────────────────────────────────────────────────────┐
│ Header: Version | Strategy | File | Gateway | Toggle      │
├─────────────────────────────────────┬───────────────────────┤
│                                     │                       │
│                                     │  Logs / Tab Outputs   │
│         Output Pane                 │                       │
│      (command results)              │  (scrollable)         │
│                                     │                       │
│      (scrollable)                   │                       │
│                                     │                       │
├─────────────────────────────────────┴───────────────────────┤
│ >>> Input field with auto-complete                          │
├─────────────────────────────────────────────────────────────┤
│ Footer: Trades | CPU/Mem | Uptime                          │
└─────────────────────────────────────────────────────────────┘
```

**Key Components:**

```python
def create_input_field(completer: Completer):
    """Command input with auto-completion."""
    return CustomTextArea(
        height=10,
        prompt='>>> ',
        multiline=False,
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=True
    )

def create_output_field(client_config_map):
    """Main output pane with styling."""
    return CustomTextArea(
        scrollbar=True,
        max_line_count=MAXIMUM_OUTPUT_PANE_LINE_COUNT,
        initial_text=HEADER,  # ASCII art logo
        lexer=FormattedTextLexer(client_config_map)
    )

def create_log_field(search_field):
    """Right pane for logs."""
    return CustomTextArea(
        scrollbar=True,
        max_line_count=MAXIMUM_LOG_PANE_LINE_COUNT,
        search_field=search_field,  # Ctrl+F to search
        preview_search=False
    )

def create_timer():
    """Uptime display."""
    return CustomTextArea(
        max_line_count=1,
        width=30,
        style='class:footer'
    )

def create_process_monitor():
    """CPU/memory monitor."""
    return CustomTextArea(
        max_line_count=1,
        align=WindowAlign.RIGHT,
        style='class:footer'
    )

def create_trade_monitor():
    """Live trade statistics."""
    return CustomTextArea(
        max_line_count=1,
        style='class:footer'
    )
```

**Layout Generation:**

```python
def generate_layout(
    input_field, output_field, log_field,
    right_pane_toggle, log_field_button,
    search_field, timer, process_monitor, trade_monitor,
    command_tabs
):
    # Header
    pane_top = VSplit([
        Window(FormattedTextControl(get_version)),
        Window(FormattedTextControl(get_active_strategy)),
        Window(FormattedTextControl(get_strategy_file)),
        Window(FormattedTextControl(get_gateway_status)),
        right_pane_toggle
    ], height=1)
    
    # Footer
    pane_bottom = VSplit([
        trade_monitor,
        process_monitor,
        timer
    ], height=1)
    
    # Left pane (output + input)
    pane_left = HSplit([
        Box(body=output_field, style="class:output_field"),
        Box(body=input_field, style="class:input_field")
    ])
    
    # Right pane (logs/tabs)
    pane_right = ConditionalContainer(
        Box(body=HSplit([
            tab_buttons,
            log_field,
            search_field
        ])),
        filter=True  # Can be toggled
    )
    
    # Root
    root_container = HSplit([
        pane_top,
        VSplit([
            FloatContainer(pane_left, hint_menus),
            pane_right
        ]),
        pane_bottom
    ])
    
    return Layout(root_container, focused_element=input_field)
```

---

### 3. **Auto-Completion** (`ui/completer.py`)

**Purpose:** Intelligent command completion based on context.

```python
class HummingbotCompleter(Completer):
    def __init__(self, hummingbot_application):
        self.hummingbot_application = hummingbot_application
        
        # Command completers
        self._command_completer = WordCompleter(
            self.parser.commands,  # ["connect", "start", "stop", ...]
            ignore_case=True
        )
        
        # Context-specific completers
        self._exchange_completer = WordCompleter(
            sorted(AllConnectorSettings.get_connector_settings().keys())
        )
        self._strategy_completer = WordCompleter(STRATEGIES)
        self._script_strategy_completer = WordCompleter(
            file_name_list(SCRIPT_STRATEGIES_PATH, "py")
        )
        
    def get_completions(self, document, complete_event):
        """Return completions based on current input."""
        
        # Check context
        if self._complete_command(document):
            yield from self._command_completer.get_completions(...)
        
        elif self._complete_strategies(document):
            yield from self._strategy_completer.get_completions(...)
        
        elif self._complete_exchanges(document):
            yield from self._exchange_completer.get_completions(...)
        
        elif self._complete_trading_pairs(document):
            yield from self._trading_pair_completer.get_completions(...)
        
        # ... more context checks
```

**Context Detection Examples:**

```python
def _complete_exchanges(self, document) -> bool:
    """Check if we're in exchange name context."""
    return any(x in self.prompt_text.lower() 
               for x in ("exchange name", "name of exchange"))

def _complete_trading_pairs(self, document) -> bool:
    """Check if we're in trading pair context."""
    return "trading pair" in self.prompt_text

def _complete_script_strategy_files(self, document) -> bool:
    """Check if we're selecting a script."""
    text = document.text_before_cursor
    return text.startswith("start --script ")
```

**Dynamic Completions:**

```python
@property
def _trading_pair_completer(self) -> Completer:
    """Fetch trading pairs from exchange."""
    trading_pair_fetcher = TradingPairFetcher.get_instance()
    
    # Detect which exchange from command
    market = ""
    for exchange in sorted(list(all_exchanges), key=len, reverse=True):
        if exchange in self.prompt_text:
            market = exchange
            break
    
    # Get pairs for that exchange
    trading_pairs = trading_pair_fetcher.trading_pairs.get(market, [])
    return WordCompleter(trading_pairs, ignore_case=True)
```

---

### 4. **Command Parser** (`ui/parser.py`)

**Purpose:** Parse user commands using `argparse`.

```python
class ThrowingArgumentParser(argparse.ArgumentParser):
    """Custom parser that raises instead of exiting."""
    def error(self, message):
        raise ArgumentParserError(message)
    
    def exit(self, status=0, message=None):
        pass  # Don't exit the app

def load_parser(hummingbot, command_tabs):
    parser = ThrowingArgumentParser(prog="", add_help=False)
    subparsers = parser.add_subparsers()
    
    # Connect command
    connect_parser = subparsers.add_parser(
        "connect",
        help="List available exchanges and add API keys"
    )
    connect_parser.add_argument(
        "option",
        nargs="?",
        choices=CONNECT_OPTIONS,
        help="Name of the exchange"
    )
    connect_parser.set_defaults(func=hummingbot.connect)
    
    # Start command
    start_parser = subparsers.add_parser(
        "start",
        help="Start the current bot"
    )
    start_parser.add_argument(
        "--script",
        type=str,
        dest="script",
        help="Script strategy file name"
    )
    start_parser.add_argument(
        "--conf",
        type=str,
        dest="conf",
        help="Script config file name"
    )
    start_parser.set_defaults(func=hummingbot.start)
    
    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Get market status of current bot"
    )
    status_parser.add_argument(
        "--live",
        default=False,
        action="store_true",
        dest="live",
        help="Show status updates"
    )
    status_parser.set_defaults(func=hummingbot.status)
    
    # ... more commands
    
    return parser
```

**Usage:**
```python
# In HummingbotApplication
def _handle_command(self, raw_command: str):
    try:
        args = self.parser.parse_args(raw_command.split())
        # Call the function set by set_defaults()
        args.func(args)
    except ArgumentParserError as e:
        self.notify(str(e))
```

---

### 5. **Key Bindings** (`ui/keybindings.py`)

**Purpose:** Define keyboard shortcuts.

```python
def load_key_bindings(hb) -> KeyBindings:
    bindings = KeyBindings()
    
    @bindings.add("c-c", "c-c")  # Double Ctrl+C
    def exit_(event):
        hb.app.log("\n[Double CTRL + C] keyboard exit")
        safe_ensure_future(hb.exit_loop())
    
    @bindings.add("c-s")  # Ctrl+S
    def status(event):
        hb.app.log("\n[CTRL + S] Status")
        hb.status()
    
    @bindings.add("c-f", filter=to_filter(not is_searching()))
    def do_find(event):
        """Open search in log pane."""
        start_search(hb.app.log_field.control)
    
    @bindings.add("c-t")  # Ctrl+T
    def toggle_logs(event):
        """Show/hide right pane."""
        hb.app.toggle_right_pane()
    
    @bindings.add("c-d")  # Ctrl+D
    def scroll_down_output(event):
        """Scroll output pane down."""
        scroll_down(event, hb.app.output_field.window, ...)
    
    @bindings.add("c-e")  # Ctrl+E
    def scroll_up_output(event):
        """Scroll output pane up."""
        scroll_up(event, hb.app.output_field.window, ...)
    
    return bindings
```

---

### 6. **Custom Widgets** (`ui/custom_widgets.py`)

**Purpose:** Enhanced text areas with logging and styling.

```python
class CustomTextArea:
    """TextArea with automatic line wrapping and max line count."""
    
    def __init__(
        self,
        text='',
        multiline=True,
        lexer=None,
        completer=None,
        max_line_count=1000,
        initial_text="",
        ...
    ):
        self.max_line_count = max_line_count
        self.log_lines: Deque[str] = deque()
        
        self.buffer = CustomBuffer(
            document=Document(text, 0),
            multiline=multiline,
            completer=DynamicCompleter(lambda: self.completer),
            auto_suggest=DynamicAutoSuggest(lambda: self.auto_suggest),
        )
        
        self.window = Window(
            height=height,
            width=width,
            content=BufferControl(buffer=self.buffer, lexer=lexer),
            wrap_lines=Condition(lambda: is_true(self.wrap_lines))
        )
    
    def log(self, text: str, save_log: bool = True, silent: bool = False):
        """Add text to the buffer."""
        # Split by newlines
        new_lines = str(text).split('\n')
        
        # Wrap long lines based on window width
        max_width = self.window.render_info.window_width - 2
        wrapped_lines = []
        for line in new_lines:
            while len(line) > max_width:
                wrapped_lines.append(line[:max_width])
                line = line[max_width:]
            wrapped_lines.append(line)
        
        # Add to deque with max size
        if save_log:
            self.log_lines.extend(wrapped_lines)
            while len(self.log_lines) > self.max_line_count:
                self.log_lines.popleft()
        
        # Update buffer
        new_text = "\n".join(self.log_lines)
        if not silent:
            self.buffer.document = Document(
                text=new_text,
                cursor_position=len(new_text)
            )
```

---

### 7. **Styling** (`ui/style.py`)

**Purpose:** Color themes and visual styling.

```python
def load_style(config_map: ClientConfigAdapter):
    """Load color scheme from config."""
    
    # Default colors
    style = {
        "output_field": "bg:#171E2B #1CD085",  # Dark bg, green text
        "input_field": "bg:#000000 #FFFFFF",   # Black bg, white text
        "log_field": "bg:#171E2B #FFFFFF",     # Dark bg, white text
        "header": "bg:#000000 #AAAAAA",        # Black bg, gray text
        "footer": "bg:#000000 #AAAAAA",
        "primary": "#1CD085",                   # Brand green
        "warning": "#93C36D",                   # Yellow-green
        "error": "#F5634A",                     # Red
        "tab_button.focused": "bg:#1CD085 #171E2B",
        "tab_button": "bg:#FFFFFF #000000",
    }
    
    # Override with user config
    style["output_field"] = f"bg:{config_map.color.output_pane} {config_map.color.terminal_primary}"
    # ... more overrides
    
    return Style.from_dict(style)
```

**Label Styling:**
```python
# In lexer
text_ui_style = {
    "&cGOLD": "gold_label",      # &cGOLD Text → gold colored
    "&cSILVER": "silver_label",
    "&cBRONZE": "bronze_label",
}
```

---

### 8. **Live Updates** (`ui/interface_utils.py`)

**Purpose:** Background tasks for real-time displays.

```python
async def start_timer(timer):
    """Update uptime display every second."""
    count = 1
    while True:
        count += 1
        mins, sec = divmod(count, 60)
        hour, mins = divmod(mins, 60)
        days, hour = divmod(hour, 24)
        
        timer.log(f"Uptime: {days:>3} day(s), {hour:02}:{mins:02}:{sec:02}")
        await asyncio.sleep(1)

async def start_process_monitor(process_monitor):
    """Update CPU/memory every second."""
    hb_process = psutil.Process()
    while True:
        with hb_process.oneshot():
            cpu = hb_process.cpu_percent()
            mem_vms = hb_process.memory_info().vms
            mem_rss = hb_process.memory_info().rss
            threads = hb_process.num_threads()
            
            process_monitor.log(
                f"CPU: {cpu:>5}%, "
                f"Mem: {format_bytes(mem_vms / threads)} ({format_bytes(mem_rss)}), "
                f"Threads: {threads:>3}"
            )
        await asyncio.sleep(1)

async def start_trade_monitor(trade_monitor):
    """Update trade statistics every 2 seconds."""
    while True:
        if strategy_running and all_markets_ready:
            trades = get_trades_from_db()
            total_pnl = calculate_total_pnl(trades)
            avg_return = calculate_avg_return(trades)
            
            trade_monitor.log(
                f"Trades: {len(trades)}, "
                f"Total P&L: {total_pnl}, "
                f"Return %: {avg_return:.2%}"
            )
        await asyncio.sleep(2.0)
```

---

## 🎯 What's Useful for Your System

### ✅ **Highly Relevant:**

#### 1. **Status Display Pattern**
```python
# From trade_monitor and process_monitor
async def display_funding_arb_status(status_field):
    """Live update of funding arb positions."""
    while True:
        positions = get_active_positions()
        summary = []
        
        for pos in positions:
            funding = pos.cumulative_funding
            pnl = pos.unrealized_pnl
            total = funding + pnl
            
            summary.append(
                f"{pos.symbol}: "
                f"Funding: {funding:+.4f}, "
                f"PnL: {pnl:+.4f}, "
                f"Total: {total:+.4f} ({total/pos.size:.2%})"
            )
        
        status_field.log("\n".join(summary))
        await asyncio.sleep(2.0)
```

#### 2. **Command Structure**
```python
# Adapt parser pattern for your commands
def load_funding_arb_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    
    # Start monitoring
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--symbols", nargs="+")
    start_parser.add_argument("--min-spread", type=float, default=0.001)
    start_parser.set_defaults(func=start_monitoring)
    
    # Show positions
    positions_parser = subparsers.add_parser("positions")
    positions_parser.add_argument("--symbol", default=None)
    positions_parser.set_defaults(func=show_positions)
    
    # Close position
    close_parser = subparsers.add_parser("close")
    close_parser.add_argument("position_id")
    close_parser.set_defaults(func=close_position)
    
    return parser
```

#### 3. **Table Formatting**
```python
def format_df_for_printout(df, table_format="simple"):
    """Format pandas DataFrame for terminal display."""
    return tabulate.tabulate(
        df,
        tablefmt=table_format,
        showindex=False,
        headers="keys"
    )

# Usage
positions_df = pd.DataFrame([
    {
        "Symbol": "BTC",
        "Long DEX": "Lighter",
        "Short DEX": "Backpack",
        "Size": "$1000",
        "Funding": "+$2.50",
        "PnL": "-$1.20",
        "Net": "+$1.30"
    }
])

output = format_df_for_printout(positions_df)
cli.log(output)
```

### 🔄 **Adaptable:**

#### 1. **Simple CLI (without full UI)**
You don't need the full TUI. A simpler approach:

```python
class SimpleFundingArbCLI:
    def __init__(self, strategy):
        self.strategy = strategy
        self.running = True
    
    async def run(self):
        """Simple command loop."""
        print("Funding Arb Monitor - Type 'help' for commands")
        
        # Start background status display
        asyncio.create_task(self.display_status())
        
        while self.running:
            try:
                command = await ainput(">>> ")
                await self.process_command(command)
            except KeyboardInterrupt:
                print("\nExiting...")
                self.running = False
    
    async def process_command(self, command: str):
        """Handle commands."""
        parts = command.split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        
        if cmd == "status":
            self.show_status()
        elif cmd == "positions":
            self.show_positions()
        elif cmd == "start":
            symbol = parts[1] if len(parts) > 1 else "BTC"
            await self.strategy.start_monitoring(symbol)
        elif cmd == "stop":
            symbol = parts[1] if len(parts) > 1 else None
            await self.strategy.stop_monitoring(symbol)
        elif cmd == "help":
            self.show_help()
        elif cmd == "exit":
            self.running = False
        else:
            print(f"Unknown command: {cmd}")
    
    async def display_status(self):
        """Background task - update status every 5s."""
        while self.running:
            # Clear screen and show status
            os.system('clear')
            self.show_status()
            await asyncio.sleep(5)
    
    def show_status(self):
        """Display current positions."""
        positions = self.strategy.get_active_positions()
        
        if not positions:
            print("No active positions")
            return
        
        # Build table
        data = []
        for pos in positions:
            data.append({
                "Symbol": pos.symbol,
                "Long": pos.long_dex,
                "Short": pos.short_dex,
                "Size": f"${pos.size:.2f}",
                "Funding": f"${pos.cumulative_funding:+.2f}",
                "PnL": f"${pos.unrealized_pnl:+.2f}",
                "Net": f"${pos.net_profit:+.2f}"
            })
        
        df = pd.DataFrame(data)
        print(tabulate(df, headers="keys", tablefmt="grid"))
```

#### 2. **Rich Library Alternative**
Modern alternative to prompt_toolkit:

```python
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()

def create_positions_table(positions):
    """Create Rich table."""
    table = Table(title="Active Positions")
    
    table.add_column("Symbol", style="cyan")
    table.add_column("Long DEX", style="green")
    table.add_column("Short DEX", style="red")
    table.add_column("Funding", justify="right")
    table.add_column("PnL", justify="right")
    table.add_column("Net", justify="right", style="bold")
    
    for pos in positions:
        net_color = "green" if pos.net_profit > 0 else "red"
        table.add_row(
            pos.symbol,
            pos.long_dex,
            pos.short_dex,
            f"${pos.cumulative_funding:+.2f}",
            f"${pos.unrealized_pnl:+.2f}",
            f"[{net_color}]${pos.net_profit:+.2f}[/{net_color}]"
        )
    
    return table

# Live updating display
with Live(create_positions_table(positions), refresh_per_second=1) as live:
    while running:
        await asyncio.sleep(1)
        positions = get_updated_positions()
        live.update(create_positions_table(positions))
```

### ❌ **Not Needed:**

1. **Full TUI complexity** - Overkill for your use case
2. **Auto-completion** - Nice to have but not essential
3. **Multi-pane layout** - Simple single view is fine
4. **Custom lexer** - Standard colors are enough

---

## 💡 Recommended Approach for Your System

### Option A: Web Dashboard (Recommended)
Instead of CLI, build a simple web UI:

```python
# FastAPI dashboard
@app.get("/dashboard")
async def dashboard():
    positions = get_active_positions()
    opportunities = get_current_opportunities()
    
    return {
        "positions": positions,
        "opportunities": opportunities,
        "performance": get_performance_metrics()
    }

# Simple HTML + JS frontend with auto-refresh
# Much easier to build and use than TUI
```

### Option B: Rich CLI
If you want terminal UI:

```python
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
import asyncio

console = Console()

async def display_dashboard():
    """Live updating terminal dashboard."""
    
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3)
    )
    
    layout["body"].split_row(
        Layout(name="positions"),
        Layout(name="opportunities")
    )
    
    with Live(layout, refresh_per_second=1) as live:
        while True:
            # Update header
            layout["header"].update(
                Panel("Funding Arb Monitor", style="bold green")
            )
            
            # Update positions
            positions = get_active_positions()
            layout["positions"].update(
                Panel(create_positions_table(positions), title="Positions")
            )
            
            # Update opportunities
            opps = get_opportunities()
            layout["opportunities"].update(
                Panel(create_opportunities_table(opps), title="Opportunities")
            )
            
            # Update footer
            layout["footer"].update(
                Panel(f"Uptime: {get_uptime()} | Total P&L: ${get_total_pnl()}")
            )
            
            await asyncio.sleep(1)
```

### Option C: Minimal CLI + API
Simplest approach:

```python
# Just use your existing FastAPI endpoints
# CLI is just a wrapper around API calls

class FundingArbCLI:
    def __init__(self, api_url="http://localhost:8000"):
        self.api_url = api_url
    
    async def show_positions(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_url}/positions")
            positions = response.json()
            
            df = pd.DataFrame(positions)
            print(tabulate(df, headers="keys", tablefmt="grid"))
    
    async def show_opportunities(self, min_spread=0.001):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/opportunities",
                params={"min_profitability": min_spread}
            )
            opps = response.json()
            
            df = pd.DataFrame(opps)
            print(tabulate(df, headers="keys", tablefmt="grid"))

# Usage
cli = FundingArbCLI()
await cli.show_positions()
await cli.show_opportunities()
```

---

## 📝 Key Takeaways

1. **Hummingbot's TUI is powerful but complex**
   - Built for interactive trading with many features
   - Requires significant setup and maintenance

2. **For your funding arb system:**
   - **Web dashboard** is probably better UX
   - If terminal UI needed, use **Rich library** (simpler than prompt_toolkit)
   - Keep CLI as simple wrapper around your API

3. **Useful patterns to extract:**
   - Live updating displays (monitor tasks)
   - Table formatting for positions
   - Command parsing structure

4. **Skip the complexity:**
   - Don't need multi-pane layout
   - Don't need auto-completion
   - Don't need custom key bindings

---

## 🚀 Recommendation

**Build a simple web dashboard instead:**

```python
# backend/api/routes/dashboard.py
@router.get("/dashboard/data")
async def get_dashboard_data():
    return {
        "positions": await get_active_positions(),
        "opportunities": await get_current_opportunities(),
        "performance": await get_performance_metrics(),
        "recent_trades": await get_recent_trades(limit=10)
    }

# frontend/dashboard.html
<div id="dashboard">
    <div class="positions-panel">
        <!-- Auto-refreshing table of positions -->
    </div>
    <div class="opportunities-panel">
        <!-- Real-time funding rate spreads -->
    </div>
    <div class="performance-panel">
        <!-- P&L charts -->
    </div>
</div>

<script>
    // Refresh every 2 seconds
    setInterval(async () => {
        const data = await fetch('/dashboard/data').then(r => r.json());
        updateDashboard(data);
    }, 2000);
</script>
```

**Why?**
- ✅ Easier to build and maintain
- ✅ Better UX (charts, colors, responsive)
- ✅ Can access from anywhere
- ✅ Multiple users can view
- ✅ Easier to add features (notifications, alerts)

