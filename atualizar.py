import json
import os
import sys
from datetime import datetime, timezone
import urllib.request
import urllib.parse

API_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip()
if not API_KEY:
    print("ERRO: Variável FOOTBALL_API_KEY não configurada nos Secrets do GitHub.")
    sys.exit(1)

BASE_URL = "https://v3.football.api-sports.io"
TEAM_FLU = 124
TEAM_BOTA = 120

LEAGUE_BRASILEIRAO = 71
LEAGUE_COPA_BRASIL = 73
LEAGUE_LIBERTADORES = 13
LEAGUE_CARIOCA_IDS = {624, 629}

FINISHED_STATUSES = {"FT", "AET", "PEN"}
DATA_FILE = "dados.json"


def api_get(endpoint, params):
    url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": API_KEY,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_metric(dados, metric_id):
    for m in dados["metrics"]:
        if m["id"] == metric_id:
            return m
    return None


def parse_iso(dt_str):
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def format_sg(sg):
    return f"+{sg}" if sg > 0 else str(sg)


def sync_pontos_corridos_cards(dados):
    pc = dados["pontos_corridos"]
    flu = pc["flu"]
    bota = pc["bota"]

    m_pts = get_metric(dados, "pts_pc")
    if m_pts:
        m_pts["flu"] = flu["pts"]
        m_pts["bota"] = bota["pts"]
        m_pts["noteF"] = f'{flu["j"]}J • {flu["v"]}V • {flu["e"]}E • {flu["d"]}D (+2 STJD 03)'
        m_pts["noteB"] = f'{bota["j"]}J • {bota["v"]}V • {bota["e"]}E • {bota["d"]}D'

    m_gp = get_metric(dados, "gp_pc")
    if m_gp:
        sg_f = flu["gp"] - flu["gc"]
        sg_b = bota["gp"] - bota["gc"]
        m_gp["flu"] = flu["gp"]
        m_gp["bota"] = bota["gp"]
        m_gp["noteF"] = f'GS: {flu["gc"]} • SG: {format_sg(sg_f)}'
        m_gp["noteB"] = f'GS: {bota["gc"]} • SG: {format_sg(sg_b)}'


def sync_cdb_pct(dados):
    m_jog = get_metric(dados, "cdb_jog")
    m_vit = get_metric(dados, "cdb_vit")
    if m_jog and m_vit:
        if m_jog["flu"] > 0:
            pct_f = (m_vit["flu"] / m_jog["flu"]) * 100
            m_vit["noteF"] = f"{pct_f:.2f}%".replace(".", ",")
        if m_jog["bota"] > 0:
            pct_b = (m_vit["bota"] / m_jog["bota"]) * 100
            m_vit["noteB"] = f"{pct_b:.2f}%".replace(".", ",")


def sync_classico_cards(dados):
    rec = dados["classico_recortes"]
    mapping = [
        ("cl_vit", rec["geral"]["flu"], rec["geral"]["bota"]),
        ("cl_gols", rec["geral"]["gf"], rec["geral"]["gb"]),
        ("cl_car", rec["carioca"]["flu"], rec["carioca"]["bota"]),
        ("cl_br", rec["brasileirao"]["flu"], rec["brasileirao"]["bota"]),
        ("cl_mar", rec["maracana"]["flu"], rec["maracana"]["bota"]),
        ("cl_eng", rec["engenhao"]["flu"], rec["engenhao"]["bota"]),
    ]
    for metric_id, val_f, val_b in mapping:
        m = get_metric(dados, metric_id)
        if m:
            m["flu"] = val_f
            m["bota"] = val_b


def update_classico_bucket(bucket, gf, gb):
    bucket["jogos"] += 1
    bucket["gf"] += gf
    bucket["gb"] += gb
    if gf > gb:
        bucket["flu"] += 1
    elif gb > gf:
        bucket["bota"] += 1
    else:
        bucket["emp"] += 1


