"""Start the local studio. Single process; no public bind option."""
import argparse
import os
import uvicorn


def main():
    parser = argparse.ArgumentParser(description='로컬 제휴 콘텐츠 작업대')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('포트는 1024~65535 범위여야 합니다.')
    os.umask(0o077)
    uvicorn.run('studio.app:create_app', factory=True, host='127.0.0.1', port=args.port, workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()
