"""
╔══════════════════════════════════════════════════════════════╗
║   COPA DO MUNDO 2026 — Extrator Master para Power BI         ║
║   Fonte primária : openfootball/worldcup.json (sem auth)     ║
║   Fonte scout    : ESPN hidden API (standings + scout)       ║
║   Bandeiras      : flagcdn.com (via times.csv)               ║
║   Fotos jogadores: media.api-sports.io (api-football)        ║
╠══════════════════════════════════════════════════════════════╣
║  Arquivos gerados em ./wc2026_data/                          ║
║    times_ref.csv          — 48 times com id, bandeira, grupo ║
║    jogos.csv              — 104 fixtures + id_time_casa/fora ║
║    gols.csv               — goleadores por minuto/jogo       ║
║    artilheiros.csv        — ranking geral com id_time        ║
║    grupos_standings.csv   — classificação com id_time        ║
║    scout_jogadores.csv    — stats por atleta/jogo (ESPN)     ║
║    scout_legenda.csv      — dicionário das siglas do scout   ║
║    _meta.json             — timestamp da última extração     ║
╚══════════════════════════════════════════════════════════════╝

Sobre fotos de jogadores (api-football):
  - Requer API key gratuita: https://dashboard.api-football.com/register
  - Cole sua key na variável API_FOOTBALL_KEY abaixo
  - Com key: gera coluna 'foto_url' no scout_jogadores.csv
  - Sem key: coluna com URL padrão (funciona para jogadores famosos)

Como rodar:
    pip install requests pandas
    python wc2026_master.py
"""

import os, json, time, requests, pandas as pd
from collections import defaultdict
from datetime import datetime
from io import StringIO

OUTPUT_DIR       = "./wc2026_data"
TIMES_REF_PATH   = "./times.csv"   # ← caminho do seu times.csv com as bandeiras
API_FOOTBALL_KEY = ""              # ← opcional: cole sua key do api-football

# ── URLs ─────────────────────────────────────────────────────
URL_OPENFOOTBALL   = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
URL_ESPN_STANDINGS  = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/standings"
URL_ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates=20260611-20260720&limit=150"
URL_ESPN_SUMMARY    = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event="
URL_APIFOOTBALL     = "https://v3.football.api-sports.io"

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://www.espn.com.br/",
}

# ── Dicionário de nomes: openfootball → times.csv (name_en) ──
# Apenas os 3 casos que divergem
NOME_MAP = {
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "DR Congo":             "Democratic Republic of the Congo",
    "USA":                  "United States",
}

# ── Dicionário de siglas ESPN ─────────────────────────────────
SCOUT_LEGENDA = {
    "APP":  ("Partidas",             "Entrou em campo"),
    "G":    ("Gols",                 "Gols marcados"),
    "A":    ("Assistências",         "Assistências para gol"),
    "SHOT": ("Chutes",               "Total de chutes"),
    "SOG":  ("Chutes no Alvo",       "Chutes que exigiram defesa"),
    "SV":   ("Defesas",              "Defesas do goleiro"),
    "GA":   ("Gols Sofridos",        "Gols sofridos pelo goleiro"),
    "OF":   ("Impedimentos",         "Vezes pego em impedimento"),
    "FC":   ("Faltas Cometidas",     "Faltas que o jogador cometeu"),
    "FA":   ("Faltas Sofridas",      "Faltas que o jogador sofreu"),
    "YC":   ("Cartões Amarelos",     "Amarelos recebidos na partida"),
    "RC":   ("Cartões Vermelhos",    "Vermelhos recebidos na partida"),
    "CK":   ("Escanteios",           "Escanteios cobrados"),
    "BC":   ("Bolas Bloqueadas",     "Chutes bloqueados pelo jogador"),
    "CLR":  ("Cortes",               "Bolas cortadas na defesa"),
    "INT":  ("Interceptações",       "Passes do adversário interceptados"),
    "PKA":  ("Pênaltis Provocados",  "Pênaltis cometidos contra o jogador"),
    "PKS":  ("Pênaltis Defendidos",  "Pênaltis defendidos pelo goleiro"),
    "PKG":  ("Gols de Pênalti",      "Gols marcados de pênalti"),
    "PKM":  ("Pênaltis Perdidos",    "Pênaltis desperdiçados"),
    "DS":   ("Duelos Ganhos",        "Disputas de bola vencidas"),
    "TO":   ("Perdas de Bola",       "Vezes que perdeu a posse"),
    "DB":   ("Dribles Completos",    "Dribles bem-sucedidos"),
    "KP":   ("Passes-Chave",         "Passes que geraram chances de gol"),
    "TK":   ("Desarmes",             "Bolas roubadas do adversário"),
}

