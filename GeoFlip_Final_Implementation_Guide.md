# GeoFlip — 最終實作指南 v1.0

> 這是 agent 可以直接照著做的完整規格。已整合所有修訂，所有規則為最終版。

---

# Part 1 — 規格鎖定（不可更動）

## 1.1 遊戲規則（最終）

1. 2 人 hotseat，同一瀏覽器輪流操作。
2. 全局共 16 回合，`turn_index = 0..15`。
   - 偶數回合：Player 1
   - 奇數回合：Player 2
3. 旗子只能插在 Nominatim 搜尋回來、已登錄於伺服器狀態中的 POI。
4. 只能插在中立 POI（`owner is None`）。不能插在已被任何玩家擁有的 POI。
5. 每位玩家的第一面旗可以插任意中立 POI（不檢查距離）。
6. 從該玩家第二面旗開始，新旗必須在該玩家任一 **anchor flag** 的 OSRM 步行時間 600 秒內。
7. 若某玩家被翻到完全沒有 anchor flag，下一次輪到他時允許重新在任意中立 POI 插旗。
8. 插旗成功後，系統用 OSRM 計算「新旗」到「目前玩家所有 anchor flag」的步行路線。**只取 `duration_s` 最小的那一條** 進入 buffer 流程。
9. 對該條路線做 buffer：
   - 一般旗：50m
   - 王牌旗：150m
10. buffer 內的對手 POI 全部翻成目前玩家。
11. 每位玩家一張王牌旗，只能成功使用一次。沒有 anchor flag 時不可使用王牌。
12. 無效落子不消耗王牌、不前進回合、不改任何 POI owner。
13. 分數永遠由目前 POI owner 即時計算，不儲存 cached score。
14. 16 回合結束後分數高者勝，平手顯示平手。

## 1.2 核心概念定義

| 名稱 | 定義 | 用途 |
|---|---|---|
| `owner` | POI 目前歸屬玩家 | 算分、顯示顏色、判斷可否插旗 |
| `flag` | 某玩家曾透過合法 move 主動插下的旗，來源是 `MoveRecord.placed_poi_id` | 推導 anchor |
| `anchor flag` | 某玩家自己插過 **且** 目前 `owner` 仍是該玩家的 POI | 10 分鐘檢查、OSRM route endpoint、王牌 UI 條件 |
| flipped POI | 因 buffer 翻面而取得的 POI | 只算分，**不** 當 route anchor |

**最重要的不變式**：route endpoint 只能從 `anchor_pois()` 取，**永遠不可** 用 `owned_pois()`。

## 1.3 技術架構

- Python 3.11+
- Flask（本機 web server）
- Folium（地圖 HTML）
- Nominatim API（搜尋 POI）
- OSRM API（步行路線）
- Shapely（buffer / point-in-polygon）
- pyproj（WGS84 → 公尺投影）
- pytest, pytest-mock（測試）
- **禁用**：React、Vue、Next.js、Google Maps、Mapbox、Overpass、SQL database
### 評分核心要求（不可替代）
- Nominatim API 必須用於搜尋候選 POI candidates。
- OSRM API 必須用於計算真實步行時間，並用於 600 秒規則判斷。
- Folium 必須產生互動式 Leaflet map，並嵌入 Flask web page。
- Overpass API 不可取代 Nominatim 作為核心 candidate finder。
**本專案是本機雙人 hotseat 遊戲，不實作線上多人、帳號、同步、WebSocket 或部署後多人共用狀態。但執行時會連線到 Nominatim 與 OSRM 公開 API。**

## 1.4 座標慣例

| 場景 | 順序 |
|---|---|
| `Poi.lat`, `Poi.lon` 欄位 | 分開存 |
| Folium marker / PolyLine | `[lat, lon]` |
| OSRM request path | `lon,lat` |
| OSRM GeoJSON response | `[lon, lat]` |
| `RouteRecord.coordinates_lonlat` | `[lon, lat]` |
| Shapely geometry | `(lon, lat)`，**投影後才 buffer** |
| pyproj Transformer | **一律 `always_xy=True`** |

## 1.5 專案目錄

```
geoflip/
  pyproject.toml
  README.md
  .env.example
  app/
    __init__.py
    config.py
    models.py
    state.py
    web.py
    game/
      __init__.py
      rules.py
    services/
      __init__.py
      nominatim.py
      osrm.py
      geometry.py
    map/
      __init__.py
      render.py
    templates/
      index.html
    static/
      app.css
  data/
    .gitkeep
  tests/
    fixtures/
    test_models.py
    test_state.py
    test_nominatim.py
    test_osrm.py
    test_rules.py
    test_web.py
    test_map_render.py
```

## 1.6 Data structure（Phase 1 定義，後續不可改）

### Type aliases
```python
PlayerId = Literal[1, 2]
GameStatus = Literal["active", "finished"]
```

### Dataclasses

