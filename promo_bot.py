"""
Robô de promoções do Telegram
-----------------------------
Fica escutando canais de promoção do Telegram em tempo real e te avisa
quando aparecer um produto da sua lista (lista_desejos.json) com preço
menor ou igual ao teto que você definiu.

Uso:
    python promo_bot.py                 # roda o robô
    python promo_bot.py --descobrir-chat  # mostra o chat_id do seu bot (opcional)
    python promo_bot.py --testar        # testa as regras da lista com mensagens de exemplo

Configuração: veja o README.md.
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Usa os certificados do próprio Windows/macOS (resolve o erro
# CERTIFICATE_VERIFY_FAILED quando um antivírus inspeciona o HTTPS).
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

PASTA = Path(__file__).resolve().parent
ARQ_LISTA = PASTA / "lista_desejos.json"
ARQ_ENV = PASTA / ".env"
ARQ_DB = PASTA / "vistos.db"


# ---------------------------------------------------------------- config
def carregar_env():
    """Lê o .env simples (CHAVE=valor) sem depender de bibliotecas extras."""
    if ARQ_ENV.exists():
        for linha in ARQ_ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


class Lista:
    """Lista de desejos que recarrega sozinha quando você edita o arquivo."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._mtime = None
        self.canais = []
        self.produtos = []
        self.recarregar(forcar=True)

    def recarregar(self, forcar=False):
        mtime = self.caminho.stat().st_mtime
        if not forcar and mtime == self._mtime:
            return False
        dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        self.canais = dados.get("canais", [])
        self.seguir_todos = bool(dados.get("monitorar_todos_que_sigo", False))
        self.ignorar_canais = dados.get("ignorar_canais", [])
        self.produtos = [p for p in dados.get("produtos", []) if p.get("ativo", True)]
        self._mtime = mtime
        print(f"[{agora()}] Lista carregada: {len(self.produtos)} produtos ativos, "
              f"{len(self.canais)} canais.")
        return True


