"""
RockoAgents -- main.py
PyInstaller entry point. Compiles to:
  rocko.exe  (Windows)
  rocko      (Mac / Linux)

Usage:
  rocko run
  rocko run --port 8787
  rocko run --verbose
"""
import sys
import os
from pathlib import Path

# Path setup
if getattr(sys, 'frozen', False):
    # Compiled exe -- bridge is a package bundled inside
    ROOT = Path(sys.executable).parent.resolve()
    if hasattr(sys, '_MEIPASS'):
        # Add the unpacked temp dir to sys.path for package resolution
        sys.path.insert(0, sys._MEIPASS)
else:
    # Running as plain Python script
    ROOT = Path(__file__).parent.resolve()
    sys.path.insert(0, str(ROOT))

os.environ['ROCKO_ROOT_OVERRIDE'] = str(ROOT)

def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog='rocko',
        description='RockoAgents v5.0 -- Self-hosted local agent orchestration'
    )
    sub = parser.add_subparsers(dest='command')

    run_p = sub.add_parser('run', help='Start RockoAgents bridge and UI')
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

        # Import via package path -- consistent between frozen and source
        from bridge.bridge import cli_main
        cli_main(argv)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()