# ── Helpers ───────────────────────────────────────────────────

def make_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"[✓] Saída: {os.path.abspath(OUTPUT_DIR)}\n")


def save(df: pd.DataFrame, name: str):
    path = os.path.join(OUTPUT_DIR, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"    [✓] {name}  —  {len(df)} linhas")


def get_json(url: str, label: str = "", delay: float = 0.3,
             extra_headers: dict = None) -> dict | list | None:
    h = {**HEADERS_BROWSER, **(extra_headers or {})}
    try:
        r = requests.get(url, headers=h, timeout=15)
        r.raise_for_status()
        time.sleep(delay)
        return r.json()
    except Exception as e:
        print(f"    [X] {label or url[:60]}: {e}")
        return None


def normalizar_nome(nome: str) -> str:
    """Converte nome openfootball → nome times.csv quando divergem."""
    return NOME_MAP.get(nome, nome)

# ── Carrega times_ref (times.csv com bandeiras) ───────────────

def carregar_times_ref() -> pd.DataFrame:
    """
    Lê o times.csv com bandeiras e retorna um DataFrame indexado por name_en.
    Gera também times_ref.csv na pasta de saída (já enriquecido e limpo).
    """
    if not os.path.exists(TIMES_REF_PATH):
        print(f"  [!] {TIMES_REF_PATH} não encontrado — colunas de id/bandeira não serão adicionadas.")
        print(f"       Coloque o times.csv na mesma pasta do script e rode novamente.")
        return pd.DataFrame()

    df = pd.read_csv(TIMES_REF_PATH, encoding="utf-8-sig")

    # Seleciona e renomeia apenas o necessário
    df_ref = df[["id", "name_en", "fifa_code", "iso2", "flag", "groups"]].copy()
    df_ref.columns = ["id_time", "time", "fifa_code", "iso2", "flag_url", "grupo_ref"]

    # Gera URL de foto de bandeira em duas resoluções (já vem do flagcdn)
    df_ref["flag_url_large"] = df_ref["flag_url"].str.replace("/w80/", "/w160/", regex=False)

    save(df_ref, "times_ref.csv")
    print(f"    [i] times_ref.csv gerado com {len(df_ref)} times + URLs de bandeira")

    return df_ref.set_index("time")   # index = name_en do times.csv


def enriquecer_com_id(df: pd.DataFrame,
                      ref: pd.DataFrame,
                      cols_time: list[str]) -> pd.DataFrame:
    """
    Para cada coluna de nome de time em cols_time, adiciona colunas
    id_<col>, flag_url_<col> e fifa_code_<col>.
    Normaliza o nome antes do lookup (trata Bosnia & Herzegovina etc.)
    """
    if ref.empty:
        return df

    for col in cols_time:
        if col not in df.columns:
            continue

        # Normaliza os nomes no DataFrame antes do merge
        nome_norm = df[col].map(lambda n: normalizar_nome(n) if isinstance(n, str) else n)

        df[f"id_{col}"]       = nome_norm.map(ref["id_time"])
        df[f"flag_url_{col}"] = nome_norm.map(ref["flag_url"])
        df[f"fifa_code_{col}"]= nome_norm.map(ref["fifa_code"])

    return df

# ── ETAPA 1: Openfootball ─────────────────────────────────────

