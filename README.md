## Taipower Outage Crawler

This repository contains a small crawler that collects the planned outage announcements published by Taipower on the page at `https://www.taipower.com.tw/2289/2406/2420/2421/11934/`.

### Prerequisites

```bash
python3 -m pip install -r requirements.txt
```

### Usage

```bash
python3 taipower_crawler.py \
  --output taipo_power.json \
  --raw-output taipo_power_raw.json \
  --timeout 30 \
  --limit 5 \
  --regions 北部 中部 \
  --verbose
```

By default, the script writes all branches to `taipower_outages.json`. Each notice now also includes an `addresses` array where the raw描述文字被清理成可拿去 geocode 的候選地址。Use `-` as the output path to send the JSON to stdout.

### Mapbox Geocoding (Optional)

Provide a Mapbox access token either through `--mapbox-token` or the `MAPBOX_ACCESS_TOKEN` environment variable to have the crawler call Mapbox’s forward geocoding API for every notice：

```bash
export MAPBOX_ACCESS_TOKEN=pk.your_token_here
python3 taipower_crawler.py \
  --limit 2 \
  --mapbox-country tw \
  --mapbox-language zh-Hant \
  --mapbox-types address,place \
  --geocode-cache .cache/mapbox.json
```

When enabled the crawler will try each candidate address until Mapbox returns a hit, then append the chosen result under `notice["geocode"]` (containing `lat`, `lng`, `matched_name`, `relevance`, etc.). Use `--geocode-cache` to persist lookups between runs and `--mapbox-delay` if你需要降低 QPS。

The crawler also會自動把原始描述清理成容易 geocode 的格式：會把全形符號、區間（例如「335至341號」）拆成端點、移除常見的「右50公尺空地」「臨時用電」等註記，並避免重複附加縣市名稱。`addresses` 仍保留所有原始拆分結果（筆數記在 `address_entry_count`），而 `address_streets` / `address_count` 則提供整理後的「只到路／街」清單與其筆數，同時 `address_groups` 會把每條路對應的完整地址列表分開存放；若你在輸出中看到尚未處理的特殊格式，歡迎調整 `taipower/processing.py` 內的清理規則。

若只想整理資料而暫時不打 geocoding API，可以在命令列加上 `--disable-geocode`（即使環境變數已設定 token 也會跳過查詢）。同時，當公告文字含有「取消停電」等關鍵字時，輸出的 notice 會多一個 `cancelled: true`，且預設不會對該筆去 geocode。

此外可以同時輸出兩份 JSON：`--output` 會產出處理後的資料（含 `addresses`、可選的 `geocode`），`--raw-output` 則保留原始公告欄位（不含地址清理結果），方便比對或備份最原始資訊。

### API Server

若需要把整理後的資料提供給前端，可啟動內建的 Flask API：

```bash
export TAIPOWER_DATA_PATH=processed.json   # 預設就讀 processed.json，可省略
python3 app.py                             # 或 FLASK_APP=app.py flask run
```

可用的端點：

- `GET /health`：簡單健康檢查。
- `GET /outages?date=2025-11-07&street=台北市大安區安和路二段&area=大安區&include_groups=true`
  - `date`（可選）：只回傳指定日期的公告。
  - `street`（可選）：只回傳包含該路名的公告。
  - `area`（可選）：指定行政區（例如 `大安區`）。公告若涵蓋多個區，其 `areas` 內含該值就會被回傳。
  - `include_groups=true` 時會加上 `address_groups` 供前端直接取完整巷弄清單。
  - 每筆 response 只保留前端需要的欄位：`date`, `time_window`, `type`（現為 `"停電"`）、`cities`, `areas`, `addresses`, `address_entry_count`, `address_streets`, `address_group_counts`，以及 `address_groups`（在 `include_groups=true` 時才會出現）。
- `POST /outages/query`：若前端需要以 POST 送出篩選條件，可傳 JSON body，例如：
  ```json
  {
    "date": "2025-11-07",
    "area": "大安區",
    "street": "台北市大安區安和路二段",
    "include_groups": true
  }
  ```
  回傳格式與 GET 版本相同。

回傳格式僅包含前端需要的欄位，便於直接渲染地圖或列表。

### TLS Notes

The Taipower main page occasionally serves certificates that Python fails to verify. When that happens the crawler automatically falls back to an insecure mode (certificate validation disabled) and logs a warning. Use `--no-insecure-fallback` if you prefer the script to abort instead.
