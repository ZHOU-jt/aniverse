"""Portable WSGI entry point for a local demonstration or a reverse proxy."""
import argparse
import logging
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Run AniVerse with Waitress.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if not (Path(__file__).resolve().parent / '.env').exists():
        parser.error('Run python setup_demo.py first, or configure .env from .env.example.')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from waitress import serve
    from app import app
    if app.secret_key in {'anime-review-secret-key-change-this', 'replace-with-a-long-random-secret'}:
        parser.error('Configure a random ANIVERSE_SECRET_KEY before starting the server.')
    print(f'AniVerse: http://{args.host}:{args.port}', flush=True)
    serve(app, host=args.host, port=args.port, threads=4)


if __name__ == '__main__':
    main()