| 結構 | 欄位 |
|---|---|
| `Poi` | `id: str`, `name: str`, `lat: float`, `lon: float`, `osm_type: str \| None`, `osm_id: int \| None`, `category: str`, `poi_type: str`, `score: int`, `owner: PlayerId \| None`, `discovered_turn: int \| None`, `placed_turn: int \| None`, `raw: dict` |
| `PlayerState` | `id: PlayerId`, `name: str`, `trump_available: bool` |
| `RouteRecord` | `id: str`, `turn_index: int`, `player_id: PlayerId`, `from_poi_id: str`, `to_poi_id: str`, `coordinates_lonlat: list[list[float]]`, `distance_m: float`, `duration_s: float`, `buffer_m: float` |
| `MoveRecord` | `turn_index: int`, `player_id: PlayerId`, `placed_poi_id: str`, `used_trump: bool`, `route_ids: list[str]` (max 1), `flipped_poi_ids: list[str]` |
| `GameState` | `game_id: str`, `turn_index: int`, `max_turns: int`, `players: dict[PlayerId, PlayerState]`, `pois: list[Poi]`, `routes: list[RouteRecord]`, `moves: list[MoveRecord]`, `created_at: str`, `updated_at: str`, `status: GameStatus` |
| `RouteResult` | `coordinates_lonlat: list[list[float]]`, `distance_m: float`, `duration_s: float` |
| `MoveResult` | `ok: bool`, `message: str`, `state: GameState`, `placed_poi_id: str \| None`, `flipped_poi_ids: list[str]`, `route_ids: list[str]` |

### ID 產生規則
- `GameState.game_id` = `"game_" + uuid.uuid4().hex`，在 `new_game()` 產生
- `RouteRecord.id` = `"route_" + uuid.uuid4().hex`，在 `RulesEngine.apply_move()` commit 時產生

### Score mapping（`score_poi(category, poi_type)`）

**3 分**
- `historic` 任意 type
- `tourism`: museum, attraction, gallery, zoo, theme_park
- `amenity`: university, hospital, theatre, arts_centre
- `leisure`: park, stadium

**2 分**
- `amenity`: restaurant, cafe, bar, fast_food, library, school, college, place_of_worship, marketplace
- `shop` 任意 type
- `railway`: station, halt
- `public_transport` 任意 type
- `tourism`: hotel, hostel, viewpoint
- `leisure`: garden, playground, sports_centre

**1 分**：其他

### GameState helpers（必須實作）
```
current_player_id() -> PlayerId
is_finished() -> bool
scores() -> dict[PlayerId, int]              # 即時計算，由 owner
winner() -> PlayerId | None                  # finished 後才有意義
owned_pois(player_id) -> list[Poi]           # 算分用，不可當 anchor
opponent_id(player_id) -> PlayerId
neutral_pois() -> list[Poi]
get_poi(poi_id) -> Poi | None
merge_discovered_pois(pois: list[Poi]) -> None
placed_flag_poi_ids(player_id) -> list[str]  # 由 moves 推導
placed_flag_pois(player_id) -> list[Poi]
anchor_pois(player_id) -> list[Poi]          # 唯一可當 route anchor 的 helper
has_anchor_flag(player_id) -> bool
```

### `merge_discovered_pois()` 規則
- 新 POI：`discovered_turn = state.turn_index`，`owner = None`，`placed_turn = None`
- 已存在 POI：完全不覆蓋 `owner` / `placed_turn` / `discovered_turn`
- 不可把已擁有 POI 變回 neutral

### `Poi.raw` 白名單（**只**保留以下欄位）
```
display_name, name, class, type, osm_type, osm_id, importance
address.{country_code, city, town, village, suburb, neighbourhood, road, house_number, postcode}
extratags.{website, wikidata, wikipedia, opening_hours, phone}
```
**不可保存**：geojson, boundingbox, icon, license, 完整 addressdetails, 完整 extratags

---

# Part 2 — Phase 實作

## Phase 依賴總覽

| Phase | 目標 | 依賴 |
|---|---|---|
| 0 | 專案骨架 | 無 |
| 1 | Domain model 與狀態儲存 | 0 |
| 2 | Nominatim / OSRM client | 1 |
| 3 | 遊戲規則與幾何 | 1, 2 |
| 4 | Flask backend | 1, 2, 3 |
| 5 | Folium map 與 UI | 1, 3, 4 |
| 6 | 整合測試與 playtest | 1–5 |
| 7 | Hardening 與文件 | 1–6 |

---

## Phase 0 — 專案骨架

**目標**：建立可安裝、可測試、可啟動的 Python 專案骨架。

### Agent system prompt
```
你是資深 Python full-stack engineer，負責從零建立 GeoFlip 專案骨架。

硬性限制：
- Python 3.11+
- 地圖必須用 Folium、POI 必須用 Nominatim、路線必須用 OSRM、buffer 必須用 Shapely
- 禁用 React/Vue/Next.js、Google Maps、Mapbox、Overpass
- 本 phase 只建骨架，不實作遊戲邏輯

輸出要求：
- 列出新增/修改檔案
- 提供每個檔案完整內容
- 不留 pseudo-code、不省略 imports
```

### Agent user prompt
```
Phase 0 任務：建立 GeoFlip 專案骨架。

1. 建立 pyproject.toml，dependencies 至少包含：
   flask, folium, shapely, pyproj, httpx, python-dotenv, pytest, pytest-mock

2. 建立 Part 1.5 指定的所有目錄與空檔案。

3. app/config.py 定義 Config dataclass，包含：
   DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON, DEFAULT_ZOOM,
   NOMINATIM_USER_AGENT, NOMINATIM_EMAIL, NOMINATIM_BASE_URL,
   OSRM_BASE_URL, OSRM_PROFILE,
   STATE_FILE, REQUEST_TIMEOUT_SECONDS, NOMINATIM_MIN_INTERVAL_SECONDS

4. app/web.py 建立最小 Flask app factory：
   - create_app(config=None)
   - GET / 回傳簡單頁面
   - GET /health 回傳 JSON {"ok": true}

5. tests/ 建立 smoke test：
   - 測 /health
   - 測 app 可 import

禁止：
- 不實作 Nominatim/OSRM client
- 不實作遊戲規則
- 不實作 Folium 細節
- 不加 database
```

