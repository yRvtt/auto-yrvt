# MIR4 Bot

Manda teclas direto pra janela do MIR4 com `PostMessage` — **não tira o foco**,
pode usar o PC normalmente com o jogo atrás de outras janelas.

## Uso

1. Baixe `MIR4Bot.exe` na aba [Releases](../../releases).
2. Abra o jogo, depois o exe (ele pede admin — necessário pro Windows entregar as mensagens).
3. Ligue os toggles: **Acordar** (R → Tab → PageDown x3 → F), **Auto-Farm**, **Auto-Buff**.
4. Se quiser conferir se o jogo aceita, use **Testar tecla** com `1`.

Se o jogo não reagir, ele lê teclado por Raw Input e ignora mensagens postadas.

## Atualização

O app checa sozinho o último release ao abrir; clique na versão no canto pra checar na hora.
Se houver versão nova, ele baixa, troca o exe e reinicia.

## Publicar uma versão nova

1. Suba a versão em `APP_VERSION` (`mir4_bot.py`) e em `updater.py` não muda nada.
2. `git add -A && git commit -m "v1.0.1" && git tag v1.0.1 && git push --tags`
3. A Action `.github/workflows/release.yml` compila e anexa o exe no release.
4. Os usuários recebem no próximo abrir (ou clicando na versão).

Build local: `build.bat` → `dist\MIR4Bot.exe`.
