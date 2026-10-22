"""Start the single-process studio using project-local configuration."""
import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn


def main():
    load_dotenv(Path(__file__).resolve().parent / '.env', override=False, interpolate=False)
    parser = argparse.ArgumentParser(description='Affiliate Studio')
    parser.add_argument('--port', type=int, default=os.environ.get('STUDIO_PORT', '8787'))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('포트는 1024~65535 범위만 허용')
    os.umask(0o077)
    uvicorn.run('studio.app:create_app', factory=True, host=os.environ.get('STUDIO_HOST', '127.0.0.1'), port=args.port, workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()
