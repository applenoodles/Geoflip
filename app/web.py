from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string

load_dotenv()

_INDEX_HTML = """<!doctype html>
<html lang="zh-TW">
<head><meta charset="utf-8"><title>GeoFlip</title></head>
<body>
  <h1>GeoFlip</h1>
  <p>伺服器運行中。遊戲尚未實作（Phase 0 骨架）。</p>
  <a href="/health">Health check</a>
</body>
</html>"""


def create_app(config=None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.getenv("SECRET_KEY", "geoflip-dev-secret")

    if config is not None:
        app.config.update(config)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        return render_template_string(_INDEX_HTML)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