### Checkpoint
- `pytest` 通過
- `python -m compileall app` 通過
- `create_app()` 可建立 Flask app

---

## Phase 1 — Domain model 與狀態儲存

**目標**：定義所有資料結構、序列化、分數計算、JSON 狀態檔讀寫。

### Agent system prompt
```
你是負責 GeoFlip domain model 的資深 Python engineer。

硬性限制：
- 只實作 app/models.py、app/state.py 與對應 tests
- 不呼叫外部 API、不碰 Flask、不碰 Folium
- 所有後續 phase 都會依賴你定義的資料結構，欄位語意必須穩定
- JSON serialization 不可包含 Shapely geometry 或不可序列化物件
```

### Agent user prompt
```
Phase 1 任務：實作 GeoFlip domain model 與 JSON state store。

完成 app/models.py：

1. 定義 type aliases：
   PlayerId = Literal[1, 2]
   GameStatus = Literal["active", "finished"]

2. 定義 dataclasses（欄位依 Part 1.6 表格）：
   Poi, PlayerState, RouteRecord, MoveRecord, GameState,
   RouteResult, MoveResult

3. 每個 dataclass 提供 to_dict / from_dict。
   GameState.from_dict() 遇到不是 "active" / "finished" 的 status 要 raise ValueError。

4. 實作 score_poi(category, poi_type) -> int，使用 Part 1.6 的 score mapping。

5. 實作 GameState helpers（依 Part 1.6 列表）。

關鍵實作細節：
- new_game() 的 game_id = "game_" + uuid.uuid4().hex
- new_game() status = "active"，turn_index = 0，max_turns = 16
- new_game() 建立 2 個 PlayerState，trump_available = true
- scores() 由目前 owner 即時計算，不可有 cached score 欄位
- anchor_pois(player_id) 必須同時滿足：
  a) poi.id 出現在該玩家 MoveRecord.placed_poi_id
  b) poi.owner == player_id
- merge_discovered_pois(pois)：
  - 新 POI 設 discovered_turn = self.turn_index
  - 不覆蓋既有 owner / placed_turn / discovered_turn

完成 app/state.py：

1. new_game() -> GameState
2. StateStore：
   - __init__(path)
   - load() -> GameState（不存在則回傳 new_game()）
   - save(state) -> None（atomic write：tmp file → replace）
   - reset() -> None
3. JSON 壞掉時 raise 清楚 exception，不可 silent reset

完成 tests/test_models.py、tests/test_state.py，至少覆蓋：
- new_game() current player 是 1
- 16 回合後 is_finished() 為 true
- score_poi 對 1/2/3 分 mapping 正確
- scores() 由 owner 即時計算
- Poi serialization round trip
- GameState serialization round trip
- StateStore load / save / reset
- merge_discovered_pois 不覆蓋既有 owner
- merge_discovered_pois 對新 POI 設 discovered_turn
- new_game().game_id 以 "game_" 開頭
- status 只能是 active / finished，壞值 from_dict raise ValueError
- anchor_pois 不包含 flipped POI
- anchor_pois 不包含自己插過但目前 owner 已被對手翻走的 POI

禁止：
- 不 import Flask / Folium / Shapely
- 不呼叫 Nominatim / OSRM
- 不把 score 存進 PlayerState
- 不把 lat/lon 合成 list
```

### Checkpoint
- `pytest tests/test_models.py tests/test_state.py` 通過
- 手動檢查 state JSON 無 Shapely object
- 確認 `anchor_pois()` 行為符合定義

---

## Phase 2 — Nominatim / OSRM client

**目標**：實作外部 API client，測試不打真實網路。

### Agent system prompt
```
你是負責 GeoFlip 外部 API integration 的資深 Python engineer。

硬性限制：
- POI 搜尋只能用 Nominatim
- 路線只能用 OSRM
- 測試不可打真實網路
- client 必須回傳 Phase 1 的 Poi 與 RouteResult
- 不實作遊戲規則、不實作 Flask route

重點：
- Nominatim 要有 User-Agent header
- Nominatim 要有最小間隔 rate limit
- OSRM request coordinate order 是 lon,lat
- OSRM response geometry 維持 GeoJSON lon,lat
```

