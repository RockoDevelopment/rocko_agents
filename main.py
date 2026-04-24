"""
RockoAgents — main.py
PyInstaller entry point. Compiles to:
  rocko.exe  (Windows)
  rocko      (Mac / Linux)

Usage after compiling:
  rocko run
  rocko run --port 8787
  rocko run --verbose

Developers (no compile):
  python main.py run
"""
import sys
import os
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as compiled PyInstaller executable
    ROOT       = Path(sys.executable).parent.resolve()
    BRIDGE_DIR = ROOT          # bridge modules bundled inside exe
else:
    # Running as plain Python script
    ROOT       = Path(__file__).parent.resolve()
    BRIDGE_DIR = ROOT / 'bridge'
    sys.path.insert(0, str(BRIDGE_DIR))

os.environ['ROCKO_ROOT_OVERRIDE'] = str(ROOT)

# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog='rocko',
        description='RockoAgents v5.0 — Self-hosted local agent orchestration'
    )
    sub = parser.add_subparsers(dest='command')

    run_p = sub.add_parser('run', help='Start RockoAgents bridge and UI')
    run_p.add_argument('--project',    default=None,     help='Path to project.json')
    run_p.add_argument('--port',       type=int, default=8787, help='Port (default 8787)')
    run_p.add_argument('--host',       default='127.0.0.1')
    run_p.add_argument('--verbose',    action='store_true')
    run_p.add_argument('--no-browser', action='store_true', dest='no_browser')

    args = parser.parse_args()

    if args.command == 'run' or args.command is None:
        # Build argv for bridge.cli_main
        argv = ['rocko']
        argv += ['--port', str(args.port)]
        argv += ['--host', args.host]
        if getattr(args, 'project', None): argv += ['--project', args.project]
        if getattr(args, 'verbose', False): argv.append('--verbose')
        if getattr(args, 'no_browser', False): argv.append('--no-browser')

        from bridge import cli_main
        cli_main(argv)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()