# ---------------------------------------------------------------- texto
def normalizar(texto: str) -> str:
    """minúsculas, sem acentos, sem markdown e com espaços simples."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower().replace("*", "").replace("_", "").replace("`", "")
    return re.sub(r"[ \t]+", " ", texto)


# Aceita "R$ 1.034,90", "R$1034", "R$ 1034,90", "R$ 35". O formato com ponto de
# milhar vem primeiro e exige o ponto; o (?![0-9]) impede cortar "1034" em "103".
RE_PRECO = re.compile(
    r"r\$\s*((?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,[0-9]{1,2})?)(?![0-9.,]*[0-9])"
)


def extrair_precos(texto_norm: str):
    precos = []
    for bruto in RE_PRECO.findall(texto_norm):
        try:
            valor = float(bruto.replace(".", "").replace(",", "."))
        except ValueError:
            continue
        if valor >= 1:
            precos.append(valor)
    return precos


def termo_bate(termo: str, texto_norm: str) -> bool:
    """'a|b|c' = qualquer uma das alternativas aparece no texto."""
    return any(normalizar(alt).strip() in texto_norm for alt in termo.split("|") if alt.strip())


def produto_bate(produto: dict, trecho_norm: str) -> bool:
    if not all(termo_bate(t, trecho_norm) for t in produto.get("termos", [])):
        return False
    if any(termo_bate(t, trecho_norm) for t in produto.get("excluir", [])):
        return False
    return True


def dividir_em_ofertas(texto: str):
    """Mensagens com várias ofertas (resumos) são divididas por linha/bloco,
    para que o preço de um item não seja atribuído a outro."""
    blocos = [b.strip() for b in re.split(r"\n\s*\n|\n(?=\S*\s*[-•➡👉🔥])", texto) if b.strip()]
    return blocos or [texto]


def analisar_mensagem(texto: str, produtos: list):
    """Devolve lista de (produto, preco, trecho) que estão no teto."""
    achados = []
    texto_norm_inteiro = normalizar(texto)
    blocos = dividir_em_ofertas(texto)
    # Se só UM bloco da mensagem tem preço, é uma oferta única (ex.: preço numa
    # linha e nome do produto em outra): todos os blocos usam esse preço.
    blocos_com_preco = [b for b in blocos if extrair_precos(normalizar(b))]
    if len(blocos_com_preco) <= 1:
        blocos = [texto]
    for produto in produtos:
        teto = float(produto["preco_maximo"])
        for idx, bloco in enumerate(blocos):
            bloco_norm = normalizar(bloco)
            if not produto_bate(produto, bloco_norm):
                continue
            precos = extrair_precos(bloco_norm)
            # Preço numa linha separada ("Preço à vista R$ ..."): usa os blocos
            # vizinhos que só têm preço (não descrevem outro produto).
            if not precos:
                for viz in (idx - 1, idx + 1):
                    if 0 <= viz < len(blocos):
                        viz_norm = normalizar(blocos[viz])
                        if not any(produto_bate(p, viz_norm) for p in produtos):
                            precos += extrair_precos(viz_norm)
            # Se o bloco não tem preço e a mensagem é de um item só, usa a mensagem toda.
            if not precos and len(blocos) == 1:
                precos = extrair_precos(texto_norm_inteiro)
            if not precos:
                continue
            # Menor preço do bloco (ex.: à vista vs parcelado). Ignora valores
            # absurdamente baixos (parcelas, cupons de R$ 10 etc.).
            minimo_aceito = float(produto.get("preco_minimo", teto * 0.25))
            validos = [p for p in precos if p >= minimo_aceito]
            if not validos:
                continue
            preco = min(validos)
            if preco <= teto:
                achados.append((produto, preco, bloco))
                break
    return achados


# ---------------------------------------------------------------- avisos
def agora():
    return datetime.now().strftime("%d/%m %H:%M:%S")


def avisar_por_bot(texto: str) -> bool:
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not token or not chat_id:
        return False
    dados = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": texto[:4000],
        "disable_web_page_preview": "false",
    }).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", dados, timeout=15)
        return True
    except Exception as erro:  # noqa: BLE001
        print(f"[{agora()}] Falha ao avisar pelo bot: {erro}")
        return False


def reais(valor: float) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_aviso(produto, preco, trecho, canal, link_msg):
    teto = float(produto["preco_maximo"])
    return (
        f"🔥 PROMOÇÃO NA SUA LISTA\n\n"
        f"{produto['nome']}\n"
        f"💰 {reais(preco)} (seu teto: {reais(teto)}, {reais(teto - preco)} abaixo)\n"
        f"📢 Canal: {canal}\n\n"
        f"{trecho.strip()[:900]}\n\n"
        f"🔗 Mensagem original: {link_msg}"
    )


# ---------------------------------------------------------------- dedupe
class Vistos:
    def __init__(self, caminho: Path):
        self.con = sqlite3.connect(caminho)
        self.con.execute("CREATE TABLE IF NOT EXISTS vistos (chave TEXT PRIMARY KEY, quando TEXT)")

    def ja_viu(self, chave: str) -> bool:
        cur = self.con.execute("SELECT 1 FROM vistos WHERE chave=?", (chave,))
        if cur.fetchone():
            return True
        self.con.execute("INSERT INTO vistos VALUES (?, ?)", (chave, datetime.now().isoformat()))
        self.con.commit()
        return False


# ---------------------------------------------------------------- robô
async def rodar():
    from telethon import TelegramClient, events

    carregar_env()
    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    if not api_id or not api_hash:
        sys.exit("Faltam API_ID e API_HASH no arquivo .env (veja o README.md).")

    lista = Lista(ARQ_LISTA)
    vistos = Vistos(ARQ_DB)
    cliente = TelegramClient(str(PASTA / "sessao_robo"), int(api_id), api_hash)
    await cliente.start()  # na 1ª vez pede seu telefone e o código do Telegram

    # Num chat privado com o bot, o CHAT_ID é o próprio ID da sua conta.
    if os.environ.get("BOT_TOKEN") and not os.environ.get("CHAT_ID"):
        eu = await cliente.get_me()
        os.environ["CHAT_ID"] = str(eu.id)
        print(f"[{agora()}] CHAT_ID descoberto automaticamente: {eu.id} "
              f"(pode colar no .env, se quiser).")
    if os.environ.get("BOT_TOKEN"):
        if avisar_por_bot("✅ Robô de promoções ligado! Vou te avisar por aqui quando achar algo da sua lista."):
            print(f"[{agora()}] Mensagem de teste enviada pelo seu bot.")
        else:
            print(f"[{agora()}] Não consegui mandar pelo bot; confira o BOT_TOKEN e se você deu /start nele. "
                  f"Os avisos vão para as suas Mensagens salvas.")

    # Canais e grupos que você segue (para achar por nome e para o modo "todos")
    seguidos = {}
    async for dialogo in cliente.iter_dialogs():
        if dialogo.is_channel:  # canais e supergrupos
            seguidos[dialogo.id] = dialogo.entity

    escolhidos = {}
    for canal in lista.canais:
        ent = None
        try:
            ent = await cliente.get_entity(canal)
        except Exception:  # noqa: BLE001
            alvo = normalizar(canal).strip()
            for e in seguidos.values():
                if normalizar(getattr(e, "title", "")).strip() == alvo:
                    ent = e
                    break
        if ent is None:
            print(f"[{agora()}] Não achei o canal/grupo '{canal}'. Confira o @ ou o nome exato.")
            continue
        escolhidos[ent.id] = ent

    if lista.seguir_todos:
        ignorar = [normalizar(x).strip() for x in lista.ignorar_canais]
        for e in seguidos.values():
            titulo = normalizar(getattr(e, "title", ""))
            if any(i and i in titulo for i in ignorar):
                continue
            escolhidos.setdefault(e.id, e)

    entidades = list(escolhidos.values())
    if not entidades:
        sys.exit("Nenhum canal válido. Confira 'canais' em lista_desejos.json.")
    for e in entidades:
        print(f"    • {getattr(e, 'title', e.id)}")

    async def avisar(texto):
        if not avisar_por_bot(texto):
            await cliente.send_message("me", texto)  # "Mensagens salvas"

    async def processar(msg, canal_nome):
        lista.recarregar()
        texto = msg.raw_text or ""
        if not texto:
            return
        username = getattr(msg.chat, "username", None)
        link = f"https://t.me/{username}/{msg.id}" if username else canal_nome
        for produto, preco, trecho in analisar_mensagem(texto, lista.produtos):
            chave = f"{msg.chat_id}:{msg.id}:{produto['nome']}"
            if vistos.ja_viu(chave):
                continue
            print(f"[{agora()}] ACHOU: {produto['nome']} por R$ {preco:.2f} em {canal_nome}")
            await avisar(montar_aviso(produto, preco, trecho, canal_nome, link))

    # Ao iniciar, olha as últimas mensagens de cada canal (pega o que saiu enquanto estava desligado)
    for ent in entidades:
        nome = getattr(ent, "title", str(ent))
        async for msg in cliente.iter_messages(ent, limit=int(os.environ.get("OLHAR_ULTIMAS", "40"))):
            await processar(msg, nome)

    @cliente.on(events.NewMessage(chats=entidades))
    async def nova(evento):
        nome = getattr(evento.chat, "title", "canal")
        await processar(evento.message, nome)

    print(f"[{agora()}] Robô rodando. Monitorando {len(entidades)} canais. Ctrl+C para parar.")
    await cliente.run_until_disconnected()


async def listar_canais():
    """Lista os canais/grupos que você segue e separa os públicos (com @),
    que são os únicos que o GitHub consegue ler."""
    from telethon import TelegramClient

    carregar_env()
    cliente = TelegramClient(str(PASTA / "sessao_robo"),
                             int(os.environ["API_ID"]), os.environ["API_HASH"])
    await cliente.start()
    publicos, privados = [], []
    async for d in cliente.iter_dialogs():
        if not d.is_channel:
            continue
        user = getattr(d.entity, "username", None)
        if not user:
            usernames = getattr(d.entity, "usernames", None) or []
            ativos = [u.username for u in usernames if getattr(u, "active", False)]
            user = ativos[0] if ativos else None
        (publicos if user else privados).append((d.name, user))
    await cliente.disconnect()

    print("\n=== PÚBLICOS (o GitHub consegue ler) ===")
    for nome, user in publicos:
        print(f"  {user:<28} {nome}")
    print("\n=== PRIVADOS (só o robô do PC lê) ===")
    for nome, _ in privados:
        print(f"  {nome}")
    trecho = json.dumps([u for _, u in publicos], ensure_ascii=False, indent=2)
    (PASTA / "canais_publicos.txt").write_text(trecho, encoding="utf-8")
    print("\nLista salva em canais_publicos.txt")


def descobrir_chat():
    carregar_env()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("Coloque o BOT_TOKEN no .env primeiro.")
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15) as r:
        dados = json.load(r)
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
             for u in dados.get("result", []) if "message" in u}
    if not chats:
        print("Nenhuma mensagem encontrada. Mande um /start para o seu bot no Telegram e rode de novo.")
    for cid, nome in chats.items():
        print(f"CHAT_ID={cid}   ({nome})")


def testar():
    lista = Lista(ARQ_LISTA)
    exemplos = [
        "🔥 Preço à vista R$299,90 CADEIRA DE ESCRITÓRIO ERGONÔMICA MESH ENCOSTO ALTO APOIO LOMBAR Parcelado R$352,82",
        "🔥 Preço à vista R$2.499,99 PLACA DE VIDEO GIGABYTE GEFORCE RTX 5060 WINDFORCE OC 8GB Parcelado R$2.941,16",
        "🔥 Preço à vista R$2.999,99 PLACA DE VIDEO MSI GEFORCE RTX 5060 TI 8GB Parcelado R$3.529,40",
        "Monitor Gamer LG 24\" IPS 100Hz por R$ 279 👉 link",
        "PC Gamer Ryzen 5 7600 RTX 5060 16GB por R$ 4.999",
        "➡ Monitor AOC 27\" 144Hz ✅ R$ 659\n➡ Teclado Redragon Kumara ✅ R$ 129\n➡ Mouse Logitech ✅ R$ 99",
    ]
    for ex in exemplos:
        res = analisar_mensagem(ex, lista.produtos)
        alvo = ", ".join(f"{p['nome']} @ R$ {v:.2f}" for p, v, _ in res) or "nenhum aviso"
        print(f"- {ex[:70]!r}\n    -> {alvo}")


if __name__ == "__main__":
    if "--descobrir-chat" in sys.argv:
        descobrir_chat()
    elif "--listar-canais" in sys.argv:
        asyncio.run(listar_canais())
    elif "--testar" in sys.argv:
        testar()
    else:
        try:
            asyncio.run(rodar())
        except KeyboardInterrupt:
            print("\nRobô parado.")