### Agent user prompt
```
Phase 2 任務：實作 NominatimClient 與 OsrmClient。

完成 app/services/nominatim.py：

1. NominatimClient(base_url, user_agent, email, timeout_seconds, min_interval_seconds)

2. search(query, center_lat=None, center_lon=None, search_km=None, limit=10) -> list[Poi]

3. 呼叫 Nominatim search endpoint，params：
   format=jsonv2, q, limit, addressdetails=1, extratags=1, dedupe=1

4. 有 center + radius 時用 viewbox 限制範圍

5. in-memory cache，key 包含 query/center/radius/limit

6. rate limit：兩次 cache miss 至少間隔 min_interval_seconds（測試用 monkeypatch time）

7. normalize：
   - id = f"{osm_type}:{osm_id}"，缺則用 stable fallback（hash of lat+lon+name），不可撞 id
   - name 優先用 namedetails/name/display_name
   - lat/lon 轉 float
   - category = result["class"]
   - poi_type = result["type"]
   - score = score_poi(category, poi_type)
   - owner = None
   - raw 只保留 Part 1.6 的白名單欄位
   - **不可** 保存 geojson / boundingbox

8. 過濾：缺 lat/lon、boundary 且非明確景點

9. HTTP error / timeout / JSON error → custom exception

完成 app/services/osrm.py：

1. OsrmClient(base_url, profile, timeout_seconds)

2. route(from_lat, from_lon, to_lat, to_lon) -> RouteResult

3. URL 格式：
   /route/v1/{profile}/{from_lon},{from_lat};{to_lon},{to_lat}

4. params: overview=full, geometries=geojson, steps=false, annotations=false

5. 檢查 response code 與 OSRM code

6. 取 routes[0]
   coordinates_lonlat = routes[0].geometry.coordinates（維持 lon,lat）
   distance_m, duration_s 轉 float
   coordinates 少於 2 點 → exception

7. cache key：(from_lat, from_lon, to_lat, to_lon, profile)

8. error → custom exception

完成 tests/test_nominatim.py、tests/test_osrm.py，覆蓋：
- Nominatim result normalize 成 Poi
- lat/lon string 轉 float
- duplicate id 處理
- cache hit 不發 HTTP
- rate limiter 在 cache miss 生效（monkeypatch time）
- raw 不含 geojson / boundingbox
- OSRM request path 用 lon,lat
- OSRM GeoJSON coordinates 維持 lon,lat
- OSRM error 轉 exception
- OSRM coordinates < 2 點 raise exception

禁止：
- tests 不打真實 API
- 不加入 Overpass
- 不在 client 改 GameState
- 不在 client 實作 10 分鐘規則
```

### Checkpoint
- `pytest tests/test_nominatim.py tests/test_osrm.py` 通過
- OSRM request path 是 `lon,lat;lon,lat`
- Nominatim headers 有 User-Agent
- 測試無真實網路依賴

---

## Phase 3 — 遊戲規則與幾何（核心）

**目標**：實作合法落子、10 分鐘限制、buffer 翻面、王牌、回合推進。**只使用最短 route**。

### Agent system prompt
```
你是負責 GeoFlip 核心遊戲規則的資深 Python engineer。

硬性限制：
- 使用 Phase 1 的 GameState / Poi / RouteResult / RouteRecord / MoveRecord / MoveResult
- 透過 routing protocol 使用 OSRM，不直接發 HTTP
- 使用 Shapely 做 buffer 與 point-in-polygon
- 使用 pyproj 投影到公尺座標後才 buffer（always_xy=True）
- 不實作 Flask route、不實作 Folium map
- 不改 Phase 1 data model 欄位語意

關鍵規則：
- 只能插中立 POI
- 每位玩家第一面旗可任意中立 POI
- 之後必須距離任一 anchor flag <= 600 秒步行
- 若玩家沒有 anchor flag，允許任意中立 POI
- 計算到所有 anchor flag 的 OSRM route，取 duration_s 最小那條
- 一般 buffer 50m，王牌 buffer 150m
- 沒有 anchor flag 不可使用王牌
- buffer 內對手 POI 翻面
- invalid move 不 mutate state（transaction-like）
```

