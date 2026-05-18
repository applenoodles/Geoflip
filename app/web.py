from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.config import Config
from app.game.rules import RulesEngine
from app.services.nominatim import NominatimClient, NominatimError
from app.services.osrm import OsrmClient
from app.state import StateStore

load_dotenv()


# ---------------------------------------------------------------------------
# Stub /map placeholder (Phase 5 will replace via render_map_html)
# ---------------------------------------------------------------------------

_MAP_STUB_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>map</title></head>
<body style="margin:0;padding:0;background:#eef;">
  <p style="padding:1em;font-family:sans-serif;">
    地圖將在 Phase 5 由 Folium 渲染。目前為 stub。
  </p>
</body></html>"""


def create_app(
    config: Config | None = None,
    state_store: StateStore | None = None,
    nominatim_client: NominatimClient | None = None,
    osrm_client: OsrmClient | None = None,
    rules_engine: RulesEngine | None = None,
) -> Flask:
    """Flask app factory.

    All dependencies are injectable for tests. When omitted, real implementations
    are constructed from environment-derived `Config`.
    """
    if config is None:
        config = Config()
    if state_store is None:
        state_store = StateStore(config.STATE_FILE)
    if nominatim_client is None:
        nominatim_client = NominatimClient(
            base_url=config.NOMINATIM_BASE_URL,
            user_agent=config.NOMINATIM_USER_AGENT,
            email=config.NOMINATIM_EMAIL,
            timeout_seconds=config.REQUEST_TIMEOUT_SECONDS,
            min_interval_seconds=config.NOMINATIM_MIN_INTERVAL_SECONDS,
        )
    if osrm_client is None:
        osrm_client = OsrmClient(
            base_url=config.OSRM_BASE_URL,
            profile=config.OSRM_PROFILE,
            timeout_seconds=config.REQUEST_TIMEOUT_SECONDS,
        )
    if rules_engine is None:
        rules_engine = RulesEngine()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.getenv("SECRET_KEY", "geoflip-dev-secret")

    # Stash dependencies for handlers (closure capture is cleaner than g/app.config)
    app.config["GEOFLIP_CONFIG"] = config
    app.config["GEOFLIP_STATE_STORE"] = state_store
    app.config["GEOFLIP_NOMINATIM"] = nominatim_client
    app.config["GEOFLIP_OSRM"] = osrm_client
    app.config["GEOFLIP_RULES"] = rules_engine

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        state = state_store.load()
        q = (request.args.get("q") or "").strip()

        search_results = []
        info_message: str | None = None

        if q:
            try:
                results = nominatim_client.search(
                    q,
                    center_lat=config.DEFAULT_CENTER_LAT,
                    center_lon=config.DEFAULT_CENTER_LON,
                    search_km=5.0,
                )
            except NominatimError as exc:
                flash(f"搜尋失敗：{exc}", "error")
                results = []

            if not results:
                info_message = "沒有找到可用 POI，請換關鍵字或放大搜尋範圍"
            else:
                state.merge_discovered_pois(results)
                state_store.save(state)
                search_results = results

        current_player_id = state.current_player_id()
        anchor_count = len(state.anchor_pois(current_player_id))
        has_anchor = state.has_anchor_flag(current_player_id)
        can_use_trump = (
            state.players[current_player_id].trump_available and has_anchor
        )

        map_iframe_src = f"/map?v={state.turn_index}_{len(state.moves)}"

        return render_template(
            "index.html",
            state=state,
            current_player_id=current_player_id,
            scores=state.scores(),
            winner=state.winner(),
            status=state.status,
            search_query=q,
            search_results=search_results,
            info_message=info_message,
            current_player_anchor_count=anchor_count,
            current_player_has_anchor=has_anchor,
            current_player_can_use_trump=can_use_trump,
            map_iframe_src=map_iframe_src,
        )

    @app.post("/move")
    def move():
        poi_id = (request.form.get("poi_id") or "").strip()
        use_trump = request.form.get("use_trump") == "on"

        if not poi_id:
            flash("缺少 poi_id", "error")
            return redirect(url_for("index"))

        state = state_store.load()
        result = rules_engine.apply_move(state, poi_id, use_trump, osrm_client)

        if result.ok:
            state_store.save(result.state)
            flipped_n = len(result.flipped_poi_ids)
            if flipped_n:
                flash(f"插旗成功，翻面 {flipped_n} 個 POI", "success")
            else:
                flash("插旗成功", "success")
        else:
            flash(result.message, "error")
            # IMPORTANT: do NOT save — keep the on-disk state untouched.

        return redirect(url_for("index"))

    @app.post("/new-game")
    def new_game_route():
        state_store.reset()
        flash("新遊戲開始", "success")
        return redirect(url_for("index"))

    @app.get("/map")
    def map_view():
        # Phase 5 will replace with render_map_html(state_store.load(), config)
        return _MAP_STUB_HTML

    @app.get("/api/state")
    def api_state():
        state = state_store.load()
        return jsonify(state.to_dict())

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