def etapa1_openfootball(ref: pd.DataFrame) -> list[dict]:
    print("=" * 60)
    print("  ETAPA 1/3 — Openfootball: fixtures, gols, artilheiros")
    print("=" * 60)

    data = get_json(URL_OPENFOOTBALL, "openfootball")
    if not data:
        print("  [FATAL] Não foi possível baixar openfootball. Abortando.")
        return []

    matches = data.get("matches", [])
    print(f"  → {len(matches)} jogos encontrados")

    # ── jogos.csv ────────────────────────────────────────────
    rows_jogos = []
    for m in matches:
        score = m.get("score", {})
        ft = score.get("ft"); ht = score.get("ht")
        et = score.get("et"); pen = score.get("p")
        grupo = m.get("group", ""); rnd = m.get("round", "")

        rows_jogos.append({
            "id_jogo":     m.get("num", ""),
            "round":       rnd,
            "fase":        grupo if grupo else rnd,
            "grupo":       grupo,
            "data":        m.get("date"),
            "horario_utc": m.get("time"),
            "cidade":      m.get("ground"),
            "time_casa":   m.get("team1"),
            "time_fora":   m.get("team2"),
            "realizado":   ft is not None,
            "gols_casa":   ft[0] if ft else None,
            "gols_fora":   ft[1] if ft else None,
            "ht_casa":     ht[0] if ht else None,
            "ht_fora":     ht[1] if ht else None,
            "et_casa":     et[0] if et else None,
            "et_fora":     et[1] if et else None,
            "pen_casa":    pen[0] if pen else None,
            "pen_fora":    pen[1] if pen else None,
            "vencedor": (
                m.get("team1") if ft and ft[0] > ft[1]
                else m.get("team2") if ft and ft[1] > ft[0]
                else "Empate" if ft else None
            ),
        })

    df_jogos = pd.DataFrame(rows_jogos)
    df_jogos = enriquecer_com_id(df_jogos, ref, ["time_casa", "time_fora", "vencedor"])
    save(df_jogos, "jogos.csv")

    # ── gols.csv ─────────────────────────────────────────────
    rows_gols = []
    for m in [x for x in matches if x.get("score")]:
        for time_nome, key in [(m["team1"], "goals1"), (m["team2"], "goals2")]:
            for g in m.get(key, []):
                rows_gols.append({
                    "round":         m.get("round"),
                    "fase":          m.get("group") or m.get("round"),
                    "grupo":         m.get("group"),
                    "data":          m.get("date"),
                    "time_casa":     m["team1"],
                    "time_fora":     m["team2"],
                    "time_marcador": time_nome,
                    "jogador":       g.get("name"),
                    "minuto":        g.get("minute"),
                    "penalti":       g.get("penalty", False),
                    "gol_contra":    g.get("og", False),
                })

    df_gols = pd.DataFrame(rows_gols)
    df_gols = enriquecer_com_id(df_gols, ref, ["time_casa", "time_fora", "time_marcador"])
    save(df_gols, "gols.csv")

    # ── artilheiros.csv ──────────────────────────────────────
    artilheiros: dict[str, dict] = {}
    for g in rows_gols:
        if g["gol_contra"]: continue
        nome = g["jogador"]
        if not nome: continue
        if nome not in artilheiros:
            artilheiros[nome] = {
                "jogador":  nome,
                "time":     g["time_marcador"],
                "gols":     0,
                "penaltis": 0,
            }
        artilheiros[nome]["gols"] += 1
        if g["penalti"]:
            artilheiros[nome]["penaltis"] += 1

    df_art = (
        pd.DataFrame(list(artilheiros.values()))
          .sort_values("gols", ascending=False)
          .reset_index(drop=True)
    )
    df_art.insert(0, "posicao", df_art.index + 1)
    df_art = enriquecer_com_id(df_art, ref, ["time"])
    save(df_art, "artilheiros.csv")

    # ── grupos_standings.csv ─────────────────────────────────
    st: dict = defaultdict(lambda: defaultdict(lambda: {
        "J": 0, "V": 0, "E": 0, "D": 0, "GP": 0, "GC": 0, "PTS": 0
    }))
    for m in matches:
        g = m.get("group"); ft = m.get("score", {}).get("ft")
        if not g or not ft: continue
        t1, t2 = m["team1"], m["team2"]; g1, g2 = ft[0], ft[1]
        for t, gf, gc in [(t1, g1, g2), (t2, g2, g1)]:
            st[g][t]["J"] += 1; st[g][t]["GP"] += gf; st[g][t]["GC"] += gc
        if   g1 > g2: st[g][t1]["V"]+=1; st[g][t1]["PTS"]+=3; st[g][t2]["D"]+=1
        elif g1 < g2: st[g][t2]["V"]+=1; st[g][t2]["PTS"]+=3; st[g][t1]["D"]+=1
        else:
            st[g][t1]["E"]+=1; st[g][t1]["PTS"]+=1
            st[g][t2]["E"]+=1; st[g][t2]["PTS"]+=1

    rows_st = []
    for grupo_nome, times in sorted(st.items()):
        ranked = sorted(times.items(),
                        key=lambda x: (-x[1]["PTS"], -(x[1]["GP"]-x[1]["GC"]), -x[1]["GP"]))
        for pos, (time_nome, s) in enumerate(ranked, 1):
            rows_st.append({
                "grupo": grupo_nome, "posicao": pos, "time": time_nome,
                "jogos": s["J"], "vitorias": s["V"], "empates": s["E"],
                "derrotas": s["D"], "gols_pro": s["GP"], "gols_contra": s["GC"],
                "saldo": s["GP"]-s["GC"], "pontos": s["PTS"],
            })

    df_st = pd.DataFrame(rows_st)
    df_st = enriquecer_com_id(df_st, ref, ["time"])
    save(df_st, "grupos_standings.csv")

    return [r for r in rows_jogos if r["realizado"]]