### Agent user prompt
```
Phase 3 任務：實作 RulesEngine 與 geometry helpers。

完成 app/services/geometry.py：

1. choose_metric_crs(lon, lat) -> CRS
   - 根據 lon/lat 選 UTM zone

2. build_meter_transformers(reference_lon, reference_lat)
   - 回傳 (to_meters, to_wgs84) 兩個 pyproj Transformer
   - always_xy=True

3. route_to_meter_linestring(coordinates_lonlat, to_meters_transformer) -> shapely.LineString

4. buffer_route_meters(line_meters, buffer_m) -> shapely.Polygon

5. point_in_buffer(poi_lat, poi_lon, buffer_polygon, to_meters_transformer) -> bool
   - 使用 polygon.covers(point) 或 polygon.distance(point) <= 0
   - 不要只用 contains，邊界 POI 會 flaky

6. 所有距離判斷都在投影後的公尺座標進行，**禁止** 在 WGS84 degree 上 buffer

完成 app/game/rules.py：

1. 定義 RoutingService protocol：
   route(from_lat, from_lon, to_lat, to_lon) -> RouteResult

2. RulesEngine.apply_move(state, poi_id, use_trump, routing_service) -> MoveResult

apply_move 流程：

【驗證階段】（不可 mutate state）

1. 若 state.status == "finished" 或 state.turn_index >= state.max_turns：
   return invalid("遊戲已結束")

2. current_player_id = state.current_player_id()
   player = state.players[current_player_id]

3. candidate = state.get_poi(poi_id)
   - 不存在 → invalid("POI 不存在")
   - candidate.owner is not None → invalid("該地點已被擁有")

4. 若 use_trump == true：
   - player.trump_available == false → invalid("王牌已使用")

5. old_anchor_pois = state.anchor_pois(current_player_id)
   注意：是落子前 snapshot，不含 flipped POI，不含已被翻走的舊旗

【分支 A：沒有 anchor flag】

6. 若 old_anchor_pois 為空：
   - 若 use_trump == true → invalid("沒有可放大的路線，無法使用王牌")
   - 跳到【commit 階段】，chosen_route = None

【分支 B：有 anchor flag】

7. 對每個 old_anchor_poi 呼叫 routing_service.route(candidate, anchor)
   - 收集成功的 RouteResult，與對應 anchor poi_id 一起記錄
   - 失敗的 route 直接跳過，不算 invalid

8. 若沒有任何成功 route → invalid("找不到步行路線")

9. 從成功 route 中取 duration_s 最小那條（含其 anchor poi_id）為 chosen_route

10. 若 chosen_route.duration_s > 600 → invalid("新旗必須在己方旗子步行 10 分鐘內")

【commit 階段】（從這裡開始才 mutate state，先 deepcopy）

11. new_state = deepcopy(state)
    new_candidate = new_state.get_poi(poi_id)
    new_player = new_state.players[current_player_id]

12. new_candidate.owner = current_player_id
    new_candidate.placed_turn = state.turn_index

13. route_ids = []
    flipped_poi_ids = []

14. 若 chosen_route 存在：
    buffer_m = 150 if use_trump else 50
    
    # 投影到公尺
    to_m, _ = build_meter_transformers(new_candidate.lon, new_candidate.lat)
    line_m = route_to_meter_linestring(chosen_route.coordinates_lonlat, to_m)
    buffer_poly = buffer_route_meters(line_m, buffer_m)
    
    # 翻面
    opponent_id = new_state.opponent_id(current_player_id)
    for poi in new_state.pois:
        if poi.owner == opponent_id and point_in_buffer(poi.lat, poi.lon, buffer_poly, to_m):
            poi.owner = current_player_id
            flipped_poi_ids.append(poi.id)
    
    # 建立 RouteRecord
    route_record = RouteRecord(
        id="route_" + uuid.uuid4().hex,
        turn_index=state.turn_index,
        player_id=current_player_id,
        from_poi_id=new_candidate.id,
        to_poi_id=chosen_anchor_poi_id,
        coordinates_lonlat=chosen_route.coordinates_lonlat,
        distance_m=chosen_route.distance_m,
        duration_s=chosen_route.duration_s,
        buffer_m=buffer_m,
    )
    new_state.routes.append(route_record)
    route_ids.append(route_record.id)

15. 若 use_trump：new_player.trump_available = false

16. move_record = MoveRecord(
        turn_index=state.turn_index,
        player_id=current_player_id,
        placed_poi_id=new_candidate.id,
        used_trump=use_trump,
        route_ids=route_ids,
        flipped_poi_ids=flipped_poi_ids,
    )
    new_state.moves.append(move_record)

17. new_state.turn_index += 1
    new_state.updated_at = now()
    若 new_state.turn_index >= new_state.max_turns: new_state.status = "finished"

18. return MoveResult(ok=true, message="OK", state=new_state, 
                     placed_poi_id=new_candidate.id,
                     flipped_poi_ids=flipped_poi_ids,
                     route_ids=route_ids)

完成 tests/test_rules.py，覆蓋：
- 第一面旗可任意中立 POI
- 第二面旗必須 <= 600 秒
- > 600 秒 invalid
- 只能插中立 POI
- invalid move 不改 state（turn_index、owner、trump 都不變）
- 50m buffer 翻近點不翻遠點（用 pyproj 投影產生公尺級 fixture）
- 王牌 150m buffer 翻 100m 附近的點
- 王牌成功後 trump_available = false
- 王牌 invalid 不消耗
- route target 來自 anchor_pois，不來自 owned_pois
- flipped POI 不會被當下次 route 的 anchor
- 玩家擁有 flipped POI 但沒 anchor 時：可以 free place，但不能 use_trump
- 多個 anchor：一條 route 失敗、一條 <=600 成功 → valid，使用成功那條
- 多個 anchor：所有 route 失敗 → invalid
- 多個 anchor：成功 route 都 > 600 → invalid
- 16 回合後 status = "finished"
- 第 17 次 move invalid
- 只產生 1 條 RouteRecord per valid move
- MoveRecord.route_ids 長度 <= 1

禁止：
- 不在 degree 上 buffer
- 不把 opponent POI 當 route endpoint
- 不讓 invalid move 留下任何副作用
- 不在 route 部分失敗時建立半套 RouteRecord
- 不用直線距離取代 OSRM
```

### Checkpoint
- `pytest tests/test_rules.py` 通過
- 確認 `apply_move()` 的 mutation 全在 validation 後
- 確認每次 valid move 只產生 1 條 RouteRecord
- 確認 fixture 用 pyproj 產生公尺級座標

---

## Phase 4 — Flask backend

**目標**：把 state / Nominatim / OSRM / RulesEngine 接起來。

### Agent system prompt
```
你是負責 GeoFlip Flask backend 的資深 full-stack engineer。

硬性限制：
- Flask 本機 web server，無資料庫
- 狀態使用 Phase 1 StateStore JSON file
- 不重寫遊戲規則，全部走 RulesEngine
- 前端不可提交任意 lat/lon，只能提交 server 已知 poi_id
- 支援 dependency injection 便於測試
```

