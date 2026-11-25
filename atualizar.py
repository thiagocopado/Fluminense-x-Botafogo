import json
import os
from datetime import datetime, timezone
import requests

API_KEY = os.environ.get("FOOTBALL_API_KEY")
BASE_URL = "https://v3.football.api-sports.io"

# IDs oficiais na API-Football
FLU_ID = 124
BOTA_ID = 120

LEAGUE_BRASILEIRAO = 71
LEAGUE_COPA_BRASIL = 73
LEAGUE_LIBERTADORES = 13
LEAGUE_CARIOCA = 624

FINISHED_STATUSES = {"FT", "AET", "PEN"}


def buscar_jogos_time(team_id: int, season: int, from_date: str, to_date: str) -> list:
    headers = {"x-apisports-key": API_KEY}
    params = {
        "team": team_id,
        "season": season,
        "from": from_date,
        "to": to_date,
    }
    resp = requests.get(f"{BASE_URL}/fixtures", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("response", [])


def get_metric(metrics: list, metric_id: str) -> dict | None:
    for m in metrics:
        if m["id"] == metric_id:
            return m
    return None


def processar_jogo_time(dados: dict, fixture: dict, team_id: int, team_key: str):
    """Atualiza estatísticas de Brasileirão, Libertadores e Copa do Brasil para um clube."""
    league_id = fixture["league"]["id"]
    home_id = fixture["teams"]["home"]["id"]
    away_id = fixture["teams"]["away"]["id"]
    goals_home = fixture["goals"]["home"] or 0
    goals_away = fixture["goals"]["away"] or 0

    is_home = home_id == team_id
    gols_pro = goals_home if is_home else goals_away
    gols_contra = goals_away if is_home else goals_home
    venceu = gols_pro > gols_contra
    empatou = gols_pro == gols_contra
    pontos = 3 if venceu else (1 if empatou else 0)

    metrics = dados["metrics"]

    if league_id == LEAGUE_BRASILEIRAO:
        for mid in ("pts_pc", "pts_unif71"):
            m = get_metric(metrics, mid)
            if m:
                m[team_key] += pontos
        gp = get_metric(metrics, "gp_pc")
        if gp:
            gp[team_key] += gols_pro

    elif league_id == LEAGUE_LIBERTADORES:
        m_jog = get_metric(metrics, "lib_jog")
        m_vit = get_metric(metrics, "lib_vit")
        m_fora = get_metric(metrics, "lib_fora")
        m_gols = get_metric(metrics, "lib_gols")
        if m_jog:
            m_jog[team_key] += 1
        if m_vit and venceu:
            m_vit[team_key] += 1
        if m_fora and venceu and not is_home:
            m_fora[team_key] += 1
        if m_gols:
            m_gols[team_key] += gols_pro

    elif league_id == LEAGUE_COPA_BRASIL:
        m_jog = get_metric(metrics, "cdb_jog")
        m_vit = get_metric(metrics, "cdb_vit")
        m_gols = get_metric(metrics, "cdb_gols")
        if m_jog:
            m_jog[team_key] += 1
        if m_vit and venceu:
            m_vit[team_key] += 1
        if m_gols:
            m_gols[team_key] += gols_pro


def processar_classico(dados: dict, fixture: dict):
    """Atualiza o retrospecto do Clássico Vovô (geral, competição e estádio)."""
    league_id = fixture["league"]["id"]
    venue = (fixture["fixture"].get("venue", {}).get("name") or "").lower()
    home_id = fixture["teams"]["home"]["id"]
    goals_home = fixture["goals"]["home"] or 0
    goals_away = fixture["goals"]["away"] or 0

    g_flu = goals_home if home_id == FLU_ID else goals_away
    g_bota = goals_away if home_id == FLU_ID else goals_home

    flu_win = g_flu > g_bota
    bota_win = g_bota > g_flu
    empate = g_flu == g_bota

    recortes_para_atualizar = ["geral"]
    if league_id == LEAGUE_CARIOCA:
        recortes_para_atualizar.append("carioca")
    elif league_id == LEAGUE_BRASILEIRAO:
        recortes_para_atualizar.append("brasileirao")

    if "maracan" in venue:
        recortes_para_atualizar.append("maracana")
    elif "nilton santos" in venue or "engenh" in venue:
        recortes_para_atualizar.append("engenhao")

    for rec in recortes_para_atualizar:
        r = dados["classico_recortes"][rec]
        r["jogos"] += 1
        r["gf"] += g_flu
        r["gb"] += g_bota
        if flu_win:
            r["flu"] += 1
        elif bota_win:
            r["bota"] += 1
        elif empate:
            r["emp"] += 1

    # Sincroniza os indicadores da tabela principal
    metrics = dados["metrics"]
    mapa = {
        "cl_vit": ("geral", "flu", "bota"),
        "cl_gols": ("geral", "gf", "gb"),
        "cl_car": ("carioca", "flu", "bota"),
        "cl_br": ("brasileirao", "flu", "bota"),
        "cl_mar": ("maracana", "flu", "bota"),
        "cl_eng": ("engenhao", "flu", "bota"),
    }
    for mid, (rec, kf, kb) in mapa.items():
        m = get_metric(metrics, mid)
        if m:
            m["flu"] = dados["classico_recortes"][rec][kf]
            m["bota"] = dados["classico_recortes"][rec][kb]


def main():
    if not API_KEY:
        raise RuntimeError("Variável de ambiente FOOTBALL_API_KEY não configurada.")

    with open("dados.json", "r", encoding="utf-8") as f:
        dados = json.load(f)

    ultimo_iso = dados.get("ultimo_jogo_processado_iso", "2026-08-19T23:59:59+00:00")
    ultimo_dt = datetime.fromisoformat(ultimo_iso.replace("Z", "+00:00"))
    hoje = datetime.now(timezone.utc)

    from_date = ultimo_dt.strftime("%Y-%m-%d")
    to_date = hoje.strftime("%Y-%m-%d")
    season = hoje.year

    ids_processados = set(dados.get("ids_processados", []))
    jogos_unicos = {}

    for tid in (FLU_ID, BOTA_ID):
        for fx in buscar_jogos_time(tid, season, from_date, to_date):
            fid = fx["fixture"]["id"]
            status = fx["fixture"]["status"]["short"]
            fx_dt = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))

            if status not in FINISHED_STATUSES:
                continue
            if fid in ids_processados or fx_dt <= ultimo_dt:
                continue
            jogos_unicos[fid] = (fx_dt, fx)

    if not jogos_unicos:
        print("Nenhum jogo novo finalizado desde", ultimo_iso)
        return

    novos_ordenados = sorted(jogos_unicos.values(), key=lambda x: x[0])
    maior_dt = ultimo_dt

    for fx_dt, fx in novos_ordenados:
        fid = fx["fixture"]["id"]
        home_id = fx["teams"]["home"]["id"]
        away_id = fx["teams"]["away"]["id"]

        if FLU_ID in (home_id, away_id):
            processar_jogo_time(dados, fx, FLU_ID, "flu")
        if BOTA_ID in (home_id, away_id):
            processar_jogo_time(dados, fx, BOTA_ID, "bota")
        if {home_id, away_id} == {FLU_ID, BOTA_ID}:
            processar_classico(dados, fx)

        ids_processados.add(fid)
        if fx_dt > maior_dt:
            maior_dt = fx_dt

    dados["ids_processados"] = sorted(ids_processados)
    dados["ultimo_jogo_processado_iso"] = maior_dt.isoformat()
    dados["ultima_atualizacao"] = hoje.strftime("%d/%m/%Y")

    with open("dados.json", "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

    print(f"Atualização concluída! {len(novos_ordenados)} jogo(s) processado(s).")


if __name__ == "__main__":
    main()
