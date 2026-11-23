import html
import json
import os
import re
import time
import urllib.parse
import requests
from google import genai
from google.genai import types

ARQUIVO_VISTOS = "vistos_olx.json"

PRECO_MINIMO = 150.0
PRECO_MAXIMO = 4500.0

REGIOES_OLX = [
    "https://www.olx.com.br/estado-sp/sao-paulo-e-regiao/grande-abc",
    "https://www.olx.com.br/estado-sp/sao-paulo-e-regiao"
]

BUSCAS_NOTEBOOKS_DEFEITO = [
    "notebook rtx defeito",
    "notebook gtx defeito",
    "notebook gamer defeito",
    "notebook placa de video defeito",
    "notebook rtx tela quebrada",
    "notebook gtx tela quebrada",
    "notebook gamer tela quebrada",
    "notebook dedicado defeito",
    "notebook placa integrada defeito",
    "notebook radeon defeito"
]

BUSCAS_GAMES_ATUAIS = [
    "ps5",
    "playstation 5",
    "ps4",
    "playstation 4",
    "xbox series s",
    "xbox series x",
    "nintendo switch",
    "steam deck"
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
    vistos_lista = list(vistos)[-6000:]
    with open(ARQUIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(vistos_lista, f, indent=2)

def ler_via_jina(url, timeout=25):
    try:
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"Erro ao ler {url}: {e}")
    return ""

def extrair_anuncios_listagem(texto):
    resultados = []
    links_vistos = set()

    padrao_link = re.compile(r'\[([^\]]{10,160})\]\((https://[a-z0-9.-]*olx\.com\.br/[^\s\)]+-\d+)\)')
    matches = list(padrao_link.finditer(texto))

    for i, m in enumerate(matches):
        titulo_bruto = " ".join(m.group(1).split()).strip()
        link = m.group(2).split("?")[0].strip()

        if link in links_vistos or "/d/" not in link and not re.search(r'-\d{8,}$', link):
            continue

        inicio_bloco = m.start()
        fim_bloco = matches[i + 1].start() if i + 1 < len(matches) else min(len(texto), m.end() + 500)
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

        titulo_limpo = re.sub(r'^!\[.*?\]\(.*?\)\s*', '', titulo_bruto).strip()
        if len(titulo_limpo) < 6:
            slug = link.rstrip("/").split("/")[-1]
            titulo_limpo = re.sub(r'-\d+$', '', slug).replace("-", " ").title()

        if any(b in titulo_limpo.lower() for b in PALAVRAS_BLOQUEIO):
            continue

        m_local = re.search(r'((?:São Paulo|Santo André|São Bernardo|São Caetano|Diadema|Mauá|Ribeirão Pires|Guarulhos|Osasco|Barueri)[^\n|]{0,45})', bloco, flags=re.IGNORECASE)
        local = " ".join(m_local.group(1).split()) if m_local else "Grande SP / ABC"

        links_vistos.add(link)
        resultados.append({
            "titulo": titulo_limpo[:160],
            "preco": preco,
            "local": local[:80],
            "link": link
        })

    return resultados

def coletar_candidatos_olx():
    candidatos = []
    links_unicos = set()

    todas_buscas = [(kw, "NOTEBOOK COM DEFEITO") for kw in BUSCAS_NOTEBOOKS_DEFEITO] + [
        (kw, "GAME ATUAL") for kw in BUSCAS_GAMES_ATUAIS
    ]

    for regiao in REGIOES_OLX:
        for kw, categoria in todas_buscas:
            q = urllib.parse.quote(kw)
            url = f"{regiao}?q={q}&sf=1"
            print(f"[OLX - {categoria}] Buscando '{kw}' em {regiao}...")
            texto = ler_via_jina(url, timeout=22)
            if not texto:
                continue

            achados = extrair_anuncios_listagem(texto)
            novos = 0
            for item in achados:
                if item["link"] not in links_unicos:
                    item["categoria_busca"] = categoria
                    links_unicos.add(item["link"])
                    candidatos.append(item)
                    novos += 1
            print(f"  -> Novos encontrados: {novos}")
            time.sleep(0.5)

    return candidatos

def inspecionar_detalhes_anuncio(item):
    texto = ler_via_jina(item["link"], timeout=20)
    if not texto:
        return item

    linhas = [
        " ".join(l.split()) for l in texto.splitlines()
        if len(l.strip()) > 4 and not any(x in l.lower() for x in ["cookie", "dicas de segurança", "olx pay", "compartilhar", "denunciar"])
    ]
    item["descricao"] = " | ".join(linhas)[:1000]
    return item

def filtrar_com_ia(candidatos):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not candidatos:
        return [], False

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=35000)
    )

    modelos_disponiveis = []
    try:
        for m in client.models.list():
            nome = m.name.replace("models/", "")
            if "flash" in nome and "image" not in nome and "audio" not in nome and "tts" not in nome:
                modelos_disponiveis.append(nome)
    except Exception:
        pass

    if not modelos_disponiveis:
        modelos_disponiveis = ["gemini-2.5-flash"]

    lista_texto = ""
    for i, item in enumerate(candidatos):
        lista_texto += (
            f"ID {i} | Categoria: {item['categoria_busca']} | Título: {item['titulo']} | "
            f"Preço: R$ {item['preco']:.2f} | Local: {item['local']} | "
            f"Detalhes: {item.get('descricao', '')}\n"
        )

    prompt = f"""
Você é um especialista em compra e revenda de hardware e games usados no Brasil (2026), avaliando anúncios da OLX em São Paulo/ABC.

APROVE ESTRITAMENTE APENAS ESTES DOIS TIPOS DE ANÚNCIO:
1. NOTEBOOKS COM PLACA DE VÍDEO (DEDICADA GTX/RTX/RADEON OU INTEGRADA DE BOM DESEMPENHO COMO RYZEN VEGA / INTEL IRIS XE) QUE TENHAM ALGUM DEFEITO FÍSICO/REPARÁVEL (ex: tela quebrada, teclado com falha, bateria viciada, carcaça trincada, sem carregador, problema de software) E ESTEJAM BARATOS O SUFICIENTE PARA VALER O CONSERTO/REVENDA.
   - REJEITE notebooks funcionando perfeitamente a preço normal de mercado.
   - REJEITE notebooks básicos antigos (Celeron, Pentium, Dual Core, Positivo, Multilaser) ou placas-mãe totalmente queimadas sem conserto.
2. VÍDEO GAMES MAIS ATUAIS EM BOM ESTADO OU OPORTUNIDADE DE PREÇO: EXCLUSIVAMENTE PlayStation 5 (PS5), PlayStation 4 (PS4 Slim/Pro/Fat), Xbox Series S, Xbox Series X, Nintendo Switch (V1, V2, OLED, Lite) e Steam Deck.
   - REJEITE consoles antigos (PS3, PS2, Xbox 360, Xbox One, Wii), controles avulsos, jogos avulsos, caixas vazias ou acessórios isolados.

Anúncios coletados:
{lista_texto}

Responda APENAS com um array JSON válido (se nenhum atender rigorosamente aos critérios acima, retorne []):
[
  {{
    "id": 0,
    "motivo": "Tipo (Notebook c/ Defeito ou Console Atual) | Estado relatado | Média de mercado vs Preço anunciado e margem estimada"
  }}
]
"""
    for modelo in modelos_disponiveis[:4]:
        try:
            print(f"Analisando {len(candidatos)} anúncios da OLX com IA ({modelo})...")
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
        cat_esc = html.escape(item["categoria_busca"])
        motivo_esc = html.escape(item["motivo"])
        link_esc = html.escape(item["link"])
        preco_fmt = f"R$ {item['preco']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        mensagem = (
            f"🎯 <b>OLX SP/ABC - {cat_esc}</b>\n\n"
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
    todos_candidatos = coletar_candidatos_olx()

    novos = [c for c in todos_candidatos if c["link"] not in vistos]
    print(f"Total de anúncios inéditos para inspecionar: {len(novos)}")

    detalhados = []
    for item in novos[:50]:
        vistos.add(item["link"])
        detalhado = inspecionar_detalhes_anuncio(item)
        detalhados.append(detalhado)
        time.sleep(0.4)

    oportunidades = []
    for i in range(0, len(detalhados), 15):
        lote = detalhados[i:i+15]
        aprovados, sucesso = filtrar_com_ia(lote)
        if sucesso:
            oportunidades.extend(aprovados)

    print(f"Oportunidades aprovadas pela IA: {len(oportunidades)}")
    if oportunidades:
        enviar_telegram(oportunidades)

    salvar_vistos(vistos)
    print("Processo OLX finalizado!")
