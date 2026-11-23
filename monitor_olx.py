import html
import json
import os
import re
import time
import requests
from google import genai
from google.genai import types

ARQUIVO_VISTOS = "vistos_olx.json"

PRECO_MINIMO = 150.0
PRECO_MAXIMO = 4500.0

FONTES_OLX = [
    {
        "url": "https://sp.olx.com.br/sao-paulo-e-regiao/informatica/notebooks?sf=1",
        "tipo": "NOTEBOOK",
        "modalidade": "SP / DDD 11 (Presencial)"
    },
    {
        "url": "https://www.olx.com.br/informatica/notebooks?sd=1&sf=1",
        "tipo": "NOTEBOOK",
        "modalidade": "Brasil com Entrega OLX"
    },
    {
        "url": "https://sp.olx.com.br/sao-paulo-e-regiao/games/consoles-de-video-game?sf=1",
        "tipo": "GAME",
        "modalidade": "SP / DDD 11 (Presencial)"
    },
    {
        "url": "https://www.olx.com.br/games/consoles-de-video-game?sd=1&sf=1",
        "tipo": "GAME",
        "modalidade": "Brasil com Entrega OLX"
    }
]

TERMOS_GPU = [
    "nvidia", "geforce", "gtx", "rtx", "amd", "radeon", "vega",
    "placa de video", "placa de vídeo", "dedicada", "gpu", "mx150", "mx250", "mx330", "mx350", "mx450"
]

TERMOS_DEFEITO = [
    "defeito", "quebrada", "quebrado", "trincada", "trincado", "avaria",
    "não liga", "nao liga", "sem imagem", "não dá vídeo", "nao da video",
    "conserto", "reparo", "manutenção", "manutencao", "técnico", "tecnico",
    "peças", "pecas", "retirar", "tela ruim", "display", "dobradiça", "dobradica",
    "carcaça", "carcaca", "bateria viciada", "não carrega", "nao carrega", "teclado falhando"
]

TERMOS_GAMES_ATUAIS = [
    "ps5", "playstation 5", "ps4", "playstation 4",
    "xbox series", "series s", "series x",
    "nintendo switch", "switch oled", "switch lite", "steam deck"
]

PALAVRAS_BLOQUEIO = [
    "caixa vazia", "cabo avulso", "capinha", "película", "pelicula",
    "adesivo", "skin", "suporte parede", "aluguel", "procuro", "compro",
    "conta digital", "midia digital", "mídia digital", "jogo avulso",
    "ps3", "ps2", "xbox 360", "xbox one", "wii", "3ds", "psp",
    "positivo", "multilaser", "celeron", "pentium", "atom"
]

def carregar_vistos():
    if os.path.exists(ARQUIVO_VISTOS):
        try:
            with open(ARQUIVO_VISTOS, "r", encoding="utf-8") as f:
                conteudo = f.read().strip()
                return set(json.loads(conteudo)) if conteudo else set()
        except Exception:
            return set()
    return set()

def salvar_vistos(vistos):
    vistos_lista = list(vistos)[-8000:]
    with open(ARQUIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(vistos_lista, f, indent=2)

def ler_via_jina(url, timeout=25, tentativas=2):
    for tentativa in range(tentativas):
        try:
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=timeout)
            if resp.status_code == 200 and len(resp.text) > 2000:
                return resp.text
            if tentativa < tentativas - 1:
                time.sleep(3)
        except Exception as e:
            print(f"  [Erro Jina] {url}: {e}")
            time.sleep(2)
    return ""