def process_team_fixture(dados, fixture, club_key, team_id):
    league_id = fixture["league"]["id"]
    home_id = fixture["teams"]["home"]["id"]
    away_id = fixture["teams"]["away"]["id"]

    # Gols no tempo regulamentar + prorrogação (sem disputa de pênaltis)
    goals_home = fixture["goals"]["home"]
    goals_away = fixture["goals"]["away"]
    if goals_home is None or goals_away is None:
        return

    is_home = home_id == team_id
    gf = goals_home if is_home else goals_away
    gc = goals_away if is_home else goals_home
    won = gf > gc
    drew = gf == gc
    lost = gf < gc

    # 1. Brasileirão Série A
    if league_id == LEAGUE_BRASILEIRAO:
        pc = dados["pontos_corridos"][club_key]
        pts_add = 3 if won else (1 if drew else 0)
        pc["j"] += 1
        pc["v"] += 1 if won else 0
        pc["e"] += 1 if drew else 0
        pc["d"] += 1 if lost else 0
        pc["pts"] += pts_add
        pc["gp"] += gf
        pc["gc"] += gc

        m_71 = get_metric(dados, "pts_unif71")
        if m_71:
            m_71[club_key] += pts_add

    # 2. Libertadores
    elif league_id == LEAGUE_LIBERTADORES:
        for mid, inc in [
            ("lib_jog", 1),
            ("lib_vit", 1 if won else 0),
            ("lib_fora", 1 if (won and not is_home) else 0),
            ("lib_gols", gf),
        ]:
            m = get_metric(dados, mid)
            if m and inc:
                m[club_key] += inc

    # 3. Copa do Brasil
    elif league_id == LEAGUE_COPA_BRASIL:
        for mid, inc in [
            ("cdb_jog", 1),
            ("cdb_vit", 1 if won else 0),
            ("cdb_gols", gf),
        ]:
            m = get_metric(dados, mid)
            if m and inc:
                m[club_key] += inc


def process_classico(dados, fixture):
    home_id = fixture["teams"]["home"]["id"]
    goals_home = fixture["goals"]["home"]
    goals_away = fixture["goals"]["away"]
    if goals_home is None or goals_away is None:
        return

    gf = goals_home if home_id == TEAM_FLU else goals_away
    gb = goals_away if home_id == TEAM_FLU else goals_home

    rec = dados["classico_recortes"]
    update_classico_bucket(rec["geral"], gf, gb)

    league_id = fixture["league"]["id"]
    league_name = (fixture["league"].get("name") or "").lower()
    venue_name = ((fixture["fixture"].get("venue") or {}).get("name") or "").lower()

    if league_id == LEAGUE_BRASILEIRAO:
        update_classico_bucket(rec["brasileirao"], gf, gb)
    elif league_id in LEAGUE_CARIOCA_IDS or "carioca" in league_name:
        update_classico_bucket(rec["carioca"], gf, gb)

    if "maracan" in venue_name:
        update_classico_bucket(rec["maracana"], gf, gb)
    elif "nilton santos" in venue_name or "engenh" in venue_name:
        update_classico_bucket(rec["engenhao"], gf, gb)


def main():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        dados = json.load(f)

    last_iso = dados.get("ultimo_jogo_processado_iso", "2026-11-26T12:00:00-03:00")
    last_dt = parse_iso(last_iso)
    processed = set(dados.get("ids_processados", []))

    now_utc = datetime.now(timezone.utc)
    seasons = sorted({last_dt.year, now_utc.year})

    all_fixtures = {}
    for team_id in (TEAM_FLU, TEAM_BOTA):
        for season in seasons:
            res = api_get("fixtures", {"team": team_id, "season": season})
            for fx in res.get("response", []):
                fx_id = fx["fixture"]["id"]
                status = fx["fixture"]["status"]["short"]
                fx_dt = parse_iso(fx["fixture"]["date"])
                if status in FINISHED_STATUSES and fx_dt > last_dt and fx_id not in processed:
                    all_fixtures[fx_id] = fx

    if not all_fixtures:
        print("Nenhum jogo novo finalizado desde a última atualização.")
        return

    sorted_fixtures = sorted(
        all_fixtures.values(), key=lambda x: parse_iso(x["fixture"]["date"])
    )

    max_dt = last_dt
    for fx in sorted_fixtures:
        fx_id = fx["fixture"]["id"]
        fx_dt = parse_iso(fx["fixture"]["date"])
        home_id = fx["teams"]["home"]["id"]
        away_id = fx["teams"]["away"]["id"]

        teams_in_match = {home_id, away_id}
        if TEAM_FLU in teams_in_match:
            process_team_fixture(dados, fx, "flu", TEAM_FLU)
        if TEAM_BOTA in teams_in_match:
            process_team_fixture(dados, fx, "bota", TEAM_BOTA)
        if TEAM_FLU in teams_in_match and TEAM_BOTA in teams_in_match:
            process_classico(dados, fx)

        processed.add(fx_id)
        if fx_dt > max_dt:
            max_dt = fx_dt
        print(
            f'Processado [{fx_id}] {fx["league"]["name"]}: '
            f'{fx["teams"]["home"]["name"]} {fx["goals"]["home"]} x {fx["goals"]["away"]} {fx["teams"]["away"]["name"]}'
        )

    sync_pontos_corridos_cards(dados)
    sync_cdb_pct(dados)
    sync_classico_cards(dados)

    dados["ids_processados"] = sorted(processed)
    dados["ultimo_jogo_processado_iso"] = max_dt.isoformat()
    dados["ultima_atualizacao"] = datetime.now().strftime("%d/%m/%Y")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    print(f"Atualização concluída! {len(sorted_fixtures)} jogo(s) somado(s).")


if __name__ == "__main__":
    main()