### Agent user prompt
```
Phase 4 任務：實作 Flask backend flow。

完成 app/web.py：

1. create_app(config=None, state_store=None, nominatim_client=None, 
              osrm_client=None, rules_engine=None) -> Flask
   - 所有依賴可注入，預設 None 時自動建立

2. Routes：

GET /health → JSON {"ok": true}

GET /
- state = state_store.load()
- q = request.args.get("q")
- search_results = []
- info_message = None
- 若 q 存在：
    try:
        results = nominatim_client.search(q, center_lat=config.DEFAULT_CENTER_LAT, ...)
    except NominatimError:
        flash error
    if results == []:
        info_message = "沒有找到可用 POI，請換關鍵字或放大搜尋範圍"
    else:
        state.merge_discovered_pois(results)
        state_store.save(state)
        search_results = results
- 傳入 template：
    state, current_player_id, scores, winner, status,
    search_results, info_message,
    current_player_anchor_count = len(state.anchor_pois(current_player_id)),
    current_player_has_anchor = state.has_anchor_flag(current_player_id),
    current_player_can_use_trump = (
        state.players[current_player_id].trump_available
        and state.has_anchor_flag(current_player_id)
    )

POST /move
- poi_id = request.form["poi_id"]
- use_trump = request.form.get("use_trump") == "on"
- state = state_store.load()
- result = rules_engine.apply_move(state, poi_id, use_trump, osrm_client)
- if result.ok:
    state_store.save(result.state)
    flash success（含翻面數）
  else:
    flash error(result.message)
    # 不 save，state 保持原樣
- redirect "/"

POST /new-game
- state_store.reset()
- flash "新遊戲開始"
- redirect "/"

GET /map
- state = state_store.load()
- return render_map_html(state, config)  # Phase 5 實作；Phase 4 可先回 stub

GET /api/state
- state = state_store.load()
- return jsonify(state.to_dict())

完成 tests/test_web.py（使用 Flask test_client 與 fake services）：
- /health
- GET / 可 render
- GET /?q=... 呼叫 fake nominatim 並 merge POI
- GET /?q=... 回 0 筆，畫面顯示 info_message
- POST /move 成功 → state 被 save、turn_index 前進
- POST /move invalid → state 不變、turn_index 不變
- POST /new-game → state reset
- /api/state 回 JSON
- current_player_can_use_trump：玩家只有 flipped POI 時為 false

禁止：
- 不在 route 寫 Shapely buffer
- 不在 route 組 OSRM URL
- 不允許 form 提交 lat/lon
- 不加 auth / WebSocket / SQL database
- state_store 不可是 module-level singleton
```

### Checkpoint
- `pytest tests/test_web.py` 通過
- 手動啟動：`/health`、`/`、`/api/state` 正常
- invalid move 後 `turn_index` 不變
- 0 筆搜尋有提示

---

## Phase 5 — Folium map 與 hotseat UI

**目標**：瀏覽器可玩，地圖由 Folium 產生，sidebar 控制遊戲。

### Agent system prompt
```
你是負責 GeoFlip 地圖與 UI 的資深 full-stack engineer。

硬性限制：
- 地圖必須用 Folium，禁止手寫 Leaflet 取代
- Folium map 只顯示，不修改 state
- 修改 state 一律走 Flask POST
- 不依賴 Folium marker click callback（瀏覽器做不到）
- /map 單獨回 Folium HTML，主頁用 iframe 嵌入，避免 html/head/body 衝突
```

### Agent user prompt
```
Phase 5 任務：實作 Folium renderer 與可玩 UI。

完成 app/map/render.py：

1. render_map_html(state, config) -> str

2. folium.Map 設定：
   - center：
       若 state.pois 非空：所有 POI lat/lon 平均
       否則：config.DEFAULT_CENTER_LAT/LON
   - zoom：config.DEFAULT_ZOOM

3. Marker：
   - neutral: 灰色 CircleMarker
   - Player 1: 藍色
   - Player 2: 紅色
   - has_flag（poi 在該玩家 placed_flag_poi_ids 中）：用 folium.Marker（pin 圖示）代替 CircleMarker
   - popup 內容（HTML escape）：
       POI name, category/type, score, owner（玩家名）, 
       是否為 anchor flag（在 state.anchor_pois(owner) 中）

4. Route polyline：
   - 遍歷 state.routes
   - coordinates_lonlat 轉成 [[lat, lon], ...]
   - Player 1: 藍色，Player 2: 紅色
   - buffer_m == 150 的 route：線粗 5 + dash style
   - buffer_m == 50 的 route：線粗 3

5. 若 state.pois 非空：fit_bounds 到所有 POI

6. 若 state.status == "finished"：地圖角落加文字 marker 顯示結果

修改 app/web.py：
- GET /map 呼叫 render_map_html
- GET / 傳給 template map_iframe_src = f"/map?v={state.turn_index}_{len(state.moves)}"
  （避免瀏覽器 cache 舊地圖）

完成 templates/index.html：

1. 雙欄 layout：左 sidebar（350px 寬），右 map iframe（高 80vh）

2. Sidebar 顯示：
   - "GeoFlip" title
   - 回合：{turn_index + 1} / {max_turns}
   - 目前玩家（顏色 badge）
   - Player 1 分數 / Player 2 分數
   - 王牌狀態（兩位玩家各自）
   - 若 status == "finished"：顯示 "Player X 勝利" 或 "平手"

3. 搜尋表單（GET /，input name=q）
   - submit button "搜尋"
   - 提示文字告訴使用者按 enter 送出

4. info_message 顯示區塊

5. 搜尋結果列表：
   - 每筆顯示 name / category-type / score / owner
   - neutral POI：
       POST /move form，hidden poi_id，submit button "插旗"
       若 current_player_can_use_trump：顯示 use_trump checkbox
   - 非 neutral：顯示 owner 並 disabled button

6. flash messages 區塊

7. POST /new-game form，button "新開局"

8. 右側：<iframe src="{{ map_iframe_src }}" width="100%" height="100%"></iframe>

完成 static/app.css：
- 雙欄 flex layout
- player 1 / player 2 color badge
- 搜尋結果卡片
- map iframe 撐滿右欄

完成 tests/test_map_render.py：
- render_map_html 包含 POI name（HTML escape 後）
- 包含 route polyline 資料
- lon/lat 路線正確轉成 lat/lon
- 不同 owner marker 顏色不同
- 空 state 也能 render
- placed flag POI 用不同 marker style

禁止：
- 不在 Folium popup 寫主要插旗流程
- 不用 JavaScript 寫遊戲邏輯
- 不在 browser 直接打 Nominatim/OSRM
- 不導入前端 build tool
- 不用 contains 判斷邊界 POI
```

