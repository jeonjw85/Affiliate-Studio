"""Trusted presentation only. The article fragment remains the WP payload."""
import base64
import hashlib
from html import escape

CSS = '''html{color-scheme:light}body{font-family:system-ui,-apple-system,sans-serif;color:#20232b;background:#fff;max-width:48rem;margin:0 auto;padding:24px;line-height:1.8;overflow-wrap:anywhere}h1{font-size:1.7rem;line-height:1.4;letter-spacing:-.035em;margin:24px 0}h2{font-size:1.2rem;line-height:1.5;margin-top:1.8em}h3{font-size:1.05rem;margin-top:1.5em}p{margin:1em 0}li{margin:.35em 0}blockquote{margin:1.2em 0;padding:1px 18px;border-left:3px solid #8892a4;background:#f6f7f9}table{border-collapse:collapse;width:100%;display:block;overflow-x:auto;margin:1.2em 0;font-size:.94rem}th,td{border:1px solid #dce0e7;padding:10px 14px;text-align:left;min-width:90px}th{background:#f6f7f9}a{color:#245ac0;text-underline-offset:3px}ul,ol{padding-left:1.5em}strong{font-weight:700}@media(max-width:480px){body{padding:16px}h1{font-size:1.4rem}th,td{padding:8px}}'''
CSS_HASH = 'sha256-' + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()


def document(fragment, title):
    # Only the fixed CSS hash is allowed. No user CSS, scripts, images or requests.
    csp = f"default-src 'none'; style-src '{CSS_HASH}'; base-uri 'none'; form-action 'none'"
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<title>{escape(title)}</title><style>{CSS}</style></head>'
            f'<body>{fragment}</body></html>')
