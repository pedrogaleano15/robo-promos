"""
Monitor de promoções pela web (para rodar no GitHub Actions, sem PC ligado)
--------------------------------------------------------------------------
Lê a prévia pública dos canais do Telegram (https://t.me/s/<canal>), procura
os produtos de lista_desejos.json abaixo do teto e avisa pelo seu bot.
Não precisa de API_ID/API_HASH nem de login: só BOT_TOKEN e CHAT_ID.

Só funciona com canais PÚBLICOS (que têm @). Guarda o último post visto de
cada canal em estado.json para não repetir avisos.

Uso local (teste):  python monitor_web.py
"""

import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:  # usa os certificados do sistema (útil no Windows com antivírus)
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from promo_bot import (  # reaproveita as regras do robô
    ARQ_LISTA, Lista, analisar_mensagem, avisar_por_bot, carregar_env, checar_bot, montar_aviso,
)

PASTA = Path(__file__).resolve().parent
ARQ_ESTADO = PASTA / "estado.json"
AGENTE = "Mozilla/5.0 (compatible; robo-promos/1.0)"

RE_POST = re.compile(r'data-post="([^"/]+)/(\d+)"')
RE_TEXTO = re.compile(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)


def baixar(canal: str) -> str:
    req = urllib.request.Request(f"https://t.me/s/{canal}", headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def html_para_texto(trecho: str) -> str:
    trecho = re.sub(r"<br\s*/?>", "\n", trecho, flags=re.I)
    trecho = re.sub(r"<[^>]+>", "", trecho)
    return html.unescape(trecho).strip()


def extrair_posts(pagina: str):
    """Devolve [(id, texto)] dos posts da página, em ordem crescente de id."""
    posicoes = [(m.start(), m.group(1), int(m.group(2))) for m in RE_POST.finditer(pagina)]
    posts = []
    for i, (ini, _canal, pid) in enumerate(posicoes):
        fim = posicoes[i + 1][0] if i + 1 < len(posicoes) else len(pagina)
        bloco = pagina[ini:fim]
        m = RE_TEXTO.search(bloco)
        if m:
            posts.append((pid, html_para_texto(m.group(1))))
    return sorted(posts)


def carregar_estado():
    if ARQ_ESTADO.exists():
        return json.loads(ARQ_ESTADO.read_text(encoding="utf-8"))
    return {}


def main():
    carregar_env()
    if not os.environ.get("BOT_TOKEN") or not os.environ.get("CHAT_ID"):
        sys.exit("Faltam BOT_TOKEN e CHAT_ID (no .env ou nos Secrets do GitHub).")

    bot_ok = checar_bot()
    if bot_ok and os.environ.get("TESTE_AVISO") == "true":
        ok = avisar_por_bot("✅ Teste do GitHub: o Caçador de Promos está funcionando e vai te avisar por aqui!")
        print("Mensagem de teste enviada!" if ok else "FALHA ao enviar teste: confira o secret CHAT_ID")
    lista = Lista(ARQ_LISTA)
    estado = carregar_estado()
    avisos = 0
    falhas = []

    for canal in lista.canais:
        try:
            posts = extrair_posts(baixar(canal))
        except Exception as erro:  # noqa: BLE001
            print(f"[{canal}] erro ao abrir: {erro}")
            continue
        if not posts:
            print(f"[{canal}] nenhum post legível (canal privado ou sem prévia?)")
            continue

        ultimo = estado.get(canal)
        maior = max(pid for pid, _ in posts)
        if ultimo is None:
            # Primeira vez: só marca onde parou, para não disparar avisos antigos.
            estado[canal] = maior
            print(f"[{canal}] primeira leitura, marcando post {maior} como ponto de partida")
            continue

        novos = [(pid, txt) for pid, txt in posts if pid > ultimo]
        falhas_canal = []
        print(f"[{canal}] {len(novos)} posts novos")
        for pid, texto in novos:
            for produto, preco, trecho in analisar_mensagem(texto, lista.produtos):
                link = f"https://t.me/{canal}/{pid}"
                print(f"  ACHOU: {produto['nome']} por R$ {preco:.2f} ({link})")
                if bot_ok and avisar_por_bot(montar_aviso(produto, preco, trecho, canal, link)):
                    avisos += 1
                else:
                    falhas.append(link)
                    falhas_canal.append(pid)
        # Se algum aviso falhou, volta o marcador para tentar de novo na próxima execução.
        estado[canal] = (min(falhas_canal) - 1) if falhas_canal else max(ultimo, maior)

    estado["_atualizado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ARQ_ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Concluído. {avisos} aviso(s) enviado(s).")
    if falhas:
        print(f"ATENÇÃO: {len(falhas)} oferta(s) achada(s) mas NÃO enviada(s): " + " ".join(falhas))


if __name__ == "__main__":
    main()