### Checkpoint
- `pytest tests/test_map_render.py tests/test_web.py` 通過
- 手動：搜尋 → 插第一旗 → 換人插 → 嘗試超過 10 分鐘 → 看翻面動作
- iframe reload 不是舊地圖

---

## Phase 6 — 整合測試與 playtest

**目標**：不打真實 API 的整合測試 + 手動驗收 checklist。

### Agent system prompt
```
你是負責 GeoFlip 測試完整性的 QA-minded engineer。

硬性限制：
- 測試不打真實 Nominatim / OSRM
- 使用 pytest
- 不改核心規則，除非測試揭露明確 bug
- 修 bug 不得破壞 Phase 1 data contract
```

### Agent user prompt
```
Phase 6 任務：補齊整合測試與 playtest checklist。

建立 tests/fixtures：

1. fake_nominatim_results.json
   - 至少 8 個 POI
   - 1/2/3 分各有
   - 同一小區域，使用 pyproj 投影產生公尺級 offset：
     base = (25.0330, 121.5654)
     使用 choose_metric_crs + Transformer（always_xy=True）
     POI offset：(0,0), (30E, 0), (80E, 0), (100E, 30N), 
                 (180E, 0), (0, 600m), ... 等

2. fake_osrm_routes.json
   - route geometry 維持 lon,lat
   - 包含 duration <= 600 與 > 600
   - 一條路線 50m buffer 可翻指定 POI
   - 一條路線只有 150m trump 可翻指定 POI

3. fixture helper：產生 fake_route(from_poi, to_poi, duration_s)
   - 用 pyproj 在兩點間插值
   - 返回 RouteResult

建立 integration tests：

test_full_game_flow_without_network
- new_game → fake search → merge POI
- P1 第一旗 free → 翻面 0（沒有 anchor，沒有 route）
- P2 第一旗 free → 翻面 0
- P1 第二旗距 P1 anchor <= 600 → 翻 P2 部分 POI
- 驗證 scores 正確、routes 數 == 1

test_trump_flow
- P1 使用 trump → 150m 內 P2 POI 被翻
- P1.trump_available == false
- P1 第二次 use_trump → invalid，trump_available 仍是 false

test_invalid_move_transaction
- route duration > 600 → invalid
- turn_index 不變、owner 不變、trump 不消耗、routes 不增、moves 不增

test_route_failure_partial_success
- 玩家有 2 個 anchor，一條 route 失敗、一條 <= 600 → valid
- 使用成功那條的 buffer 翻面
- routes 只增 1 條

test_route_all_failed
- 玩家有 2 個 anchor，兩條 route 都失敗 → invalid

test_finished_after_16_valid_moves
- 16 次 valid → status = "finished"
- winner 或 tie 可計算
- 第 17 次 invalid

test_only_flipped_pois_cannot_use_trump
- 玩家只擁有 flipped POI、沒 anchor → use_trump invalid

test_web_flow_with_fake_services
- GET /?q=... → merge POI
- POST /move 成功 → state 前進
- GET /map → 200 含 polyline data
- GET /api/state → JSON

README 新增 Manual Playtest Checklist：
1. 建虛擬環境、pip install -e .
2. cp .env.example .env，填入需要的值
3. flask run（或 python -m app.web）
4. 開瀏覽器 → 搜尋 POI（例：新竹車站）
5. P1 插第一旗
6. P2 插第一旗（任意中立）
7. P1 搜尋附近 POI，插第二旗（< 10 分鐘）
8. 觀察翻面與路線
9. 使用王牌測試
10. 玩到 16 回合，確認結算

禁止：
- 不為了測試打真實 API
- tests 不依賴執行順序
- 不用 sleep 測 rate limit，用 monkeypatch time
- 不加大型 binary fixture
```

### Checkpoint
- 完整 `pytest` 通過
- 隨機重跑單一測試無 order dependency
- 拔網路執行測試仍通過
- 人類照 checklist 玩至少 4 回合

---

## Phase 7 — Hardening 與文件

**目標**：可交付狀態，README 完整，錯誤訊息友善。

### Agent system prompt
```
你是負責 GeoFlip final hardening 的 full-stack engineer。

硬性限制：
- 不改核心規則
- 不新增大型架構
- 不引入 database / 前端框架
- README 要包含可操作命令與 troubleshooting
- 測試必須全通過
```

