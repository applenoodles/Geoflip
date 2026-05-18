from __future__ import annotations

import logging
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
from app.map.render import render_map_html
from app.services.nominatim import NominatimClient, NominatimError
from app.services.osrm import OsrmClient
from app.state import StateStore

load_dotenv()

logger = logging.getLogger("geoflip.web")


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
                logger.info("search q=%r → %d results", q, len(results))
            except NominatimError as exc:
                logger.warning("search q=%r failed: %s", q, exc)
                flash(f"搜尋失敗：{exc}", "error")
                results = []

            if not results:
                info_message = "沒有找到可用 POI，請換關鍵字或放大搜尋範圍"
            else:
                state.merge_discovered_pois(results)
                state_store.save(state)
                # Rebuild from state so already-owned POIs reflect their REAL owner,
                # not the owner=None on the freshly-fetched Nominatim objects.
                search_results = [
                    state.get_poi(p.id) for p in results
                    if state.get_poi(p.id) is not None
                ]

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
            logger.info(
                "move ok turn=%d player=%d poi=%s trump=%s flipped=%d routes=%d",
                state.turn_index, state.current_player_id(),
                poi_id, use_trump, flipped_n, len(result.route_ids),
            )
            if flipped_n:
                flash(f"插旗成功，翻面 {flipped_n} 個 POI", "success")
            else:
                flash("插旗成功", "success")
        else:
            logger.info(
                "move INVALID turn=%d player=%d poi=%s trump=%s reason=%s",
                state.turn_index, state.current_player_id(),
                poi_id, use_trump, result.message,
            )
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
        state = state_store.load()
        return render_map_html(state, config)

    @app.get("/api/state")
    def api_state():
        state = state_store.load()
        return jsonify(state.to_dict())

    return app


def _configure_root_logging() -> None:
    """Idempotent root-logger setup for `python -m app.web` invocations."""
    if logging.getLogger().handlers:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


if __name__ == "__main__":
    _configure_root_logging()
    app = create_app()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host=host, port=port, debug=debug)