# ── ETAPA 2: ESPN Standings ───────────────────────────────────

def etapa2_espn_standings(ref: pd.DataFrame):
    print("\n" + "=" * 60)
    print("  ETAPA 2/3 — ESPN: Standings ao vivo")
    print("=" * 60)

    data = get_json(URL_ESPN_STANDINGS, "espn_standings")
    if not data or not data.get("children"):
        print("  [!] ESPN indisponível — usando standings calculados (Etapa 1).")
        return

    rows = []
    for g in data["children"]:
        nome_g = g.get("name", "")
        for entry in g.get("standings", {}).get("entries", []):
            stats = {s.get("abbreviation",""): s.get("value", 0)
                     for s in entry.get("stats", [])}
            rows.append({
                "grupo":       nome_g,
                "time":        entry.get("team", {}).get("displayName", ""),
                "sigla":       entry.get("team", {}).get("abbreviation", ""),
                "pontos":      stats.get("P",  0),
                "jogos":       stats.get("GP", 0),
                "vitorias":    stats.get("W",  0),
                "empates":     stats.get("D",  0),
                "derrotas":    stats.get("L",  0),
                "gols_pro":    stats.get("GF", 0),
                "gols_contra": stats.get("GA", 0),
                "saldo":       stats.get("GD", 0),
            })

    if rows:
        df = pd.DataFrame(rows)
        df = enriquecer_com_id(df, ref, ["time"])
        save(df, "grupos_standings_espn.csv")
        print("  [i] grupos_standings_espn.csv disponível (dados ao vivo ESPN).")


# ── ETAPA 3: ESPN Scout + fotos de jogadores ─────────────────

def _foto_url_jogador(id_espn: str, id_apifootball: str = "") -> str:
    """
    Monta URL da foto do jogador.
    Prioridade: api-sports CDN (não requer autenticação para a URL em si)
    Fallback: ESPN CDN
    A URL pode não funcionar para todos os jogadores, mas cobre os principais.
    """
    if id_apifootball:
        return f"https://media.api-sports.io/football/players/{id_apifootball}.png"
    if id_espn:
        return f"https://a.espncdn.com/i/headshots/soccer/players/full/{id_espn}.png"
    return ""