def extrair_anuncios_categoria(texto, tipo, modalidade):
    resultados = []
    links_vistos = set()

    padrao_url = re.compile(r'https://[a-z]{2}\.olx\.com\.br/[^\s\)\]]+-\d{7,}')
    matches = list(padrao_url.finditer(texto))

    for i, m in enumerate(matches):
        link = m.group(0).split("?")[0].strip()
        if link in links_vistos:
            continue

        inicio_bloco = max(0, m.start() - 180)
        fim_bloco = matches[i + 1].start() if i + 1 < len(matches) else min(len(texto), m.end() + 450)
        bloco = texto[inicio_bloco:fim_bloco]

        m_preco = re.search(r'R\$\s*([\d\.]+)(?:,\d{2})?', bloco)
        if not m_preco:
            continue

        try:
            preco = float(m_preco.group(1).replace(".", ""))
        except ValueError:
            continue

        if not (PRECO_MINIMO <= preco <= PRECO_MAXIMO):
            continue

        slug = link.rstrip("/").split("/")[-1]
        titulo_limpo = re.sub(r'-\d+$', '', slug).replace("-", " ").title()

        if any(b in titulo_limpo.lower() for b in PALAVRAS_BLOQUEIO):
            continue

        if tipo == "GAME":
            if not any(g in titulo_limpo.lower() for g in TERMOS_GAMES_ATUAIS):
                continue

        m_local = re.search(r'([A-Za-zÀ-ÿ\s-]+-\s*[A-Z]{2}|(?:São Paulo|Santo André|São Bernardo|São Caetano|Diadema|Mauá|Guarulhos|Osasco|Barueri)[^\n|]{0,40})', bloco)
        local = " ".join(m_local.group(1).split()) if m_local else modalidade

        links_vistos.add(link)
        resultados.append({
            "titulo": titulo_limpo[:160],
            "preco": preco,
            "local": local[:80],
            "tipo": tipo,
            "modalidade": modalidade,
            "link": link
        })

    return resultados

def coletar_por_categorias():
    candidatos = []
    links_unicos = set()

    for fonte in FONTES_OLX:
        url_base = fonte["url"]
        for pagina in [1, 2]:
            url = url_base if pagina == 1 else f"{url_base}&o={pagina}"
            print(f"[{fonte['tipo']} | {fonte['modalidade']}] Lendo página {pagina}...")
            texto = ler_via_jina(url, timeout=25)
            if not texto:
                print("  -> Sem resposta válida nesta página.")
                continue

            achados = extrair_anuncios_categoria(texto, fonte["tipo"], fonte["modalidade"])
            novos = 0
            for item in achados:
                if item["link"] not in links_unicos:
                    links_unicos.add(item["link"])
                    candidatos.append(item)
                    novos += 1
            print(f"  -> Texto lido: {len(texto)} chars | Anúncios extraídos: {novos}")
            time.sleep(1.0)

    return candidatos

def inspecionar_e_filtrar_descricao(item):
    texto = ler_via_jina(item["link"], timeout=22, tentativas=1)
    if not texto:
        return None

    m_h1 = re.search(r'^#\s+(.+)$', texto, flags=re.MULTILINE)
    if m_h1 and "olx" not in m_h1.group(1).lower():
        item["titulo"] = m_h1.group(1).strip()[:160]

    linhas = [
        " ".join(l.split()) for l in texto.splitlines()
        if len(l.strip()) > 3 and not any(
            x in l.lower() for x in [
                "cookie", "dicas de segurança", "compartilhar", "denunciar",
                "publicidade", "política de privacidade", "todos os direitos"
            ]
        )
    ]
    texto_completo = " | ".join(linhas)
    texto_lower = texto_completo.lower()

    if any(b in texto_lower for b in PALAVRAS_BLOQUEIO):
        return None

    if "Brasil com Entrega" in item["modalidade"]:
        if not any(e in texto_lower for e in ["entrega", "envio", "olx pay", "frete"]):
            return None

    if item["tipo"] == "NOTEBOOK":
        tem_gpu = any(g in texto_lower for g in TERMOS_GPU)
        tem_defeito = any(d in texto_lower for d in TERMOS_DEFEITO)
        if not (tem_gpu and tem_defeito):
            return None
    elif item["tipo"] == "GAME":
        if not any(g in texto_lower for g in TERMOS_GAMES_ATUAIS):
            return None

    item["descricao"] = texto_completo[:900]
    return item

