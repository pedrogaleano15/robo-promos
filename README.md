# Robô de promoções do Telegram

Escuta canais de promoção do Telegram **em tempo real** e te avisa no Telegram
quando aparece um produto da sua lista com preço **menor ou igual ao teto** que você definiu.

Canais já configurados (oficiais ou divulgados pelos próprios sites):

| Canal | Quem é |
|---|---|
| `pichauofertas` | Canal oficial da Pichau |
| `ofertaskabum` | Canal de ofertas do KaBuM! (confira em kabum.com.br/hotsite/telegram) |
| `pelandobr` | Pelando Promoções (ofertas avaliadas pela comunidade) |
| `cupons_desconto` | Promobit – Promoções e Cupons |

**Adicionar mais canais e grupos:** com `"monitorar_todos_que_sigo": true` (padrão),
o robô vigia **todos os canais e grupos que você segue no Telegram**. Para incluir um
canal novo, é só entrar nele pelo app e reiniciar o robô (Ctrl+C e `python promo_bot.py`).
Para tirar um canal que não tem nada a ver (notícias, jogos etc.), coloque parte do nome
dele em `"ignorar_canais"`, por exemplo `["noticias", "crunchyroll"]`.
Em `"canais"` você também pode listar canais que não segue, pelo @ (sem o @) ou pelo nome exato.

Ao ligar, o robô mostra a lista de todos os canais que está vigiando.

---

## 1. Instalar (uma vez)

Precisa de Python 3.9+.

```bash
pip install telethon
```

## 2. Pegar suas chaves do Telegram (uma vez)

1. Entre em https://my.telegram.org com seu número.
2. Clique em **API development tools** e crie um app (nome e descrição quaisquer).
3. Copie o **api_id** e o **api_hash**.
4. Renomeie `.env.example` para `.env` e cole os dois valores.

> ⚠️ Essas chaves dão acesso à sua conta do Telegram. Não mande para ninguém,
> não suba no GitHub (o `.gitignore` já ignora o `.env` e a sessão).

## 3. Receber os avisos com notificação (recomendado)

Sem isso, os avisos vão para as suas **Mensagens salvas**, que não tocam notificação.

1. No Telegram, fale com o **@BotFather** → `/newbot` → escolha nome e usuário.
2. Cole o token no `.env` em `BOT_TOKEN=`.
3. Abra o seu bot e mande `/start`.
4. Rode `python promo_bot.py --descobrir-chat` e cole o número em `CHAT_ID=`.

## 4. Rodar

```bash
python promo_bot.py
```

Na primeira vez ele pede seu telefone **com +55** (ex.: `+5567999999999`) e o código que o Telegram te manda
(é o login normal; fica salvo no arquivo `sessao_robo.session`).
Ao ligar, ele confere as últimas 40 mensagens de cada canal e depois fica escutando.

Para testar as regras sem conectar: `python promo_bot.py --testar`

## 5. Adicionar ou mudar produtos

Edite `lista_desejos.json` — **não precisa reiniciar**, o robô recarrega sozinho.

```json
{
  "nome": "Mouse Logitech G203",
  "ativo": true,
  "preco_maximo": 120,
  "termos": ["mouse", "g203"],
  "excluir": ["mousepad", "pad"]
}
```

- `termos`: **todas** precisam aparecer na oferta. Use `|` para alternativas:
  `"1tb|1 tb"` = "1tb" **ou** "1 tb". Acentos e maiúsculas são ignorados.
- `excluir`: se **qualquer uma** aparecer, a oferta é ignorada
  (ex.: `"5060 ti"` para não confundir com a 5060 comum).
- `preco_maximo`: avisa quando o preço for **menor ou igual** a este.
- `preco_minimo` (opcional): ignora valores muito baixos, como parcelas e cupons.
  Padrão: 25% do teto.
- `ativo: false` pausa o produto sem apagar.

Observação: a cadeira está com `"gamer"` em `excluir`, para pegar só modelos de escritório/ergonômicos.
Se quiser ser avisado de cadeiras gamer também, tire essa palavra.

## 6. Deixar rodando sempre

O robô só avisa enquanto estiver rodando.

- **Windows, ao ligar o PC:** crie um atalho para `pythonw promo_bot.py` na pasta
  `shell:startup` (Win+R → `shell:startup`).
- **Linux:** rode com `nohup python3 promo_bot.py &` ou crie um serviço `systemd`.
- **24h sem PC ligado:** uma VPS gratuita (ex.: Oracle Cloud Free Tier) ou um Raspberry Pi.

## Limitações

- Ele só lê o texto da mensagem. Se o preço estiver só na imagem, não detecta.
- Ofertas em mensagens com muitos itens são separadas por linha; formatos estranhos
  podem escapar ou gerar um aviso errado de vez em quando. Ajuste `termos`/`excluir`.
- Sempre confira no site da loja antes de comprar: preço, vendedor e estoque.

---

# Rodar 24h de graça no GitHub (sem PC ligado e sem tokens)

O arquivo `monitor_web.py` lê a **prévia pública** dos canais (`https://t.me/s/<canal>`),
então não precisa de login no Telegram: só do `BOT_TOKEN` e do `CHAT_ID`.
O GitHub roda ele a cada ~15 minutos de graça em repositório **público**.
Só funciona com canais **públicos** (que têm @) — liste-os em `"canais"` no `lista_desejos.json`.

1. Em github.com, clique em **New repository** → nome `robo-promos` → **Public** → Create.
2. **Add file → Upload files** e envie: `monitor_web.py`, `promo_bot.py`,
   `lista_desejos.json`, `README.md` e `.gitignore`.
   ⚠️ **Nunca** envie `.env`, `sessao_robo.session` nem `vistos.db`.
3. **Add file → Create new file**, nome `.github/workflows/promos.yml`,
   cole o conteúdo do arquivo `promos.yml` e clique em **Commit changes**.
4. **Settings → Secrets and variables → Actions → New repository secret**:
   - `BOT_TOKEN` = token do seu bot
   - `CHAT_ID` = seu ID (o robô no PC mostrou ao ligar: "CHAT_ID descoberto automaticamente")
5. **Settings → Actions → General → Workflow permissions** → marque
   **Read and write permissions** → Save.
6. Aba **Actions** → "Caçador de promoções" → **Run workflow**.
   A 1ª execução só marca o ponto de partida de cada canal (não avisa nada antigo).
   Depois, ele roda sozinho a cada ~15 min.

Para mudar produtos ou canais: edite `lista_desejos.json` direto no GitHub (ícone de lápis).