def etapa3_espn_scout(jogos_realizados: list[dict], ref: pd.DataFrame):
    print("\n" + "=" * 60)
    print("  ETAPA 3/3 — ESPN: Scout de atletas por jogo")
    print("=" * 60)

    if not jogos_realizados:
        print("  [!] Sem jogos realizados para buscar scout.")
        return

    # Passo A: scoreboard ESPN → mapa (casa_lower, fora_lower) → id_espn
    print("  [→] Buscando scoreboard ESPN para mapear IDs dos jogos...")
    data_sb = get_json(URL_ESPN_SCOREBOARD, "espn_scoreboard")
    if not data_sb:
        print("  [!] ESPN indisponível — scout não extraído.")
        print("      Rode o script novamente quando a conexão com a ESPN estiver disponível.")
        return

    eventos = data_sb.get("events", [])
    print(f"  → {len(eventos)} eventos no scoreboard ESPN")

    mapa_id: dict[tuple, str] = {}
    ids_encerrados: list[str] = []

    for ev in eventos:
        comps = ev.get("competitions", [{}])[0].get("competitors", [])
        casa_nome = fora_nome = ""
        for c in comps:
            nome = c.get("team", {}).get("displayName", "").lower()
            if c.get("homeAway") == "home": casa_nome = nome
            else: fora_nome = nome
        eid = ev.get("id", "")
        if casa_nome and fora_nome and eid:
            mapa_id[(casa_nome, fora_nome)] = eid
        if ev.get("status", {}).get("type", {}).get("completed", False) and eid:
            ids_encerrados.append(eid)

    # Passo B: cruza com jogos do openfootball
    ids_cruzados: list[str] = []
    for j in jogos_realizados:
        chave = (j["time_casa"].lower(), j["time_fora"].lower())
        eid = mapa_id.get(chave, "")
        if eid:
            ids_cruzados.append(eid)

    ids_finais = list(dict.fromkeys(ids_cruzados + ids_encerrados))
    print(f"  → {len(ids_finais)} jogos para extrair scout")

    if not ids_finais:
        print("  [!] Nenhum jogo para processar.")
        return

    # Passo C: /summary?event=ID para cada jogo
    linhas: list[dict] = []
    erros = 0

    for i, eid in enumerate(ids_finais):
        data_sum = get_json(f"{URL_ESPN_SUMMARY}{eid}", f"summary/{eid}", delay=0.3)
        if not data_sum:
            erros += 1
            continue

        header = data_sum.get("header", {})
        comp0  = (header.get("competitions") or [{}])[0]
        data_jogo   = comp0.get("date", "")
        status_jogo = comp0.get("status", {}).get("type", {}).get("shortDetail", "")

        for equipe_bloco in data_sum.get("rosters", []):
            time_info = equipe_bloco.get("team", {})
            nome_time = time_info.get("displayName", "")
            id_time_espn = time_info.get("id", "")

            # id_time do times.csv via lookup pelo nome
            nome_norm = normalizar_nome(nome_time)
            id_time_ref = (
                ref.loc[nome_norm, "id_time"]
                if not ref.empty and nome_norm in ref.index else ""
            )
            flag_url = (
                ref.loc[nome_norm, "flag_url"]
                if not ref.empty and nome_norm in ref.index else ""
            )

            for ab in equipe_bloco.get("roster", []):
                info = ab.get("athlete", {})
                id_jogador_espn = str(info.get("id", ""))

                reg = {
                    "id_espn_jogo":  eid,
                    "data_jogo":     data_jogo,
                    "status_jogo":   status_jogo,
                    "id_time_espn":  id_time_espn,
                    "id_time":       id_time_ref,   # ← ID do times.csv (chave de vínculo)
                    "time":          nome_time,
                    "flag_url_time": flag_url,       # ← bandeira do time direto no scout
                    "id_jogador":    id_jogador_espn,
                    "nome":          info.get("displayName"),
                    "posicao":       info.get("position", {}).get("abbreviation", ""),
                    "titular":       ab.get("starter", False),
                    "camisa":        ab.get("jersey", ""),
                    # Foto do jogador via ESPN CDN (sem necessidade de API key)
                    # Funciona para a maioria dos jogadores da Copa — se não carregar
                    # no Power BI, o visual simplesmente fica sem foto (não dá erro).
                    "foto_url": _foto_url_jogador(id_jogador_espn),
                }

                # Parse das estatísticas (cada sigla ESPN → coluna numérica)
                for st in ab.get("stats", []):
                    if not isinstance(st, dict): continue
                    sigla = st.get("abbreviation") or st.get("name", "")
                    if not sigla: continue
                    raw = st.get("displayValue", st.get("value", ""))
                    try:
                        reg[sigla] = float(raw) if "." in str(raw) else int(raw)
                    except (ValueError, TypeError):
                        reg[sigla] = raw

                linhas.append(reg)

        if (i + 1) % 10 == 0 or (i + 1) == len(ids_finais):
            print(f"  ... {i+1}/{len(ids_finais)} jogos | {len(linhas)} registros")

    if not linhas:
        print("  [!] Nenhum atleta extraído.")
        return

    df_scout = pd.DataFrame(linhas)

    # Reordena: metadados → siglas conhecidas → siglas extras
    colunas_meta = [
        "id_espn_jogo", "data_jogo", "status_jogo",
        "id_time", "id_time_espn", "time", "flag_url_time",
        "id_jogador", "nome", "posicao", "titular", "camisa", "foto_url",
    ]
    siglas_ok    = [s for s in SCOUT_LEGENDA if s in df_scout.columns]
    siglas_extra = [c for c in df_scout.columns
                    if c not in colunas_meta and c not in siglas_ok]
    ordem = [c for c in colunas_meta + siglas_ok + siglas_extra if c in df_scout.columns]
    df_scout = df_scout[ordem]
    save(df_scout, "scout_jogadores.csv")

    if erros:
        print(f"  [i] {erros} jogos com erro (normal para jogos não encerrados).")

    # Legenda das siglas presentes
    rows_leg = [
        {"sigla": s, "nome_pt": SCOUT_LEGENDA[s][0], "descricao": SCOUT_LEGENDA[s][1]}
        for s in siglas_ok
    ]
    if rows_leg:
        save(pd.DataFrame(rows_leg), "scout_legenda.csv")