### Agent user prompt
```
Phase 7 任務：hardening 與文件整理。

完成 README.md：
1. 專案介紹（含遊戲規則簡介）
2. 技術限制對應表（Nominatim / OSRM / Folium / Shapely / pyproj）
3. 安裝步驟（虛擬環境、依賴）
4. .env 設定說明
5. 啟動方式
6. 測試方式
7. Manual Playtest Checklist
8. Troubleshooting
9. 已知限制（hotseat 限制、Nominatim rate limit、OSRM public service 不穩等）

完成 .env.example：
DEFAULT_CENTER_LAT=25.0330
DEFAULT_CENTER_LON=121.5654
DEFAULT_ZOOM=15
NOMINATIM_USER_AGENT=geoflip-coursework/0.1 (your-email@example.com)
NOMINATIM_EMAIL=your-email@example.com
NOMINATIM_BASE_URL=https://nominatim.openstreetmap.org
NOMINATIM_MIN_INTERVAL_SECONDS=1.0
OSRM_BASE_URL=https://router.project-osrm.org
OSRM_PROFILE=foot
STATE_FILE=data/state.json
REQUEST_TIMEOUT_SECONDS=10

錯誤處理：
- Nominatim timeout → "搜尋服務暫時無法使用，請稍後再試"
- OSRM no route → "找不到步行路線"
- OSRM profile 錯 → "OSRM walking profile 設定可能不正確，請檢查 OSRM_PROFILE"
- state JSON 壞 → log 清楚錯誤，README 提供修復方式（刪除 / 備份）；不 silent reset

Logging：
- 對 search / move / route failure / invalid move 做 structured-ish log
- 不 log 完整 raw API response

啟動：
- flask run 與 python -m app.web 都可
- app/web.py 加 main guard

Cleanup：
- 移除未使用 imports / debug print
- 確認 pytest 全綠
- 確認 README 沒承諾不存在的功能

禁止：
- 不在 hardening 階段改規則
- 不吞錯誤顯示 generic failure
- 不加 Docker / auth / 線上多人
- 不改為資料庫儲存
- 不把 OSRM fallback 為直線距離
```

### Checkpoint
- Clean virtualenv 安裝後 `pytest` 通過
- 啟動 app 完成 4 回合
- 故意設錯 OSRM profile → 錯誤訊息可理解
- 故意製造壞 state JSON → 不會 silent reset
- 人類能照 README 從零跑起來

---

# Part 3 — 已知陷阱

| 陷阱 | 症狀 | 修正 |
|---|---|---|
| 把 `owned_pois()` 當 route anchor | OSRM call 爆量，後期常 invalid | 用 `anchor_pois()` |
| flipped POI 變 anchor | 不公平的網狀擴張 | flipped 只算分，不當 anchor |
| 所有 route 都納入 buffer | 視覺亂、策略不直觀 | **只用 duration 最短的成功 route** |
| 任一 route 失敗就 invalid | 多 anchor 時容易卡死 | 失敗略過，至少一條成功且 <= 600 即合法 |
| `status` 字串不一致 | phase 間衝突 | `Literal["active", "finished"]`，from_dict 驗證 |
| `discovered_turn` 永遠 None | 無法 debug | merge 時設 `state.turn_index` |
| `RouteRecord.id` / `GameState.game_id` 不一致 | serialization 不穩 | 固定 UUID prefix |
| 搜尋 0 筆靜默 | 使用者困惑 | 顯示提示 |
| 王牌 UI 用 owned POI 判斷 | UI 顯示但後端 invalid | UI 改用 `has_anchor_flag()` |
| `Poi.raw` 保存完整 response | state JSON 膨脹 | whitelist raw 欄位 |
| fixture 用 degree 猜距離 | buffer 測試 flaky | 用 pyproj 公尺 offset |
| Nominatim rate limit | 429 / IP 暫封 | 不做 autocomplete、加 User-Agent、cache、min interval |
| Nominatim 結果不是 POI | 搜到行政區 | normalize 過濾、缺 lat/lon 丟掉 |
| Nominatim lat/lon 字串 | geometry 計算錯 | 立即轉 float |
| OSRM coordinate order | route 跑錯國家 | request `lon,lat;lon,lat` |
| Folium coordinate order | marker 位置錯 | Folium 永遠 `[lat, lon]` |
| Shapely 在 degree buffer | buffer 變幾十公里 | 投影到 UTM 再 buffer |
| pyproj axis order | 結果顛倒 | `always_xy=True` |
| OSRM 公開服務不穩 | 測試 flaky | tests 全 mock |
| route 終點是對手旗 | 對手旗必被翻 | 只 route 到自己 anchor |
| Player 2 第一手被卡 | 用全局 turn 判斷新手 | 每位玩家自己第一面旗 free |
| 玩家 0 anchor 卡死 | 沒辦法插旗 | 0 anchor 時允許任意中立 |
| invalid 消耗王牌 | 體驗錯誤 | 全部 validation 過才 commit trump |
| route 部分成功半套 RouteRecord | state 髒 | transaction-like apply_move |
| score cached drift | 翻面後分數不準 | `scores()` 每次重算 |
| Folium 無 Python callback | 點 marker 改不了 state | sidebar HTML form |
| Folium HTML 嵌入主頁衝突 | nested html/head/body | `/map` 獨立 + iframe |
| iframe cache 舊地圖 | 插旗後不更新 | `src` 加版本 query |
| popup HTML injection | XSS | 全部 escape |
| 前端送 lat/lon 繞過 | 玩家作弊 | `/move` 只收 poi_id |
| Flask 全域 mutable state | 測試污染 | dependency injection |
| JSON state 壞 silent reset | 資料消失 | 明確 raise，README 教修復 |
| route geometry < 2 點 | Shapely 爆 | OSRM client 驗證 |
| POI 在 buffer 邊界 | flaky | 用 `covers` 或 `distance <= 0` |
| route cache miss 過多 | 效能差 | round coordinates 當 key 或用 poi_id |

---

*v1.0 — 可直接交給 agent 實作。*
