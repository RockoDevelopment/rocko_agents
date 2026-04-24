"""
rockoagents/cli.py
Registered as the `rocko` console_script entry point via pyproject.toml.
Used when running in development mode (pip install -e .).
For distribution, use the compiled exe from main.py instead.
"""
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
BRIDGE_DIR = ROOT / 'bridge'

def main():
    sys.path.insert(0, str(BRIDGE_DIR))

    import argparse
    parser = argparse.ArgumentParser(prog='rocko',
        description='RockoAgents v5.0 — Self-hosted local agent orchestration')
    sub = parser.add_subparsers(dest='command')

    run_p = sub.add_parser('run', help='Start RockoAgents')
    run_p.add_argument('--project',    default=None)
    run_p.add_argument('--port',       type=int, default=8787)
    run_p.add_argument('--host',       default='127.0.0.1')
    run_p.add_argument('--verbose',    action='store_true')
    run_p.add_argument('--no-browser', action='store_true', dest='no_browser')

    args = parser.parse_args()

    if args.command == 'run' or args.command is None:
        argv = ['rocko', '--port', str(args.port), '--host', args.host]
        if getattr(args, 'project', None): argv += ['--project', args.project]
        if getattr(args, 'verbose', False): argv.append('--verbose')
        if getattr(args, 'no_browser', False): argv.append('--no-browser')

        from bridge import cli_main
        cli_main(argv)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()