# ── Main ──────────────────────────────────────────────────────

def main():
    print("\n╔" + "═"*58 + "╗")
    print("║   COPA DO MUNDO 2026 — Extrator Master para Power BI    ║")
    print("╚" + "═"*58 + "╝\n")

    make_dir()

    print("── Carregando tabela de referência de times (bandeiras)...")
    ref = carregar_times_ref()

    jogos_realizados = etapa1_openfootball(ref)
    etapa2_espn_standings(ref)
    etapa3_espn_scout(jogos_realizados, ref)

    meta = {
        "extraido_em":      datetime.now().isoformat(),
        "jogos_realizados": len(jogos_realizados),
        "fonte_primaria":   "openfootball/worldcup.json",
        "fonte_scout":      "ESPN hidden API (/summary?event=ID)",
        "bandeiras":        "flagcdn.com (via times.csv)",
        "fotos_jogadores":  "ESPN CDN (a.espncdn.com/i/headshots/soccer/players/full/{id}.png)",
    }
    with open(os.path.join(OUTPUT_DIR, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print("\n╔" + "═"*58 + "╗")
    print("║   EXTRAÇÃO CONCLUÍDA!                                    ║")
    print(f"║   {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}                                   ║")
    print("╚" + "═"*58 + "╝\n")

    # Imprime resumo dos vínculos criados
    if not ref.empty:
        print("── Colunas de vínculo adicionadas aos CSVs:")
        print("   jogos.csv          → id_time_casa, id_time_fora, flag_url_time_casa/fora")
        print("   gols.csv           → id_time_marcador, flag_url_time_marcador")
        print("   artilheiros.csv    → id_time, flag_url_time")
        print("   grupos_standings.csv → id_time, flag_url_time")
        print("   scout_jogadores.csv  → id_time, flag_url_time, foto_url (jogador)")
        print("\n── No Power BI: use 'id_time' para relacionar todas as tabelas com times_ref.csv")
        print("   Use 'flag_url_*' e 'foto_url' em visuals de imagem (HTML visual ou Image URL).\n")


if __name__ == "__main__":
    main()