def filtrar_com_ia(candidatos):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not candidatos:
        return [], False

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=65000)
    )

    modelos_prioritarios = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite"
    ]

    lista_texto = ""
    for i, item in enumerate(candidatos):
        lista_texto += (
            f"ID {i} | Tipo: {item['tipo']} | Modalidade: {item['modalidade']} | "
            f"Título: {item['titulo']} | Preço: R$ {item['preco']:.2f} | Local: {item['local']} | "
            f"Descrição: {item.get('descricao', '')}\n"
        )

    prompt = f"""
Você é um especialista em compra e revenda de hardware e games usados no Brasil (2026), analisando anúncios da OLX.

APROVE ESTRITAMENTE APENAS ESTES DOIS PERFIS:
1. NOTEBOOK (Tipo: NOTEBOOK): Precisa ter placa de vídeo NVIDIA (GTX/RTX/MX) ou AMD (Radeon/Ryzen Vega) E apresentar algum defeito na descrição (ex: tela quebrada, carcaça, teclado, bateria, problema de imagem/reparo) com preço atrativo que compense o conserto.
   - REJEITE notebooks sem defeito ou com CPU básica antiga (Celeron, Pentium, Dual Core, Positivo, Multilaser).
2. GAME ATUAL (Tipo: GAME): Precisa ser console completo (PS5, PS4, Xbox Series S/X, Nintendo Switch ou Steam Deck) com bom preço ou oportunidade de revenda.
   - REJEITE controles avulsos, jogos avulsos, caixas vazias ou consoles antigos (PS3, Xbox 360, Xbox One).

Anúncios pré-filtrados pela descrição:
{lista_texto}

Responda APENAS com um array JSON válido (se nenhum valer a pena, retorne []):
[
  {{
    "id": 0,
    "motivo": "GPU/Modelo e Defeito identificado na descrição (ou Console) | Média de mercado vs Preço anunciado e margem estimada"
  }}
]
"""
    for modelo in modelos_prioritarios:
        try:
            print(f"Analisando {len(candidatos)} anúncios pré-filtrados com IA ({modelo})...")
            res = client.models.generate_content(
                model=modelo,
                contents=prompt,
            )
            texto_limpo = res.text.strip().removeprefix("```json").removesuffix("```").strip()
            aprovados = json.loads(texto_limpo)

            finais = []
            for ap in aprovados:
                idx = ap.get("id")
                if isinstance(idx, int) and 0 <= idx < len(candidatos):
                    item = candidatos[idx]
                    item["motivo"] = ap.get("motivo", "Oportunidade identificada")
                    finais.append(item)
            return finais, True
        except Exception as e:
            print(f"Falha em {modelo}: {e}")
            time.sleep(3)

    return [], False

def enviar_telegram(achados):
    print(f"Enviando {len(achados)} oportunidades da OLX para o Telegram...")
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url_texto = f"https://api.telegram.org/bot{token}/sendMessage"

    for item in achados:
        titulo_esc = html.escape(item["titulo"])
        local_esc = html.escape(item["local"])
        mod_esc = html.escape(item["modalidade"])
        tipo_esc = html.escape(item["tipo"])
        motivo_esc = html.escape(item["motivo"])
        link_esc = html.escape(item["link"])
        preco_fmt = f"R$ {item['preco']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        mensagem = (
            f"🎯 <b>OLX - {tipo_esc} ({mod_esc})</b>\n\n"
            f"📦 <b>Anúncio:</b> {titulo_esc}\n"
            f"💰 <b>Preço:</b> {preco_fmt}\n"
            f"📍 <b>Local:</b> {local_esc}\n\n"
            f"💡 <b>Análise IA:</b>\n{motivo_esc}\n\n"
            f"🔗 {link_esc}"
        )

        for _ in range(3):
            try:
                resp = requests.post(
                    url_texto,
                    json={
                        "chat_id": chat_id,
                        "text": mensagem,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False
                    },
                    timeout=15
                )
                if resp.status_code == 200:
                    break
            except Exception:
                time.sleep(3)
        time.sleep(2)

    print("Envio concluído!")

if __name__ == "__main__":
    vistos = carregar_vistos()
    todos_candidatos = coletar_por_categorias()

    novos = [c for c in todos_candidatos if c["link"] not in vistos]
    print(f"Total de anúncios inéditos nas categorias: {len(novos)}")

    pre_aprovados = []
    for item in novos[:80]:
        vistos.add(item["link"])
        resultado = inspecionar_e_filtrar_descricao(item)
        if resultado:
            print(f"  [MATCH DESCRIÇÃO] {resultado['tipo']} | {resultado['titulo'][:45]} | R$ {resultado['preco']:.2f}")
            pre_aprovados.append(resultado)
        time.sleep(0.35)

    print(f"Total após filtro de descrição (GPU+Defeito ou Game Atual): {len(pre_aprovados)}")

    oportunidades = []
    for i in range(0, len(pre_aprovados), 10):
        lote = pre_aprovados[i:i+10]
        aprovados, sucesso = filtrar_com_ia(lote)
        if sucesso:
            oportunidades.extend(aprovados)

    print(f"Oportunidades aprovadas pela IA: {len(oportunidades)}")
    if oportunidades:
        enviar_telegram(oportunidades)

    salvar_vistos(vistos)
    print("Processo OLX finalizado!")
